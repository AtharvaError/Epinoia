from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(db: Session, user: User, festival_id: int, action: str, entity: str, entity_id: Any, summary: str,
           before: dict[str, Any] | None = None, after: dict[str, Any] | None = None, reason: str | None = None) -> None:
    """Add an audit row to the caller's transaction, so it commits or rolls back with the change."""
    db.add(AuditLog(festival_id=festival_id, actor_id=user.id, actor_label=user.name, action=action, entity=entity,
                    entity_id=str(entity_id), summary=summary, before=before, after=after, reason=reason))
