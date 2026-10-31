"""Role matrix. Seeded into roles, permissions and role_permissions."""

READ_ALL = {
    "dashboard.read", "events.read", "participants.read", "accommodation.read", "transport.read",
    "volunteers.read", "venues.read", "finance.read", "communication.read", "incidents.read",
    "analytics.read", "vendors.read", "equipment.read", "merch.read",
}

ALL = READ_ALL | {
    "participants.read_sensitive", "participants.write", "participants.checkin", "incidents.report", "events.write", "accommodation.write",
    "transport.write", "volunteers.write", "venues.write", "finance.write", "finance.approve",
    "communication.write", "incidents.write", "simulation.run", "simulation.approve", "audit.read",
    "settings.manage", "festivals.manage", "vendors.write", "vendors.approve",
    "equipment.request", "equipment.approve", "equipment.receive",
    "merch.issue", "merch.manage", "merch.override",
}

_HEAD = {
    "dashboard.read", "events.read", "venues.read", "vendors.read", "communication.read", "communication.write",
    "incidents.read", "incidents.report", "simulation.run", "equipment.read", "merch.read", "transport.read",
}

ROLES: dict[str, tuple[str, set[str]]] = {
    "super_admin": ("Super Admin", ALL),
    "festival_director": ("Festival Director", ALL - {"festivals.manage"}),
    "event_coordinator": ("Event Coordinator", _HEAD | {
        "events.write", "venues.write", "participants.read", "participants.write", "participants.checkin",
        "volunteers.read", "analytics.read", "equipment.request", "equipment.receive", "merch.issue"}),
    "accommodation_head": ("Accommodation Head", _HEAD | {
        "participants.read", "participants.read_sensitive", "accommodation.read", "accommodation.write",
        "vendors.write", "analytics.read"}),
    "transport_head": ("Transport Head", _HEAD | {
        "participants.read", "transport.write", "accommodation.read", "vendors.write", "analytics.read",
        "equipment.request", "equipment.approve", "equipment.receive", "merch.issue", "merch.manage"}),
    "volunteer_head": ("Volunteer Head", _HEAD | {"volunteers.read", "volunteers.write", "analytics.read"}),
    "finance_head": ("Finance Head", _HEAD | {"finance.read", "finance.write", "finance.approve", "vendors.write", "analytics.read"}),
    "safety_coordinator": ("Safety Coordinator", _HEAD | {
        "participants.read", "participants.read_sensitive", "incidents.write", "vendors.write", "vendors.approve", "audit.read"}),
    "volunteer": ("Volunteer", {"events.read", "venues.read", "communication.read", "incidents.read", "incidents.report",
                                "participants.checkin", "transport.read", "equipment.read", "equipment.receive",
                                "merch.read", "merch.issue"}),
    "participant": ("Participant", {"events.read", "communication.read"}),
}
