from collections.abc import Collection

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Notification, Role, User

MODULE_ROLES = {
    "participants": {"event_coordinator"}, "events": {"event_coordinator"}, "venues": {"event_coordinator"},
    "volunteers": {"volunteer_head"}, "transportation": {"transport_head"}, "accommodation": {"accommodation_head"},
    "finance": {"finance_head"},
}


def notify_roles(db: Session, festival_id: int, roles: Collection[str], title: str, body: str, link: str, exclude_user: int | None = None) -> int:
    """Send an in-app notification to everyone holding these roles on this festival."""
    users = db.scalars(select(User).join(Role, Role.id == User.role_id).where(User.festival_id == festival_id, User.is_active.is_(True), Role.key.in_(roles)))
    notes = [Notification(festival_id=festival_id, user_id=u.id, title=title, body=body, link=link) for u in users if u.id != exclude_user]
    db.add_all(notes)
    return len(notes)


def roles_for_modules(modules: Collection[str]) -> set[str]:
    return {"festival_director"}.union(*(MODULE_ROLES.get(m, set()) for m in modules))
