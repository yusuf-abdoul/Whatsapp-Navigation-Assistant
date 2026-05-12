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


async def find_anchor_by_name(
    db: AsyncSession, query: str, *, city: str | None = None
) -> Anchor | None:
    """Look up an anchor by exact name or alias (case-insensitive).

    Used when the user names a known place as their origin (e.g. typing "Police
    Signpost") — we want to use the anchor's coordinates rather than send the
    text through LocationIQ, which often returns nearby addresses instead.
    """
    needle = query.strip().lower()
    if not needle:
        return None
    where = [or_(func.lower(Anchor.name) == needle, Anchor.aliases.contains([needle]))]
    if city is not None:
        where.append(Anchor.city == city)
    stmt = select(Anchor).where(*where).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def search_anchors(
    db: AsyncSession, query: str, *, city: str | None = None, limit: int = 5
) -> Sequence[Anchor]:
    """Prefix/substring search across anchor names and aliases.

    Powers the submission-form autocomplete: as a contributor types a name,
    we suggest existing anchors so they don't have to enter coordinates that
    are already known. Case-insensitive; matches ``ILIKE %q%`` on name and
    array-containment on aliases.
    """
    needle = query.strip().lower()
    if len(needle) < 2:
        return []
    name_like = func.lower(Anchor.name).like(f"%{needle}%")
    alias_match = Anchor.aliases.contains([needle])
    where = [or_(name_like, alias_match)]
    if city is not None:
        where.append(Anchor.city == city)
    stmt = select(Anchor).where(*where).order_by(Anchor.name).limit(limit)
    return (await db.execute(stmt)).scalars().all()


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
    """All distinct anchors that act as join points for the corridor, in order
    of first appearance.

    Join points = segment endpoints (from/to) PLUS any passthrough anchors on
    a segment. A passthrough is a named place the vehicle physically passes
    through on a leg — a rider near it can board the same vehicle and follow
    the same instruction as if they'd boarded at the leg's `from`.
    """
    stmt = (
        select(Segment)
        .where(Segment.corridor_id == corridor_id)
        .order_by(Segment.sequence)
        .options(selectinload(Segment.from_anchor), selectinload(Segment.to_anchor))
    )
    segments = list((await db.execute(stmt)).scalars().all())

    seen: set[uuid.UUID] = set()
    ordered: list[Anchor] = []
    for s in segments:
        for anchor in (s.from_anchor, s.to_anchor):
            if anchor.id not in seen:
                seen.add(anchor.id)
                ordered.append(anchor)

    # Bulk-load passthrough anchors across all segments in one query so we
    # don't N+1 over the segment list.
    passthrough_ids: set[uuid.UUID] = set()
    for s in segments:
        passthrough_ids.update(s.passthrough_anchor_ids or [])
    passthrough_ids.difference_update(seen)  # already covered by endpoints
    if passthrough_ids:
        rows = (
            await db.execute(select(Anchor).where(Anchor.id.in_(passthrough_ids)))
        ).scalars().all()
        for a in rows:
            seen.add(a.id)
            ordered.append(a)
    return ordered


def clip_segments_from_anchor(segments: Sequence[Segment], anchor_id: uuid.UUID) -> list[Segment]:
    """Return segments from the first one whose `from_anchor_id` (or whose
    `passthrough_anchor_ids`) matches ``anchor_id``, onward.

    A passthrough match clips to THAT segment: a rider boarding mid-leg at a
    passthrough still takes the same instruction as someone boarding at the
    leg's `from` (the instruction names the leg's destination, not its origin).
    """
    for i, seg in enumerate(segments):
        if seg.from_anchor_id == anchor_id:
            return list(segments[i:])
        if anchor_id in (seg.passthrough_anchor_ids or []):
            return list(segments[i:])
    return []


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
