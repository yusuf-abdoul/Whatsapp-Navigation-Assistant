"""Postgres test fixtures.

Connects to the dev Postgres (started via docker compose) and wraps each test
in a rolled-back transaction so schema state is shared but data is not.
"""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """Per-test session, rolled back at the end so tests don't leak data.

    Engine is created per-test to avoid conflicts with pytest-asyncio's
    function-scoped event loop. Connection pooling keeps the cost negligible.
    """
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
