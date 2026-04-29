"""Redis-backed session store. Keyed by user_id with TTL from settings.

Every write refreshes the TTL so an active conversation stays alive; once the
user goes quiet for `session_ttl_seconds` the key evicts and the next message
starts fresh.
"""

import redis.asyncio as aioredis
import structlog

from app.config import get_settings
from app.session.state import SessionState

log = structlog.get_logger("session")

_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def _key(user_id: str) -> str:
    return f"session:{user_id}"


async def get(user_id: str) -> SessionState | None:
    try:
        raw = await _redis().get(_key(user_id))
    except aioredis.RedisError as e:
        log.warning("session_get_failed", user_id=user_id, error=str(e))
        return None
    if not raw:
        return None
    return SessionState.model_validate_json(raw)


async def put(state: SessionState) -> None:
    ttl = get_settings().session_ttl_seconds
    try:
        await _redis().set(_key(state.user_id), state.model_dump_json(), ex=ttl)
    except aioredis.RedisError as e:
        log.warning("session_put_failed", user_id=state.user_id, error=str(e))


async def delete(user_id: str) -> None:
    try:
        await _redis().delete(_key(user_id))
    except aioredis.RedisError as e:
        log.warning("session_delete_failed", user_id=user_id, error=str(e))
