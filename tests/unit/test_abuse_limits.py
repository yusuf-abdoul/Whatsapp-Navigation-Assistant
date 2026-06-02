"""Abuse-control tests.

Covers the hourly + daily rate limits and the identical-query cooldown.
Uses fakeredis so we don't depend on a live Redis instance.
"""

from collections.abc import AsyncIterator
from unittest.mock import MagicMock, patch

import fakeredis.aioredis
import pytest
from redis.exceptions import RedisError

from app.abuse import limits


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prev = limits._override_client_for_tests(client)
    try:
        yield client
    finally:
        limits._override_client_for_tests(prev)
        await client.aclose()


def _settings(**overrides):
    """Build a MagicMock that mimics the parts of Settings we touch."""
    defaults = dict(
        redis_url="redis://localhost:6379/0",
        rate_limit_per_hour=3,
        rate_limit_per_day=5,
        identical_query_cooldown_seconds=5,
    )
    defaults.update(overrides)
    return MagicMock(**defaults)


WA = "whatsapp:+2348100000001"


async def test_under_limit_returns_true(fake_redis) -> None:
    with patch("app.abuse.limits.get_settings", return_value=_settings()):
        for _ in range(3):
            assert await limits.check_and_record(WA) is True


async def test_hourly_limit_blocks_fourth_request(fake_redis) -> None:
    with patch("app.abuse.limits.get_settings", return_value=_settings(rate_limit_per_hour=3)):
        for _ in range(3):
            assert await limits.check_and_record(WA) is True
        # 4th request in the same hour is blocked.
        assert await limits.check_and_record(WA) is False


async def test_daily_limit_blocks_when_hourly_is_high(fake_redis) -> None:
    with patch(
        "app.abuse.limits.get_settings",
        return_value=_settings(rate_limit_per_hour=100, rate_limit_per_day=2),
    ):
        assert await limits.check_and_record(WA) is True
        assert await limits.check_and_record(WA) is True
        assert await limits.check_and_record(WA) is False


async def test_limits_are_per_number(fake_redis) -> None:
    """Two different users have independent counters."""
    with patch("app.abuse.limits.get_settings", return_value=_settings(rate_limit_per_hour=1)):
        assert await limits.check_and_record("whatsapp:+2348100000001") is True
        # Different number — first request, allowed.
        assert await limits.check_and_record("whatsapp:+2348100000002") is True
        # First number again — second request, blocked.
        assert await limits.check_and_record("whatsapp:+2348100000001") is False


async def test_redis_error_fails_open(fake_redis) -> None:
    """If Redis is unreachable, we serve the user rather than reject them."""
    broken = MagicMock()
    broken.incr = MagicMock(side_effect=RedisError("connection refused"))
    prev = limits._override_client_for_tests(broken)
    try:
        with patch("app.abuse.limits.get_settings", return_value=_settings()):
            assert await limits.check_and_record(WA) is True
    finally:
        limits._override_client_for_tests(prev)


async def test_duplicate_within_cooldown_is_flagged(fake_redis) -> None:
    with patch("app.abuse.limits.get_settings", return_value=_settings()):
        assert await limits.is_duplicate(WA, "How do I get to Banex") is False
        # Same text again within cooldown — duplicate.
        assert await limits.is_duplicate(WA, "How do I get to Banex") is True


async def test_different_text_is_not_duplicate(fake_redis) -> None:
    with patch("app.abuse.limits.get_settings", return_value=_settings()):
        assert await limits.is_duplicate(WA, "How do I get to Banex") is False
        # A different message from the same number — fresh.
        assert await limits.is_duplicate(WA, "How do I get to Jabi") is False


async def test_same_text_from_different_number_is_not_duplicate(fake_redis) -> None:
    """Cooldown is scoped per number — two users can ask the same thing back-to-back."""
    with patch("app.abuse.limits.get_settings", return_value=_settings()):
        assert await limits.is_duplicate("whatsapp:+2348100000001", "How do I get to X") is False
        assert await limits.is_duplicate("whatsapp:+2348100000002", "How do I get to X") is False


async def test_cooldown_disabled_when_set_to_zero(fake_redis) -> None:
    with patch(
        "app.abuse.limits.get_settings",
        return_value=_settings(identical_query_cooldown_seconds=0),
    ):
        assert await limits.is_duplicate(WA, "hi") is False
        # Cooldown disabled — repeat is not a duplicate.
        assert await limits.is_duplicate(WA, "hi") is False


async def test_empty_text_is_never_duplicate(fake_redis) -> None:
    with patch("app.abuse.limits.get_settings", return_value=_settings()):
        assert await limits.is_duplicate(WA, "") is False
        assert await limits.is_duplicate(WA, "") is False
