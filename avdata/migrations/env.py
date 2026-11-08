"""Alembic environment. Migrations run as av_owner, which owns the tables.

The database URL comes from config.attributes["database_url"] when set (tests
use this to target av_test), otherwise from OWNER_DATABASE_URL.
"""

import logging
import os

from alembic import context
from sqlalchemy import create_engine, pool

logging.basicConfig(format="%(levelname)-5.5s [%(name)s] %(message)s")
logging.getLogger("alembic").setLevel(logging.INFO)

config = context.config


def database_url() -> str:
    url = config.attributes.get("database_url") or os.environ.get("OWNER_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "OWNER_DATABASE_URL is not set. Run: uv run --env-file .env alembic ..."
        )
    return url


def run_migrations() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Offline (--sql) mode is not supported.")
run_migrations()
