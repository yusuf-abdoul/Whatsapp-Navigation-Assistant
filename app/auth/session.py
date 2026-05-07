"""Web-session helpers: read/write the signed cookie, fetch the current user.

We use Starlette's ``SessionMiddleware`` (signed cookie via ``itsdangerous``).
The session dict carries one key: ``user_id`` (str UUID). On every request
``current_user`` looks that up in the DB; missing or invalid id → None, and
the caller decides whether to redirect.
"""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy.exc import SQLAlchemyError

from app.corridors.db import session_factory
from app.users.models import User
from app.users.repository import get_by_id


async def current_user(request: Request) -> User | None:
    """Return the logged-in user, or None. Tolerates DB errors and bad ids."""
    raw_id = request.session.get("user_id") if hasattr(request, "session") else None
    if not raw_id:
        return None
    try:
        user_uuid = uuid.UUID(raw_id)
    except (ValueError, TypeError):
        return None

    try:
        factory = session_factory()
        async with factory() as db:
            return await get_by_id(db, user_uuid)
    except SQLAlchemyError:
        return None


def login(request: Request, user: User) -> None:
    request.session["user_id"] = str(user.id)


def logout(request: Request) -> None:
    request.session.clear()
