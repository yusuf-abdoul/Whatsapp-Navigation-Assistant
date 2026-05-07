"""WhatsApp OTP — short-lived 6-digit codes stored in Redis.

Each code is keyed by ``otp:{kind}:{wa_number}`` where ``kind`` is "signup"
or "login". A successful verify or three failures evicts the code; resends
are rate-limited to one every 30 seconds.

We pre-generate ALL random bits with ``secrets`` and compare with
``compare_digest`` to avoid timing leaks.
"""

from __future__ import annotations

import secrets

import redis.asyncio as aioredis
import structlog

from app.config import get_settings

log = structlog.get_logger("auth.otp")

OTP_TTL_SECONDS = 300
OTP_LENGTH = 6
MAX_ATTEMPTS = 3
RESEND_COOLDOWN_SECONDS = 30

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def _override_client_for_tests(client: aioredis.Redis | None) -> aioredis.Redis | None:
    """Used only by tests to swap in a fakeredis instance. Returns the previous one."""
    global _client
    prev = _client
    _client = client
    return prev


def _code_key(kind: str, wa_number: str) -> str:
    return f"otp:{kind}:{wa_number}"


def _attempts_key(kind: str, wa_number: str) -> str:
    return f"otp_attempts:{kind}:{wa_number}"


def _cooldown_key(kind: str, wa_number: str) -> str:
    return f"otp_cooldown:{kind}:{wa_number}"


class OTPRateLimited(Exception):
    """Raised when issue() is called within the resend-cooldown window."""


async def issue(wa_number: str, *, kind: str) -> str:
    """Generate, store, and return a fresh OTP. Resets attempt counter."""
    r = _redis()
    cooldown = _cooldown_key(kind, wa_number)
    if await r.exists(cooldown):
        raise OTPRateLimited(f"Wait at least {RESEND_COOLDOWN_SECONDS}s between code requests.")

    code = f"{secrets.randbelow(10**OTP_LENGTH):0{OTP_LENGTH}d}"
    await r.set(_code_key(kind, wa_number), code, ex=OTP_TTL_SECONDS)
    await r.delete(_attempts_key(kind, wa_number))
    await r.set(cooldown, "1", ex=RESEND_COOLDOWN_SECONDS)
    log.info("otp_issued", kind=kind, wa_number=wa_number)
    return code


async def verify(wa_number: str, *, kind: str, submitted: str) -> bool:
    """Return True iff the submitted code matches and is unexpired.

    A successful match clears the code (single-use). Three wrong tries
    also clears it (forces a fresh send).
    """
    r = _redis()
    stored = await r.get(_code_key(kind, wa_number))
    if stored is None:
        return False

    attempts = int(await r.incr(_attempts_key(kind, wa_number)))
    if attempts > MAX_ATTEMPTS:
        await r.delete(_code_key(kind, wa_number))
        await r.delete(_attempts_key(kind, wa_number))
        log.info("otp_too_many_attempts", kind=kind, wa_number=wa_number)
        return False

    if not secrets.compare_digest(stored, submitted.strip()):
        return False

    await r.delete(_code_key(kind, wa_number))
    await r.delete(_attempts_key(kind, wa_number))
    log.info("otp_verified", kind=kind, wa_number=wa_number)
    return True
