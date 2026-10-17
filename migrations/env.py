"""Alembic environment: hand-written migrations only (no autogenerate), run online in one transaction each."""

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

url = context.config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]

if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported: run them against a database.")

# A plain engine without the request time limits: a migration may run longer than one API call.
with create_engine(url, poolclass=NullPool).connect() as connection:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()
