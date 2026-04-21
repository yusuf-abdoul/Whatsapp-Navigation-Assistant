"""Redis-backed session store. Keyed by user_id with TTL from settings."""

from app.config import get_settings
from app.session.state import SessionState


async def get(user_id: str) -> SessionState | None:
    raise NotImplementedError


async def put(state: SessionState) -> None:
    _ = get_settings().session_ttl_seconds
    raise NotImplementedError


async def delete(user_id: str) -> None:
    raise NotImplementedError
