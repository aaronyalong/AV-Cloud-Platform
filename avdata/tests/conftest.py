"""Shared fixtures for avdata tests, which run against the av_test database.

Run with: uv run --env-file .env pytest
"""

import os
from collections.abc import Callable
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_DB = "av_test"
PASSWORD_VARS = {
    "postgres": "POSTGRES_PASSWORD",
    "av_owner": "AV_OWNER_PASSWORD",
    "av_app": "AV_APP_PASSWORD",
}


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.exit(f"{name} is not set. Run: uv run --env-file .env pytest", 2)
    return value


def _password(role: str) -> str:
    return _require_env(PASSWORD_VARS[role])


@pytest.fixture(scope="session")
def pg_conninfo() -> Callable[[str], str]:
    """Returns a function giving the libpq connection string for a role."""

    def conninfo(role: str) -> str:
        return psycopg.conninfo.make_conninfo(
            host="127.0.0.1",
            port=os.environ.get("POSTGRES_PORT", "5432"),
            dbname=TEST_DB,
            user=role,
            password=_password(role),
        )

    return conninfo


@pytest.fixture(scope="session")
def pg_url() -> Callable[[str], str]:
    """Returns a function giving the SQLAlchemy URL for a role."""

    def url(role: str) -> str:
        port = os.environ.get("POSTGRES_PORT", "5432")
        return (
            f"postgresql+psycopg://{role}:{_password(role)}@127.0.0.1:{port}/{TEST_DB}"
        )

    return url


@pytest.fixture(scope="session")
def migrated_db(pg_url: Callable[[str], str]) -> None:
    """Rebuild av_test from an empty schema to the latest migration, as av_owner."""
    cfg = Config(
        toml_file=str(REPO_ROOT / "pyproject.toml"),
        attributes={"database_url": pg_url("av_owner")},
    )
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def mongo_url() -> str:
    return _require_env("MONGO_URL")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
