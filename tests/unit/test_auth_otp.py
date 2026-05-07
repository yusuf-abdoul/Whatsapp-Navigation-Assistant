"""OTP issue/verify tests using fakeredis."""

from collections.abc import AsyncIterator

import fakeredis.aioredis
import pytest

from app.auth import otp


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prev = otp._override_client_for_tests(client)
    try:
        yield client
    finally:
        otp._override_client_for_tests(prev)
        await client.aclose()


WA = "+2348123456789"


async def test_issue_returns_a_six_digit_code(fake_redis) -> None:
    code = await otp.issue(WA, kind="signup")
    assert len(code) == otp.OTP_LENGTH
    assert code.isdigit()


async def test_verify_accepts_valid_code(fake_redis) -> None:
    code = await otp.issue(WA, kind="signup")
    assert await otp.verify(WA, kind="signup", submitted=code) is True


async def test_verify_is_single_use(fake_redis) -> None:
    code = await otp.issue(WA, kind="signup")
    assert await otp.verify(WA, kind="signup", submitted=code) is True
    assert await otp.verify(WA, kind="signup", submitted=code) is False


async def test_verify_rejects_wrong_code(fake_redis) -> None:
    await otp.issue(WA, kind="signup")
    assert await otp.verify(WA, kind="signup", submitted="000000") is False


async def test_verify_clears_after_max_attempts(fake_redis) -> None:
    code = await otp.issue(WA, kind="signup")
    for _ in range(otp.MAX_ATTEMPTS):
        await otp.verify(WA, kind="signup", submitted="000000")
    # Even the correct code should now fail because the slot was cleared.
    assert await otp.verify(WA, kind="signup", submitted=code) is False


async def test_issue_is_rate_limited_within_cooldown(fake_redis) -> None:
    await otp.issue(WA, kind="signup")
    with pytest.raises(otp.OTPRateLimited):
        await otp.issue(WA, kind="signup")


async def test_signup_and_login_codes_are_independent(fake_redis) -> None:
    signup_code = await otp.issue(WA, kind="signup")
    login_code = await otp.issue(WA, kind="login")
    # Different keyspaces — both codes should verify independently.
    assert await otp.verify(WA, kind="signup", submitted=signup_code) is True
    assert await otp.verify(WA, kind="login", submitted=login_code) is True


async def test_codes_for_different_numbers_are_independent(fake_redis) -> None:
    code_a = await otp.issue("+2348111111111", kind="signup")
    code_b = await otp.issue("+2348222222222", kind="signup")
    # Distinct keyspaces, so each code verifies independently regardless of the other.
    assert await otp.verify("+2348111111111", kind="signup", submitted=code_a) is True
    assert await otp.verify("+2348222222222", kind="signup", submitted=code_b) is True
