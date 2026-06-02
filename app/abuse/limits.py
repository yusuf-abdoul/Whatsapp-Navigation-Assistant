"""Per-WhatsApp-number abuse controls.

Three layered checks, each cheap (one or two Redis ops):

1. **Hourly cap** (default 30/hour per ``rate_limit_per_hour``)
2. **Daily cap** (default 100/day per ``rate_limit_per_day``)
3. **Identical-query cooldown** (default 5s per ``identical_query_cooldown_seconds``)
   — debounces accidental re-taps in WhatsApp where a user fat-fingers send.

Windows are tumbling, not sliding — simpler and fine at MVP scale. When a
window expires, the count resets. The hourly/daily counters key on the
user's WhatsApp number; the cooldown also keys on a hash of the text so a
genuinely different message doesn't get debounced.

Fail-open policy: any Redis error logs a warning and returns "allowed."
We'd rather serve traffic than reject paying customers because Redis
hiccupped. Real abuse will still hit the limit on the next successful op.
"""

from __future__ import annotations

import hashlib

import redis.asyncio as aioredis
import structlog

from app.config import get_settings

log = structlog.get_logger("abuse")

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def _override_client_for_tests(client: aioredis.Redis | None) -> aioredis.Redis | None:
    """Swap in fakeredis for tests. Returns the previous client."""
    global _client
    prev = _client
    _client = client
    return prev


def _hour_key(wa_number: str) -> str:
    return f"rate:hour:{wa_number}"


def _day_key(wa_number: str) -> str:
    return f"rate:day:{wa_number}"


def _cooldown_key(wa_number: str, text: str) -> str:
    # Short hex digest is plenty — same text in the cooldown window collides
    # by design (that's the whole point of the dedup).
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    return f"recent:{wa_number}:{digest}"


async def check_and_record(wa_number: str) -> bool:
    """Increment per-hour and per-day counters, return True if within limits.

    Call this once per inbound message before doing work. Returns False if
    EITHER the hourly or daily limit is exceeded — the caller should reply
    with the rate-limited error message.
    """
    settings = get_settings()
    r = _redis()
    try:
        hour_count = int(await r.incr(_hour_key(wa_number)))
        if hour_count == 1:
            await r.expire(_hour_key(wa_number), 3600)
        day_count = int(await r.incr(_day_key(wa_number)))
        if day_count == 1:
            await r.expire(_day_key(wa_number), 86400)
    except aioredis.RedisError as e:
        log.warning("rate_limit_check_failed", wa_number=wa_number, error=str(e))
        return True  # fail open

    if hour_count > settings.rate_limit_per_hour:
        log.info(
            "rate_limited",
            scope="hour",
            wa_number=wa_number,
            count=hour_count,
            limit=settings.rate_limit_per_hour,
        )
        return False
    if day_count > settings.rate_limit_per_day:
        log.info(
            "rate_limited",
            scope="day",
            wa_number=wa_number,
            count=day_count,
            limit=settings.rate_limit_per_day,
        )
        return False
    return True


async def is_duplicate(wa_number: str, text: str) -> bool:
    """Return True if the same text was sent by this number within the
    cooldown window. The first send wins; subsequent ones inside the
    window are flagged as duplicates and should be silently ignored.

    Empty text or a zero-second cooldown disables the check.
    """
    settings = get_settings()
    if not text or settings.identical_query_cooldown_seconds <= 0:
        return False
    r = _redis()
    try:
        was_set = await r.set(
            _cooldown_key(wa_number, text),
            "1",
            ex=settings.identical_query_cooldown_seconds,
            nx=True,
        )
    except aioredis.RedisError as e:
        log.warning("cooldown_check_failed", wa_number=wa_number, error=str(e))
        return False  # fail open
    return not was_set  # NX=true means first-time set; not set ⇒ duplicate
