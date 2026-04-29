from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

from app.session import store
from app.session.state import Place, SessionState


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original = store._client
    store._client = client
    try:
        yield client
    finally:
        store._client = original
        await client.aclose()


async def test_put_then_get_roundtrip(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    state = SessionState(
        user_id="whatsapp:+234800",
        destination=Place(query="banex", lat=9.07, lon=7.48, display_name="Banex Plaza"),
    )
    await store.put(state)
    loaded = await store.get("whatsapp:+234800")
    assert loaded is not None
    assert loaded.destination is not None
    assert loaded.destination.display_name == "Banex Plaza"


async def test_get_returns_none_for_missing_user(
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    assert await store.get("whatsapp:+234999") is None


async def test_put_sets_ttl(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    state = SessionState(user_id="whatsapp:+234800")
    await store.put(state)
    ttl = await fake_redis.ttl("session:whatsapp:+234800")
    assert ttl > 0


async def test_delete_removes_session(fake_redis: fakeredis.aioredis.FakeRedis) -> None:
    state = SessionState(user_id="whatsapp:+234800")
    await store.put(state)
    await store.delete("whatsapp:+234800")
    assert await store.get("whatsapp:+234800") is None
