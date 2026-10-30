from alembic import context

import app.models  # noqa: F401  (registers tables on Base.metadata)
from app.database import Base, engine


with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()
