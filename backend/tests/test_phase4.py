"""Finance, communication, safety, analytics, reports, live updates and the assistant."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from starlette.websockets import WebSocketDisconnect

from app.assistant import agent
from app.assistant.tools import TOOLS, Context, execute, tools_for
from app.auth.deps import permissions_of
from app.database import SessionLocal
from app.main import app
from app.models import Festival, User
from tests.helpers import client_for, festival_id


@pytest.fixture(scope="module")
def director():
    return client_for("st08@festos.test")


@pytest.fixture(scope="module")
def finance():
    return client_for("finance@festos.test")


@pytest.fixture(scope="module")
def safety():
    return client_for("safety@festos.test")


@pytest.fixture(scope="module")
def base(director):
    return f"/api/festivals/{festival_id(director)}"


def scalar(sql, **params):
    with SessionLocal() as db:
        return db.execute(text(sql), params).scalar_one()


# --- Finance --------------------------------------------------------------------------------

def test_summary_matches_the_database_and_counts_external_stays(finance, base):
    summary = finance.get(f"{base}/finance/summary").json()
    assert float(summary["totals"]["approved"]) == float(scalar("SELECT sum(approved_amount) FROM budgets"))
    hospitality = next(x for x in summary["lines"] if x["department"] == "Hospitality")
    assert float(hospitality["external_stays"]) > 0
    assert float(hospitality["committed"]) >= float(hospitality["external_stays"])


def test_finance_needs_its_permissions(base):
    coordinator = client_for("st03@festos.test")
    assert coordinator.get(f"{base}/finance/summary").status_code == 403
    assert coordinator.post(f"{base}/finance/expenses", json={"department": "Events", "category": "Prizes", "description": "Medals", "estimated_amount": 1}).status_code == 403


def test_expense_lifecycle(finance, base):
    created = finance.post(f"{base}/finance/expenses", json={"department": "Finance", "category": "Test", "description": "Lifecycle expense", "estimated_amount": 100}).json()
    path = f"{base}/finance/expenses/{created['id']}"
    assert finance.post(f"{path}/pay", json={"actual_amount": 100}).status_code == 409, "Unapproved expenses cannot be paid"
    assert finance.post(f"{path}/approve", json={"reason": "Routine payment"}).status_code == 200
    assert finance.post(f"{path}/approve", json={}).status_code == 409
    assert finance.post(f"{path}/pay", json={"actual_amount": 95}).status_code == 200
    assert finance.post(f"{path}/pay", json={"actual_amount": 95}).status_code == 409
    assert finance.request("DELETE", path, json={"reason": "Trying to remove"}).status_code == 409, "Paid expenses stay on record"


def test_over_budget_approval_needs_a_reason(finance, base):
    created = finance.post(f"{base}/finance/expenses", json={"department": "Registration", "category": "Test", "description": "Huge expense", "estimated_amount": 90000000}).json()
    path = f"{base}/finance/expenses/{created['id']}/approve"
    refused = finance.post(path, json={})
    assert refused.status_code == 409 and "over budget" in refused.json()["detail"]
    assert finance.post(path, json={"reason": "Director cleared this overrun"}).status_code == 200
    with SessionLocal() as db:
        db.execute(text("UPDATE expenses SET approval_status='rejected' WHERE description='Huge expense'"))
        db.commit()


def test_budget_change_is_audited_with_its_reason(finance, base):
    assert finance.put(f"{base}/finance/budgets/Finance", json={"approved_amount": 41000, "reason": "Payment fees rose"}).status_code == 200
    assert scalar("SELECT reason FROM audit_logs WHERE action='budget.update' ORDER BY id DESC LIMIT 1") == "Payment fees rose"


# --- Communication --------------------------------------------------------------------------

def test_announcement_reaches_only_its_audience(director, base):
    response = director.post(f"{base}/announcements", json={"title": "Finance sync", "body": "Meet at 5.", "audience_type": "role", "audience_ref": "finance_head"})
    assert response.status_code == 201
    assert response.json()["recipients"] == 1
    finance = client_for("finance@festos.test")
    assert any(n["title"] == "Finance sync" for n in finance.get(f"{base}/notifications").json()["items"])
    other = client_for("safety@festos.test")
    assert not any(n["title"] == "Finance sync" for n in other.get(f"{base}/notifications").json()["items"])


def test_read_and_acknowledge_are_counted(director, base):
    finance = client_for("finance@festos.test")
    note = next(n for n in finance.get(f"{base}/notifications").json()["items"] if n["title"] == "Finance sync")
    assert finance.post(f"{base}/notifications/{note['id']}/acknowledge").status_code == 204
    item = next(a for a in director.get(f"{base}/announcements").json()["items"] if a["title"] == "Finance sync")
    assert (item["recipients"], item["read"], item["acknowledged"]) == (1, 1, 1)
    assert director.post(f"{base}/notifications/{note['id']}/acknowledge").status_code == 404, "Nobody else can acknowledge it"


def test_volunteers_cannot_broadcast(base):
    client = client_for("st07@festos.test")
    assert client.post(f"{base}/announcements", json={"title": "Hello there", "body": "Everyone"}).status_code == 403


def test_bad_audience_is_rejected(director, base):
    assert director.post(f"{base}/announcements", json={"title": "Wrong", "body": "Wrong role", "audience_type": "role", "audience_ref": "nobody"}).status_code == 422


# --- Safety ---------------------------------------------------------------------------------

def test_any_volunteer_can_report_and_responders_are_told(base, safety):
    reporter = client_for("st07@festos.test")
    response = reporter.post(f"{base}/incidents", json={"category": "Medical", "location": "Main Stage", "description": "Performer fainted on stage", "priority": "critical"})
    assert response.status_code == 201
    assert any("Critical incident" in n["title"] for n in safety.get(f"{base}/notifications").json()["items"])
    assert reporter.post(f"{base}/incidents/{response.json()['code']}/status", json={"status": "acknowledged"}).status_code == 403


def test_incident_workflow_cannot_skip_steps(safety, base):
    code = safety.post(f"{base}/incidents", json={"category": "Power", "location": "LT-1", "description": "Lights failed in the hall"}).json()["code"]
    path = f"{base}/incidents/{code}"
    assert safety.post(f"{path}/status", json={"status": "resolved"}).status_code == 409
    assert safety.post(f"{path}/status", json={"status": "acknowledged"}).status_code == 200
    assert safety.post(f"{path}/status", json={"status": "assigned"}).status_code == 422, "A team must be named"
    assert safety.post(f"{path}/status", json={"status": "assigned", "assigned_team": "Logistics"}).status_code == 200
    assert safety.post(f"{path}/status", json={"status": "in_progress"}).status_code == 200
    assert safety.post(f"{path}/status", json={"status": "resolved"}).status_code == 422, "Resolution notes are required"
    assert safety.post(f"{path}/status", json={"status": "resolved", "resolution_notes": "Breaker replaced"}).status_code == 200
    assert safety.post(f"{path}/status", json={"status": "closed"}).status_code == 200
    detail = safety.get(path).json()
    assert len(detail["history"]) == 6 and detail["next_statuses"] == []


def test_escalation_steps_up_and_stops_at_critical(safety, base):
    code = safety.post(f"{base}/incidents", json={"category": "Crowd", "location": "Gate", "description": "Queue is growing fast", "priority": "high"}).json()["code"]
    assert safety.post(f"{base}/incidents/{code}/escalate", json={"reason": "Crush risk reported"}).json()["priority"] == "critical"
    assert safety.post(f"{base}/incidents/{code}/escalate", json={"reason": "Trying again now"}).status_code == 409


# --- Analytics and reports ------------------------------------------------------------------

def test_analytics_only_returns_sections_the_role_can_read(director, base):
    full = director.get(f"{base}/analytics").json()
    assert {"events", "finance", "accommodation", "transport", "incidents", "simulations"} <= set(full["sections"])
    limited = client_for("finance@festos.test").get(f"{base}/analytics").json()
    assert "finance" in limited["sections"] and "accommodation" not in limited["sections"]
    assert "accommodation" not in [r["key"] for r in limited["reports"]]


def test_analytics_filters_narrow_results(director, base):
    assert len(director.get(f"{base}/analytics").json()["sections"]["events"]) == 31
    assert len(director.get(f"{base}/analytics", params={"event": "E01"}).json()["sections"]["events"]) == 1
    day = director.get(f"{base}/analytics", params={"day": "2026-10-31"}).json()["sections"]["events"]
    assert 0 < len(day) < 31
    assert all(e["starts"].startswith("31 Oct") for e in day)


@pytest.mark.parametrize("name", ["events", "accommodation", "volunteers", "transport", "finance", "incidents", "simulations"])
def test_every_report_exports_as_csv_and_a_real_pdf(director, base, name):
    data = director.get(f"{base}/reports/{name}.csv")
    assert data.status_code == 200 and data.headers["content-type"].startswith("text/csv") and data.text.count("\n") >= 1
    pdf = director.get(f"{base}/reports/{name}.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" and len(pdf.content) > 800


def test_report_permissions_and_unknown_reports(base):
    client = client_for("finance@festos.test")
    assert client.get(f"{base}/reports/accommodation.csv").status_code == 403
    assert client.get(f"{base}/reports/nothing.csv").status_code == 404
    assert client.get(f"{base}/reports/finance.exe").status_code == 404


def test_pdf_handles_markup_in_data(director, base):
    assert director.post(f"{base}/incidents", json={"category": "<b>Odd</b> & <unclosed", "location": "A & B", "description": "Text with markup in it"}).status_code == 201
    assert director.get(f"{base}/reports/incidents.pdf").status_code == 200


# --- Live updates ---------------------------------------------------------------------------

def test_websocket_rejects_strangers_and_tells_members(base):
    fid = int(base.rsplit("/", 1)[1])
    with TestClient(app) as anonymous:
        with pytest.raises(WebSocketDisconnect):
            with anonymous.websocket_connect(f"/api/ws/{fid}"):
                pass
    with TestClient(app) as member:
        assert member.post("/api/auth/login", json={"email": "safety@festos.test", "password": member_password()}).status_code == 200
        with member.websocket_connect(f"/api/ws/{fid}") as socket:
            assert member.post(f"/api/festivals/{fid}/incidents", json={"category": "Test", "location": "Here", "description": "Live update check"}).status_code == 201
            assert socket.receive_json() == {"type": "changed"}


def member_password():
    from app.config import settings
    return settings.demo_password


# --- Assistant ------------------------------------------------------------------------------

def context_for(email):
    db = SessionLocal()
    user = db.query(User).filter_by(email=email).one()
    festival = db.query(Festival).first()
    return Context(db, festival, permissions_of(user))


def text_block(value):
    return SimpleNamespace(type="text", text=value)


def tool_call(name, args, id_="call_1"):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=args)


class FakeClient:
    """Plays back Claude's turns and records what was sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return self.turns.pop(0)


