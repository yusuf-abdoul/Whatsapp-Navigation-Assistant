"""User CRUD. Identity is the WhatsApp number — unique, normalized to E.164."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.users.models import User


async def get_by_wa_number(db: AsyncSession, wa_number: str) -> User | None:
    stmt = select(User).where(User.wa_number == wa_number)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def create(db: AsyncSession, *, wa_number: str, name: str | None) -> User:
    user = User(wa_number=wa_number, name=name)
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user
