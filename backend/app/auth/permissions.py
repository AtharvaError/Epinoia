"""Role matrix. Seeded into roles, permissions and role_permissions."""

READ_ALL = {
    "dashboard.read", "events.read", "participants.read", "accommodation.read", "transport.read",
    "volunteers.read", "venues.read", "finance.read", "communication.read", "incidents.read",
    "analytics.read",
}

ALL = READ_ALL | {
    "participants.read_sensitive", "participants.write", "events.write", "accommodation.write",
    "transport.write", "volunteers.write", "venues.write", "finance.write", "finance.approve",
    "communication.write", "incidents.write", "simulation.run", "simulation.approve", "audit.read",
    "settings.manage", "festivals.manage",
}

_HEAD = {"dashboard.read", "events.read", "venues.read", "communication.read", "incidents.read", "simulation.run"}

ROLES: dict[str, tuple[str, set[str]]] = {
    "super_admin": ("Super Admin", ALL),
    "festival_director": ("Festival Director", ALL - {"festivals.manage"}),
    "event_coordinator": ("Event Coordinator", _HEAD | {
        "events.write", "venues.write", "participants.read", "volunteers.read", "analytics.read"}),
    "accommodation_head": ("Accommodation Head", _HEAD | {
        "participants.read", "participants.read_sensitive", "accommodation.read", "accommodation.write",
        "transport.read", "analytics.read"}),
    "transport_head": ("Transport Head", _HEAD | {
        "participants.read", "transport.read", "transport.write", "accommodation.read", "analytics.read"}),
    "volunteer_head": ("Volunteer Head", _HEAD | {"volunteers.read", "volunteers.write", "analytics.read"}),
    "finance_head": ("Finance Head", _HEAD | {"finance.read", "finance.write", "finance.approve", "analytics.read"}),
    "safety_coordinator": ("Safety Coordinator", _HEAD | {
        "participants.read", "participants.read_sensitive", "incidents.write", "audit.read"}),
    "volunteer": ("Volunteer", {"events.read", "venues.read", "communication.read", "incidents.read"}),
    "participant": ("Participant", {"events.read", "communication.read"}),
}
