"""The allocator's hard rules, on small inputs where the right answer is obvious."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.allocator import Beds, Person, nights, plan_allocation

START = datetime(2026, 10, 30, tzinfo=timezone.utc)


def person(code, gender="Male", access="None", age=20, contingent="C1", stay=2):
    return Person(int(code[1:]), code, contingent, gender, access, age, START, START + timedelta(days=stay))


def campus(code, gender="boys", free=4, accessible=False, floor=1, hostel="B1"):
    return Beds(int(code[1:]), code, hostel, "on_campus", floor, gender, accessible, free)


def external(code="X100", free=10, cost=1000):
    return Beds(int(code[1:]), code, "Stay", "external", None, None, None, free, Decimal(cost), True)


def rooms_for(plan):
    return {a["code"]: a["room"] for a in plan.assignments}


def test_blank_gender_is_held_and_never_guessed():
    plan = plan_allocation([person("P1", gender=None)], [campus("R1"), external()], None)
    assert plan.assignments == []
    assert "blank" in plan.held[0]["reason"]


def test_non_binary_is_never_placed_in_a_gendered_room():
    for gender in ("Non-binary", "Prefer not to say"):
        plan = plan_allocation([person("P1", gender=gender)], [campus("R1"), campus("R2", "girls"), external()], None)
        assert rooms_for(plan) == {"P1": "X100"}


def test_men_and_women_only_get_matching_rooms():
    plan = plan_allocation([person("P1", "Male"), person("P2", "Female")], [campus("R1", "boys"), campus("R2", "girls")], None)
    assert rooms_for(plan) == {"P1": "R1", "P2": "R2"}


def test_wheelchair_user_gets_the_accessible_bed_first():
    people = [person("P1", access="Near washroom"), person("P2", access="Wheelchair access")]
    plan = plan_allocation(people, [campus("R1", free=1, accessible=True)], None)
    assert rooms_for(plan) == {"P2": "R1"}
    assert plan.held[0]["code"] == "P1"


def test_accessibility_need_is_never_moved_to_an_unverified_bed():
    plan = plan_allocation([person("P1", access="Wheelchair access")], [campus("R1"), external()], None)
    assert plan.assignments == []
    assert "accessible" in plan.held[0]["reason"]


def test_ground_floor_need_accepts_a_ground_floor_room():
    plan = plan_allocation([person("P1", access="Ground floor / no stairs")], [campus("R1", floor=0)], None)
    assert rooms_for(plan) == {"P1": "R1"}


def test_capacity_is_never_exceeded():
    plan = plan_allocation([person(f"P{i}") for i in range(1, 6)], [campus("R1", free=3)], None)
    assert len(plan.assignments) == 3
    assert len(plan.held) == 2


def test_budget_limit_is_respected_and_reported():
    people = [person(f"P{i}", "Non-binary", stay=2) for i in range(1, 4)]
    plan = plan_allocation(people, [external(cost=1000)], Decimal(4000))
    assert len(plan.assignments) == 2
    assert plan.external_cost == 4000
    assert plan.held[0]["reason"] == "External budget limit reached"


def test_cheapest_external_stay_is_used_first():
    plan = plan_allocation([person("P1", "Non-binary")], [external("X1", cost=1800), external("X2", cost=950)], None)
    assert rooms_for(plan) == {"P1": "X2"}


def test_external_without_transport_is_not_used():
    room = external()
    room.transport = False
    assert plan_allocation([person("P1", "Non-binary")], [room], None).assignments == []


def test_minors_get_campus_beds_first_and_off_campus_minors_are_marked():
    people = [person("P1", age=20), person("P2", age=17), person("P3", age=17)]
    plan = plan_allocation(people, [campus("R1", free=1), external()], None)
    assert rooms_for(plan)["P2"] == "R1"
    relaxed = {a["code"]: a["relaxed"] for a in plan.assignments}
    assert "minor_off_campus" in relaxed["P3"]
    assert "minor_off_campus" not in relaxed["P1"]


def test_contingent_stays_in_one_block_when_it_fits():
    people = [person("P1", contingent="A"), person("P2", contingent="A"), person("P3", contingent="B")]
    rooms = [campus("R1", free=2, hostel="B1"), campus("R2", free=2, hostel="B2")]
    plan = plan_allocation(people, rooms, None)
    assert plan.splits == {}
    assert rooms_for(plan)["P1"] == rooms_for(plan)["P2"]


def test_split_contingent_is_reported():
    people = [person(f"P{i}", contingent="A") for i in range(1, 4)]
    plan = plan_allocation(people, [campus("R1", free=2, hostel="B1"), campus("R2", free=1, hostel="B2")], None)
    assert plan.splits == {"A (boys)": 2}


def test_reversed_dates_have_no_nights():
    assert nights(person("P1", stay=-1)) is None
    assert nights(person("P1", stay=2)) == 2
