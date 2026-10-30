"""Seed roles and the Aarohan festival.

    python -m scripts.seed          load into an empty database
    python -m scripts.seed --reset  wipe all data first (refused in production)
"""
import sys

from sqlalchemy import func, select, text

from app.auth.permissions import ALL, ROLES
from app.config import settings
from app.database import Base, SessionLocal
from app.models import Festival, Permission, Role
from app.services.housing import apply_sample_plan
from app.services.pack_import import load_pack
from app.services.simulated_data import load_simulated


def seed_roles(db) -> None:
    permissions = {key: Permission(key=key) for key in sorted(ALL)}
    db.add_all(permissions.values())
    for key, (name, granted) in ROLES.items():
        db.add(Role(key=key, name=name, permissions=[permissions[p] for p in sorted(granted)]))
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
        elif db.scalar(select(func.count()).select_from(Festival)):
            raise SystemExit("Database already has data. Use --reset to replace it.")
        seed_roles(db)
        festival = load_pack(db)
        load_simulated(db, festival.id)
        db.flush()
        apply_sample_plan(db, festival)
        db.commit()
    print("Seed complete")


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