def turn(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


def test_no_tool_can_write():
    assert all(t.permission.endswith((".read", ".run")) for t in TOOLS)


def test_tools_are_filtered_by_role():
    names = {t.name for t in tools_for(context_for("finance@festos.test").permissions)}
    assert "budget_status" in names and "accommodation_status" not in names


def test_a_hidden_tool_still_cannot_run():
    ctx = context_for("finance@festos.test")
    assert "does not have access" in execute(ctx, "accommodation_status", {})["error"]


def test_tool_results_contain_no_participant_contact_details():
    ctx = context_for("admin@festos.test")
    phone = scalar("SELECT phone FROM participants ORDER BY id LIMIT 1")
    for tool in TOOLS:
        args = {"query": "a", "day": "2026-10-31", "route_code": None, "event": "Chess", "venue": "LT-1", "diet": None}
        assert phone not in str(execute(ctx, tool.name, {k: args[k] for k in tool.properties}))


def test_claude_loop_runs_tools_and_returns_the_answer():
    ctx = context_for("admin@festos.test")
    client = FakeClient(turn("tool_use", tool_call("accommodation_status", {})), turn("end_turn", text_block("1,074 people need beds.")))
    result = agent.ask_claude(client, ctx, "How many have no bed?", [])
    assert result == {"answer": "1,074 people need beds.", "mode": "claude", "tools": ["Accommodation"]}
    sent = client.requests[1]["messages"][-1]["content"][0]
    assert sent["type"] == "tool_result" and sent["tool_use_id"] == "call_1" and "participants_without_a_bed" in sent["content"]


def test_claude_request_uses_the_expected_settings():
    client = FakeClient(turn("end_turn", text_block("Hello.")))
    agent.ask_claude(client, context_for("finance@festos.test"), "Hi", [{"role": "user", "text": "Earlier"}, {"role": "assistant", "text": "Yes"}])
    request = client.requests[0]
    assert request["model"] == "claude-opus-5-5" and request["fallbacks"] == "default"
    assert [t["name"] for t in request["tools"]] == [t.name for t in tools_for(context_for("finance@festos.test").permissions)]
    assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in request["tools"])
    assert request["messages"][0] == {"role": "user", "content": "Earlier"} and len(request["messages"]) == 3
    assert "Today is" not in request["system"][0]["text"], "A date in the system prompt would break caching"


