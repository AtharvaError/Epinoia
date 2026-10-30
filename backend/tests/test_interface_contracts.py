"""What the interface relies on: decisions that open a valid simulation, sorting, the room map."""
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit

import pytest

from tests.helpers import client_for, festival_id


@pytest.fixture(scope="module")
def director():
    return client_for("st08@festos.test")


@pytest.fixture(scope="module")
def base(director):
    return f"/api/festivals/{festival_id(director)}"


def actions(client, base):
    return [a["action"] for a in client.get(f"{base}/dashboard").json()["alerts"] if a["action"]]


def test_each_prefilled_decision_is_accepted_by_ripple(director, base):
    ripple = [dict(parse_qsl(urlsplit(a["to"]).query)) for a in actions(director, base) if a["to"].startswith("/ripple?scenario=")]
    runnable = 0
    for fields in ripple:
        scenario = fields.pop("scenario")
        if "minutes" in fields:
            fields["minutes"] = int(fields["minutes"])
        if scenario == "venue_change" and "venue_code" not in fields:
            continue  # A person picks the venue.
        if "start_at" in fields:  # The form opens on the event's current time, for a person to change.
            start, end = datetime.fromisoformat(fields["start_at"]), datetime.fromisoformat(fields["end_at"])
            fields |= {"start_at": f"{start + timedelta(hours=1):%Y-%m-%dT%H:%M}:00+05:30", "end_at": f"{end + timedelta(hours=1):%Y-%m-%dT%H:%M}:00+05:30"}
        response = director.post(f"{base}/simulations", json={"scenario": scenario, "change": fields})
        assert response.status_code == 201, response.text
        runnable += 1
    assert runnable


def test_capacity_decision_suggests_a_venue_that_seats_everyone(director, base):
    alert = next(a for a in director.get(f"{base}/dashboard").json()["alerts"] if "exceed venue capacity" in a["title"])
    fields = dict(parse_qsl(urlsplit(alert["action"]["to"]).query))
    if "venue_code" not in fields:
        pytest.skip("No suitable venue is free for the worst event in this seed")
    event = director.get(f"{base}/events/{fields['event_code']}").json()
    venue = next(v for v in director.get(f"{base}/venues").json() if v["code"] == fields["venue_code"])
    assert venue["capacity"] >= event["registered"]
    assert venue["code"] != event["venue"]["code"]


def test_every_action_names_a_real_permission_and_page(director, base):
    pages = ("/ripple", "/safety", "/events", "/accommodation", "/volunteers", "/finance", "/participants")
    for action in actions(director, base):
        assert action["permission"] in director.get("/api/auth/me").json()["permissions"]
        assert urlsplit(action["to"]).path in pages


def test_sorting_reaches_the_rows_beyond_the_first_page(director, base):
    first = director.get(f"{base}/participants?sort=-name").json()
    names = [p["full_name"] for p in first["items"]]
    assert names == sorted(names, key=str.casefold, reverse=True) or names[0] > names[-1]
    busiest = director.get(f"{base}/participants?sort=-events").json()["items"]
    assert [p["event_count"] for p in busiest] == sorted((p["event_count"] for p in busiest), reverse=True)
    assert busiest[0]["event_count"] >= first["items"][0]["event_count"]


def test_events_sort_by_a_joined_column_while_filtered_by_it(director, base):
    venue = next(e["venue"]["code"] for e in director.get(f"{base}/events").json()["items"] if e["venue"])
    response = director.get(f"{base}/events?sort=venue&venue={venue}")
    assert response.status_code == 200
    assert {e["venue"]["code"] for e in response.json()["items"]} == {venue}
    registered = [e["registered"] for e in director.get(f"{base}/events?sort=-registered").json()["items"]]
    assert registered == sorted(registered, reverse=True)


def test_an_unknown_sort_keeps_the_default_order(director, base):
    assert director.get(f"{base}/participants?sort=password_hash").json() == director.get(f"{base}/participants").json()


@pytest.mark.parametrize("path, key", [
    ("accommodation/rooms", "free"), ("accommodation/allocations", "room"), ("accommodation/unallocated", "college"), ("volunteers", "shifts"),
    ("finance/expenses", "estimated"), ("incidents", "priority"), ("transport/routes", "departs"), ("resources", "available"),
])
def test_each_sortable_list_accepts_its_keys_both_ways(director, base, path, key):
    for sort in (key, f"-{key}"):
        assert director.get(f"{base}/{path}?sort={sort}").status_code == 200


def test_room_map_matches_the_summary(director, base):
    hostels = director.get(f"{base}/accommodation/room-map").json()
    summary = director.get(f"{base}/accommodation/summary").json()
    assert sum(r["occupied"] for h in hostels for r in h["rooms"]) == sum(h["occupied"] for h in summary["hostels"])
    assert {h["code"] for h in hostels} == {h["code"] for h in summary["hostels"]}


def test_volunteers_check_in_but_cannot_read_the_room_map(base):
    volunteer = client_for("st07@festos.test")
    assert volunteer.get(f"{base}/accommodation/room-map").status_code == 403
    assert volunteer.post(f"{base}/passes/verify", json={"token": "not-a-real-pass"}).json() == {"valid": False}
