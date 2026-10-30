"""Tests run in their own database, rebuilt each session, so they never touch demo data."""
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.config import settings

TEST_DATABASE = "festos_test_accomodation"
BACKEND = Path(__file__).resolve().parents[1]

_url = make_url(settings.database_url)
with psycopg.connect(host=_url.host, port=_url.port, user=_url.username, password=_url.password, dbname=_url.database, autocommit=True) as admin:
    if not admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (TEST_DATABASE,)).fetchone():
        admin.execute(f'CREATE DATABASE "{TEST_DATABASE}"')

# Must happen before anything imports app.database, which builds the engine from this value.
settings.database_url = _url.set(database=TEST_DATABASE).render_as_string(hide_password=False)


@pytest.fixture(scope="session", autouse=True)
def fresh_database():
    config = Config(BACKEND / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(config, "head")

    from scripts.seed import seed
    seed(reset=True)
