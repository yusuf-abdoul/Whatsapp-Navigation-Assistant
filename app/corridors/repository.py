"""Corridor data access — read-side queries the orchestrator depends on.

Two responsibilities:

1. Given a destination phrase ("Banex", "Jabi Lake Mall"), find approved corridors
   ending at a matching anchor. Match against the anchor's name *and* its aliases,
   case-insensitively.
2. Given a user's coordinates, find the nearest anchor on a candidate corridor.
   Brute-force haversine for now; PostGIS is the upgrade path when row counts grow.

Write-side (creating corridors / segments) lives in admin tooling, not here.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.corridors.models import Anchor, Corridor, Segment

EARTH_RADIUS_M = 6_371_000.0


async def find_corridors_by_destination(
    db: AsyncSession,
    query: str,
    *,
    city: str | None = None,
    only_approved: bool = True,
) -> Sequence[Corridor]:
    """Return corridors whose destination anchor matches `query` by name or alias.

    Match is case-insensitive against `Anchor.name` and any string in `Anchor.aliases`.
    """
    needle = query.strip().lower()
    if not needle:
        return []

    name_match = func.lower(Anchor.name) == needle
    # `aliases @> ARRAY[needle]` — array contains the needle.
    alias_match = Anchor.aliases.contains([needle])
    where = [or_(name_match, alias_match)]
    if city is not None:
        where.append(Anchor.city == city)

    stmt = (
        select(Corridor)
        .join(Anchor, Anchor.id == Corridor.destination_anchor_id)
        .where(*where)
        .options(selectinload(Corridor.segments))
    )
    if only_approved:
        stmt = stmt.where(Corridor.status == "approved")

    result = await db.execute(stmt)
    return result.scalars().unique().all()


async def nearest_anchor_in_corridor(
    db: AsyncSession, corridor_id: uuid.UUID, lat: float, lon: float
) -> tuple[Anchor, float] | None:
    """Find the anchor on `corridor_id` closest to (lat, lon).

    Returns (anchor, distance_metres) or None if the corridor has no segments.
    """
    anchors = await _corridor_anchors(db, corridor_id)
    if not anchors:
        return None
    best = min(anchors, key=lambda a: _haversine_m(lat, lon, a.lat, a.lon))
    return best, _haversine_m(lat, lon, best.lat, best.lon)


async def _corridor_anchors(db: AsyncSession, corridor_id: uuid.UUID) -> list[Anchor]:
    """All distinct anchors that appear on the corridor's segments, in order of first appearance."""
    stmt = (
        select(Segment)
        .where(Segment.corridor_id == corridor_id)
        .order_by(Segment.sequence)
        .options(selectinload(Segment.from_anchor), selectinload(Segment.to_anchor))
    )
    segments = (await db.execute(stmt)).scalars().all()

    seen: set[uuid.UUID] = set()
    ordered: list[Anchor] = []
    for s in segments:
        for anchor in (s.from_anchor, s.to_anchor):
            if anchor.id not in seen:
                seen.add(anchor.id)
                ordered.append(anchor)
    return ordered


def clip_segments_from_anchor(
    segments: Sequence[Segment], anchor_id: uuid.UUID
) -> list[Segment]:
    """Return segments from the first one whose `from_anchor_id` matches `anchor_id` onward.

    Mirrors the corridor model: a user joining at a mid-corridor anchor only needs the
    suffix of the route from that point. If the anchor isn't a `from_anchor` in any
    segment (e.g., it's only a destination), return an empty list.
    """
    for i, seg in enumerate(segments):
        if seg.from_anchor_id == anchor_id:
            return list(segments[i:])
    return []


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
