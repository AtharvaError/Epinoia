from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import HTTPException
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")
PAGE_SIZE = 25


def paged(db: Session, query: Select, page: int, serialize: Callable[[Any], dict[str, Any]], size: int = PAGE_SIZE) -> dict[str, Any]:
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = db.scalars(query.limit(size).offset((page - 1) * size)).unique().all()
    return {"items": [serialize(r) for r in rows], "total": total, "page": page, "pages": max(1, -(-total // size)), "page_size": size}


def by_code(db: Session, model: type[T], festival_id: int, code: str) -> T:
    query = select(model).where(model.festival_id == festival_id, model.code == code)
    if hasattr(model, "deleted_at"):
        query = query.where(model.deleted_at.is_(None))
    row = db.scalar(query)
    if row is None:
        raise HTTPException(404, f"{model.__name__} {code} not found")
    return row


def like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def csv_safe(value: Any) -> str:
    """Stop a spreadsheet treating imported text as a formula. Keeps +91 phone numbers intact."""
    text = "" if value is None else str(value)
    risky = text[:1] in ("=", "@", "\t", "\r") or (text[:1] in ("+", "-") and not text[1:2].isdigit())
    return f"'{text}" if risky else text
