"""Admin operations on the corridor data layer.

Read-side: list pending corridors + load detail (with submitter + segments).
Write-side: approve, reject, fix an anchor's coordinates.

Authorization is enforced in the web router, not here — these functions
assume the caller is already an admin.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.corridors.models import Anchor, Corridor, Segment
from app.users.models import User


async def list_pending(db: AsyncSession) -> Sequence[Corridor]:
    """Return all pending corridors, newest-first.

    Each row is loaded with its destination anchor + segments, so the queue
    view can show counts and the destination name without N+1 fetches.
    """
    stmt = (
        select(Corridor)
        .where(Corridor.status == "pending")
        .options(joinedload(Corridor.destination), selectinload(Corridor.segments))
        .order_by(Corridor.created_at.desc())
    )
    return (await db.execute(stmt)).scalars().unique().all()


async def get_detail(db: AsyncSession, corridor_id: uuid.UUID) -> Corridor:
    """Load a corridor with everything the detail view needs.

    Raises NoResultFound if the id doesn't exist.
    """
    stmt = (
        select(Corridor)
        .where(Corridor.id == corridor_id)
        .options(
            joinedload(Corridor.destination),
            selectinload(Corridor.segments).selectinload(Segment.from_anchor),
            selectinload(Corridor.segments).selectinload(Segment.to_anchor),
        )
    )
    result = (await db.execute(stmt)).scalars().unique().one_or_none()
    if result is None:
        raise NoResultFound(f"no corridor {corridor_id}")
    return result


async def get_submitter(db: AsyncSession, corridor: Corridor) -> User | None:
    """Look up the contributor record. ``contributor_id`` is a free-text uuid
    string today (seed corridors say ``"seed"`` — not a uuid — so we tolerate that)."""
    if not corridor.contributor_id:
        return None
    try:
        user_uuid = uuid.UUID(corridor.contributor_id)
    except (ValueError, TypeError):
        return None
    return await db.get(User, user_uuid)


async def approve(db: AsyncSession, corridor_id: uuid.UUID) -> Corridor:
    corridor = await get_detail(db, corridor_id)
    if corridor.status != "pending":
        return corridor  # already decided; no-op keeps approve idempotent
    corridor.status = "approved"
    corridor.approved_at = datetime.now(UTC)
    await db.flush()
    return corridor


async def reject(db: AsyncSession, corridor_id: uuid.UUID) -> Corridor:
    corridor = await get_detail(db, corridor_id)
    if corridor.status != "pending":
        return corridor
    corridor.status = "rejected"
    await db.flush()
    return corridor


async def update_anchor_coords(
    db: AsyncSession, anchor_id: uuid.UUID, *, lat: float, lon: float
) -> Anchor:
    """Admin-only path to correct an anchor's coordinates.

    This is the SOLE path that may mutate lat/lon — the contribution flow
    intentionally preserves whatever the first contributor set so a bad
    pending submission can't move a pin used by other corridors.
    """
    anchor = await db.get(Anchor, anchor_id)
    if anchor is None:
        raise NoResultFound(f"no anchor {anchor_id}")
    anchor.lat = lat
    anchor.lon = lon
    await db.flush()
    return anchor
