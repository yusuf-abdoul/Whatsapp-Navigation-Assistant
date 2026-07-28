"""Admin promotion CLI tests.

The CLI opens its own engine — that's the whole point of a runnable script —
so these tests bypass the per-test ``db`` fixture (which lives inside a
never-committed transaction) and talk to the real DB directly. The autouse
``_clean_corridor_tables`` fixture truncates ``users`` before every test,
so nothing bleeds across tests.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.users.admin_cli import _list_admins, _set_admin
from app.users.models import User


@pytest.fixture
async def real_db() -> AsyncIterator[AsyncSession]:
    """A session that actually commits to the underlying DB — required for
    the CLI's own engine to see the row on its next connection."""
    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _create_user(session: AsyncSession, wa_number: str, *, is_admin: bool = False) -> None:
    session.add(User(wa_number=wa_number, name="Test", is_admin=is_admin))
    await session.commit()


async def _get_is_admin(session: AsyncSession, wa_number: str) -> bool:
    row = (
        await session.execute(select(User).where(User.wa_number == wa_number))
    ).scalar_one()
    return row.is_admin


async def test_promote_flips_flag(real_db) -> None:
    await _create_user(real_db, "+2348100000101")

    msg = await _set_admin("+2348100000101", is_admin=True)
    assert "Promoted" in msg
    assert await _get_is_admin(real_db, "+2348100000101") is True


async def test_demote_flips_flag_back(real_db) -> None:
    await _create_user(real_db, "+2348100000102", is_admin=True)

    msg = await _set_admin("+2348100000102", is_admin=False)
    assert "Demoted" in msg
    assert await _get_is_admin(real_db, "+2348100000102") is False


async def test_set_admin_reports_when_user_missing() -> None:
    msg = await _set_admin("+2349999999999", is_admin=True)
    assert "No user" in msg


async def test_set_admin_noop_when_already_at_target(real_db) -> None:
    await _create_user(real_db, "+2348100000103", is_admin=False)

    msg = await _set_admin("+2348100000103", is_admin=False)
    assert "already" in msg
    assert "No change" in msg
    assert await _get_is_admin(real_db, "+2348100000103") is False


async def test_list_admins_returns_expected_rows(real_db) -> None:
    await _create_user(real_db, "+2348100000201", is_admin=True)
    await _create_user(real_db, "+2348100000202", is_admin=True)
    await _create_user(real_db, "+2348100000203", is_admin=False)

    listing = await _list_admins()
    assert "+2348100000201" in listing
    assert "+2348100000202" in listing
    assert "+2348100000203" not in listing


async def test_list_admins_when_none() -> None:
    listing = await _list_admins()
    assert listing == "No admins."
