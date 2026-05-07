"""Users repository tests."""

import uuid

from app.users import repository as users_repo


async def test_create_and_lookup_by_wa_number(db) -> None:
    user = await users_repo.create(db, wa_number="+2348100000001", name="Test User")
    assert user.id is not None
    assert user.is_admin is False

    found = await users_repo.get_by_wa_number(db, "+2348100000001")
    assert found is not None
    assert found.id == user.id


async def test_get_by_id_roundtrip(db) -> None:
    user = await users_repo.create(db, wa_number="+2348100000002", name=None)
    found = await users_repo.get_by_id(db, user.id)
    assert found is not None
    assert found.wa_number == "+2348100000002"


async def test_get_by_wa_number_returns_none_for_unknown(db) -> None:
    assert await users_repo.get_by_wa_number(db, "+2348100009999") is None


async def test_get_by_id_returns_none_for_unknown(db) -> None:
    assert await users_repo.get_by_id(db, uuid.uuid4()) is None
