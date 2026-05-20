"""Test-suite-wide setup: route every test against a dedicated test database.

Critical: this module runs before any ``app.*`` module is imported, so the
``DATABASE_URL`` env var we set here is what ``app.config.get_settings()``
picks up on first access. Without this, tests would share the dev DB and the
autouse truncate fixture in ``tests/integration/conftest.py`` would wipe real
data (contributor submissions, approved corridors, anything live-tested).

The test DB (``wna_test``) is created on first session start and migrated to
the head Alembic revision. Subsequent test runs reuse it.
"""

from __future__ import annotations

import os
from pathlib import Path

# MUST be set before any `from app...` import. pydantic-settings reads env on
# first instantiation and `get_settings()` caches the result via lru_cache.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://wna:wna_dev@localhost:5432/wna_test",
)

import psycopg
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TEST_DB_NAME = "wna_test"
_ADMIN_DSN = "dbname=postgres user=wna password=wna_dev host=localhost port=5432"


def _ensure_test_database_exists() -> None:
    """Create the ``wna_test`` database if it doesn't already exist.

    Connects to the maintenance ``postgres`` database with autocommit so the
    CREATE DATABASE statement isn't trapped in a transaction. Silently passes
    if the DB is already there.
    """
    try:
        with psycopg.connect(_ADMIN_DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB_NAME,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{_TEST_DB_NAME}" OWNER wna')
    except psycopg.OperationalError as e:
        raise RuntimeError(
            f"can't reach Postgres to bootstrap test DB: {e}. "
            "Is `docker compose up -d postgres` running?"
        ) from e


def _apply_migrations() -> None:
    """Bring the test DB up to ``head`` via Alembic.

    Alembic reads the URL from ``app.config.get_settings().database_url`` —
    since we set ``DATABASE_URL`` above, it migrates ``wna_test``, never ``wna``.
    """
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_test_db() -> None:
    """Session-scoped autouse: ensure test DB exists + is migrated before any test runs."""
    _ensure_test_database_exists()
    _apply_migrations()
