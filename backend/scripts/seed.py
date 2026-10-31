"""Seed roles and the Aarohan festival.

    python -m scripts.seed          load into an empty database (idempotent if already seeded)
    python -m scripts.seed --reset  wipe all data first (refused in production)
"""
import sys

from sqlalchemy import select, text

from app.auth.permissions import ALL, ROLES
from app.config import settings
from app.database import Base, SessionLocal
from app.models import Festival, Permission, Role
from app.services.housing import apply_sample_plan
from app.services.pack_import import ensure_pack_extensions, load_pack
from app.services.simulated_data import load_simulated


def seed_roles(db) -> None:
    existing_perms = {p.key: p for p in db.scalars(select(Permission))}
    for key in sorted(ALL):
        if key not in existing_perms:
            p = Permission(key=key)
            db.add(p)
            existing_perms[key] = p
    db.flush()

    existing_roles = {r.key: r for r in db.scalars(select(Role))}
    for key, (name, granted) in ROLES.items():
        if key in existing_roles:
            existing_roles[key].name = name
            existing_roles[key].permissions = [existing_perms[p] for p in sorted(granted)]
        else:
            db.add(Role(key=key, name=name, permissions=[existing_perms[p] for p in sorted(granted)]))
    db.flush()


def seed(reset: bool = False) -> None:
    if not settings.demo_password:
        raise SystemExit("Set DEMO_PASSWORD in .env first")
    with SessionLocal() as db:
        if reset:
            if settings.environment == "production":
                raise SystemExit("--reset is disabled in production")
            tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
            db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        existing = db.scalar(select(Festival).where(Festival.slug == "aarohan-2026"))
        seed_roles(db)
        if existing and not reset:
            ensure_pack_extensions(db, existing.id)
            db.commit()
            print("Seed complete (idempotent)")
            return
        festival = load_pack(db)
        load_simulated(db, festival.id)
        db.flush()
        apply_sample_plan(db, festival)
        db.commit()
    print("Seed complete")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