def test_a_tool_the_role_lacks_is_refused_even_if_requested():
    ctx = context_for("finance@festos.test")
    client = FakeClient(turn("tool_use", tool_call("accommodation_status", {})), turn("end_turn", text_block("I can't see that.")))
    agent.ask_claude(client, ctx, "Beds?", [])
    sent = client.requests[1]["messages"][-1]["content"][0]
    assert sent["is_error"] is True and "participants_without_a_bed" not in sent["content"]


def test_refusal_and_runaway_loops_end_cleanly():
    ctx = context_for("admin@festos.test")
    assert "can't help" in agent.ask_claude(FakeClient(turn("refusal")), ctx, "x y", [])["answer"]
    endless = FakeClient(*[turn("tool_use", tool_call("pending_tasks", {})) for _ in range(agent.MAX_TOOL_ROUNDS)])
    assert "more lookups" in agent.ask_claude(endless, ctx, "Loop", [])["answer"]


@pytest.mark.parametrize("question,expected", [
    ("How many participants have not received accommodation?", "need a bed"),
    ("Which events have venue conflicts?", "double-bookings"),
    ("Which volunteers are available tomorrow?", "no shift"),
    ("Which events could be affected by a transport delay?", "late"),
    ("What is our remaining approved budget?", "remaining"),
    ("What happens if we move Chess to LT-1?", "Prediction"),
    ("Which departments have pending tasks?", "Pending tasks"),
])
def test_offline_mode_answers_the_spec_questions(base, director, question, expected):
    response = director.post(f"{base}/assistant", json={"question": question})
    assert response.status_code == 200 and response.json()["mode"] == "offline"
    assert expected in response.json()["answer"]


