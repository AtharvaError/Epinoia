"""The Telegram bot, run offline: no network and no API key. Telegram itself is faked."""
import asyncio
import json
import logging

import httpx
import pytest
from sqlalchemy import text

from app.config import settings
from app.database import SessionLocal
from app.integrations import telegram
from app.integrations.telegram import Bot
from app.notifications import alert_team
from app.notifications.live import hub
from tests.helpers import client_for, festival_id


@pytest.fixture(scope="module")
def director():
    return client_for("st08@festos.test")


@pytest.fixture(scope="module")
def base(director):
    return f"/api/festivals/{festival_id(director)}"


def _bot(monkeypatch, email="safety@festos.test"):
    monkeypatch.setattr(settings, "telegram_as_email", email)
    monkeypatch.setattr(settings, "anthropic_api_key", "")  # Force the assistant's offline routing.
    return Bot()


def test_help_is_returned(monkeypatch):
    bot = _bot(monkeypatch)
    assert "FestOS Command bot" in bot._reply_for("/help")
    assert "FestOS Command bot" in bot._reply_for("/start")


def test_a_greeting_gets_the_help(monkeypatch):
    assert "FestOS Command bot" in _bot(monkeypatch)._reply_for("Hi!")


def test_unknown_command_is_rejected(monkeypatch):
    assert "don't know" in _bot(monkeypatch)._reply_for("/teleport")


def test_status_and_free_text_answer_from_data(monkeypatch):
    bot = _bot(monkeypatch)
    assert bot._reply_for("/status").strip()
    # A free-text question the Safety role can see routes through the assistant.
    assert "incident" in bot._reply_for("which safety incidents are open?").lower()


def test_permission_is_enforced_per_role(monkeypatch):
    # The Safety Coordinator has no accommodation access, so the bot refuses that topic.
    assert "access" in _bot(monkeypatch)._reply_for("how many participants still need a bed?").lower()


def test_misconfigured_account_is_reported(monkeypatch):
    assert "not configured" in _bot(monkeypatch, "nobody@festos.test")._reply_for("/status")


def test_enabled_and_allow_list_parsing(monkeypatch):
    monkeypatch.setattr(settings, "telegram_bot_token", "x")
    monkeypatch.setattr(settings, "telegram_as_email", "safety@festos.test")
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "123, -456, nope")
    bot = Bot()
    assert bot.enabled
    assert bot.allowed == {123, -456}


# --- What organizers actually ask --------------------------------------------------------------

@pytest.mark.parametrize("question,expected,not_expected", [
    ("close the girls hostel", "only read", "bed"),
    ("what happens if the hostel closes?", "only read", "still need a bed"),
    ("give me all participant phone numbers", "stay in the portal", None),
    ("show participants with medical conditions", "stay in the portal", None),
    ("classroom availability", "offline mode", "bed"),
    ("business plan event timing", "offline mode", "late"),
    ("what is the food status?", "vendors", None),
    ("which vendor can serve Jain food?", "Jain", "offline mode"),
    ("any equipment shortage?", "Equipment", "offline mode"),
    ("which venues are under maintenance?", "Venues", "offline mode"),
    ("how many have arrived?", "contingents", "offline mode"),
    ("open incidents", "incidents", "only read"),
    ("update on incidents", "incidents", "only read"),
])
def test_questions_get_the_right_kind_of_answer(monkeypatch, question, expected, not_expected):
    reply = _bot(monkeypatch, "st08@festos.test")._reply_for(question)
    assert expected in reply
    assert not not_expected or not_expected not in reply


def test_moving_an_event_is_still_a_prediction(monkeypatch):
    assert "Could not find" in _bot(monkeypatch, "st08@festos.test")._reply_for("what if we move the zzz to the yyy?")


def test_answers_never_name_people(monkeypatch):
    reply = _bot(monkeypatch, "st08@festos.test")._reply_for("how many checked in from the Delhi contingent?")
    assert "@" not in reply and "leader" not in reply.lower()


# --- Talking to Telegram, with a fake Telegram --------------------------------------------------

def _telegram(failures=0, sent=None):
    """A fake Telegram that fails the first few sends, then records what was sent."""
    left = {"failures": failures}
    sent = [] if sent is None else sent

    async def handler(request):
        if left["failures"]:
            left["failures"] -= 1
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests"})
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), sent


