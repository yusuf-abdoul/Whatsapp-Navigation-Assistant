"""Postgres test fixtures.

Connects to the dev Postgres (started via docker compose) and gives each test
a clean slate. Two layers:

1. Truncate corridor tables before every integration test (autouse). This
   isolates from any dev-seed data committed in the same database AND from
   tests that commit (corridor reply tests need durable data the orchestrator
   can read through its own session factory).
2. ``db`` fixture: a per-test rollback-wrapped session for tests that read or
   stage data without committing.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.corridors import db as db_module


@pytest.fixture(autouse=True)
async def _clean_corridor_tables() -> AsyncIterator[None]:
    """Truncate corridor tables and reset the orchestrator's engine singleton
    before each test. Keeps tests isolated from each other and from any data
    committed by earlier tests or dev-seed runs."""
    db_module._engine = None
    db_module._session_factory = None

    engine = create_async_engine(get_settings().database_url)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE segments, corridors, anchors, users RESTART IDENTITY CASCADE")
        )
    await engine.dispose()

    yield

    db_module._engine = None
    db_module._session_factory = None


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Per-test session bound to its own engine (function-scoped event loop)."""
    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            factory = async_sessionmaker(bind=conn, expire_on_commit=False)
            async with factory() as session:
                try:
                    yield session
                finally:
                    await trans.rollback()
    finally:
        await engine.dispose()