def test_offline_mode_respects_permissions(base):
    finance = client_for("finance@festos.test")
    assert "does not have access" in finance.post(f"{base}/assistant", json={"question": "How many beds are free?"}).json()["answer"]


def test_offline_mode_says_when_it_cannot_answer(base, director):
    assert "offline mode" in director.post(f"{base}/assistant", json={"question": "Write me a poem"}).json()["answer"]


def test_asking_changes_nothing_and_never_logs_the_question(base, director):
    allocations = scalar("SELECT count(*) FROM accommodation_allocations")
    director.post(f"{base}/assistant", json={"question": "How many beds? My phone is 99999-12345"})
    assert scalar("SELECT count(*) FROM accommodation_allocations") == allocations
    assert scalar("SELECT count(*) FROM audit_logs WHERE summary LIKE '%99999%' OR after::text LIKE '%99999%'") == 0


def test_api_failure_falls_back_to_offline(monkeypatch):
    import anthropic
    import httpx

    class Broken:
        beta = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: (_ for _ in ()).throw(anthropic.APIConnectionError(request=httpx.Request("POST", "https://example.test")))))

    monkeypatch.setattr(agent, "get_client", lambda: Broken())
    result = agent.answer(context_for("admin@festos.test"), "How many beds are free?", [])
    assert result["mode"] == "offline" and "could not be reached" in result["note"] and "need a bed" in result["answer"]