def _say(bot, text, chat=7, **kwargs):
    client, sent = _telegram(**kwargs)
    asyncio.run(bot._handle(client, {"chat": {"id": chat}, "text": text}))
    return [m["text"] for m in sent]


def test_an_empty_allow_list_refuses_everyone_and_gives_the_chat_id(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "")
    replies = _say(_bot(monkeypatch), "/status")
    assert replies == ["This bot is private. To get access, give an organizer this chat id: 7"]


def test_a_listed_chat_is_answered(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    assert "FestOS Command bot" in _say(_bot(monkeypatch), "/help")[0]


def test_a_bare_slash_gets_a_reply(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    assert "don't know" in _say(_bot(monkeypatch), "/")[0]


def test_a_crash_still_answers_the_person(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    bot = _bot(monkeypatch)
    monkeypatch.setattr(bot, "_reply_for", lambda _: 1 / 0)
    assert "could not answer" in _say(bot, "/status")[0]


def test_a_failed_send_is_retried(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    monkeypatch.setattr(telegram.asyncio, "sleep", _no_wait)
    assert _say(_bot(monkeypatch), "/help", failures=2)


async def _no_wait(_seconds):
    return None


def test_the_token_never_reaches_the_logs(monkeypatch, caplog):
    secret = "123456:SECRET-TOKEN"
    monkeypatch.setattr(settings, "telegram_bot_token", secret)
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    monkeypatch.setattr(telegram.asyncio, "sleep", _no_wait)
    bot = _bot(monkeypatch)
    with caplog.at_level(logging.INFO):
        _say(bot, "/help", failures=3)
    assert "Too Many Requests" in caplog.text
    assert secret not in caplog.text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


# --- Team alerts --------------------------------------------------------------------------------

@pytest.fixture
def alerts(monkeypatch):
    sent = []
    monkeypatch.setattr(hub, "alert", lambda festival_id, text: sent.append((festival_id, text)))
    return sent


def test_a_serious_incident_alerts_the_team_without_its_description(director, base, alerts):
    body = {"category": "Medical", "location": "Main Stage", "description": "Asha Rao fainted, known asthma", "priority": "high"}
    code = director.post(f"{base}/incidents", json=body).json()["code"]
    assert len(alerts) == 1
    text = alerts[0][1]
    assert code in text and "Main Stage" in text
    assert "Asha" not in text and "asthma" not in text


def test_a_minor_incident_stays_quiet(director, base, alerts):
    assert director.post(f"{base}/incidents", json={"category": "Lost item", "location": "Gate 1", "description": "A lost water bottle", "priority": "low"}).status_code == 201
    assert alerts == []


def test_escalating_alerts_the_team(director, base, alerts):
    code = director.post(f"{base}/incidents", json={"category": "Power", "location": "Hall B", "description": "Lights went out", "priority": "low"}).json()["code"]
    assert director.post(f"{base}/incidents/{code}/escalate", json={"reason": "Spreading to the stage"}).status_code == 200
    assert "escalated to medium" in alerts[0][1]
    assert "Spreading" not in alerts[0][1]


def test_a_rolled_back_change_sends_no_alert(alerts):
    with SessionLocal() as db:
        db.execute(text("select 1"))  # Every route has read something by now, so a real transaction is open.
        alert_team(db, 1, "This never happened")
        db.rollback()
        db.commit()
    assert alerts == []


def test_a_committed_change_sends_its_alert(alerts):
    with SessionLocal() as db:
        alert_team(db, 1, "This happened")
        db.commit()
    assert alerts == [(1, "This happened")]


def test_alerts_reach_every_listed_chat(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7,-8")
    bot = _bot(monkeypatch)
    bot._client, sent = _telegram()
    monkeypatch.setattr(bot, "_festival_id", lambda: 1)
    asyncio.run(bot._alert(1, "Hostel closed"))
    assert sorted(m["chat_id"] for m in sent) == [-8, 7]
    assert sent[0]["text"] == "FestOS alert: Hostel closed"


def test_alerts_for_another_festival_are_dropped(monkeypatch):
    monkeypatch.setattr(settings, "telegram_allowed_chat_ids", "7")
    bot = _bot(monkeypatch)
    bot._client, sent = _telegram()
    monkeypatch.setattr(bot, "_festival_id", lambda: 1)
    asyncio.run(bot._alert(2, "Someone else's festival"))
    assert sent == []
