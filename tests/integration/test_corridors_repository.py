"""Repository-layer tests.

Cover the three contracts the orchestrator depends on:
- destination lookup matches by name or alias, case-insensitive, scoped by city, approved-only
- nearest-anchor returns the closest point on a corridor
- segment clipping returns the route suffix from the user's join point
"""

import uuid

import pytest

from app.corridors.models import Anchor, Corridor, Segment
from app.corridors.repository import (
    _haversine_m,
    clip_segments_from_anchor,
    find_corridors_by_destination,
    nearest_anchor_in_corridor,
)


def _anchor(name: str, *, lat: float = 9.0, lon: float = 7.5, city: str = "abuja", aliases=None):
    return Anchor(name=name, lat=lat, lon=lon, city=city, aliases=aliases or [])


async def _seed_lugbe_to_banex_corridor(db, *, status: str = "approved"):
    police = _anchor("Police Signpost", lat=8.94, lon=7.36, aliases=["police signboard"])
    car_wash = _anchor("Car Wash", lat=8.97, lon=7.39)
    fed_bridge = _anchor("Federal Housing Bridge", lat=9.00, lon=7.42)
    berger = _anchor("Berger", lat=9.04, lon=7.46)
    banex = _anchor("Banex Plaza", lat=9.075, lon=7.482, aliases=["banex"])
    db.add_all([police, car_wash, fed_bridge, berger, banex])
    await db.flush()

    c = Corridor(destination_anchor_id=banex.id, status=status)
    db.add(c)
    await db.flush()

    db.add_all(
        [
            Segment(
                corridor_id=c.id,
                sequence=1,
                from_anchor_id=police.id,
                to_anchor_id=car_wash.id,
                mode="taxi",
                instruction="Take a taxi heading Berger (you'll pass Car Wash)",
            ),
            Segment(
                corridor_id=c.id,
                sequence=2,
                from_anchor_id=car_wash.id,
                to_anchor_id=fed_bridge.id,
                mode="taxi",
                instruction="Stay on the same taxi past Federal Housing Bridge",
            ),
            Segment(
                corridor_id=c.id,
                sequence=3,
                from_anchor_id=fed_bridge.id,
                to_anchor_id=berger.id,
                mode="taxi",
                instruction="Stay on until Berger junction",
            ),
            Segment(
                corridor_id=c.id,
                sequence=4,
                from_anchor_id=berger.id,
                to_anchor_id=banex.id,
                mode="keke",
                instruction="Take a keke from Berger to Banex Plaza",
            ),
        ]
    )
    await db.flush()
    return {
        "corridor": c,
        "anchors": {
            "police": police,
            "car_wash": car_wash,
            "fed_bridge": fed_bridge,
            "berger": berger,
            "banex": banex,
        },
    }


async def test_find_by_destination_name_case_insensitive(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    found = await find_corridors_by_destination(db, "BANEX PLAZA")
    assert [c.id for c in found] == [seeded["corridor"].id]


async def test_find_by_destination_alias(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    found = await find_corridors_by_destination(db, "banex")
    assert [c.id for c in found] == [seeded["corridor"].id]


async def test_find_by_destination_returns_empty_when_no_match(db):
    await _seed_lugbe_to_banex_corridor(db)
    assert list(await find_corridors_by_destination(db, "nowhere")) == []


async def test_find_by_destination_only_approved_by_default(db):
    await _seed_lugbe_to_banex_corridor(db, status="pending")
    assert list(await find_corridors_by_destination(db, "banex")) == []


async def test_find_by_destination_includes_pending_when_asked(db):
    await _seed_lugbe_to_banex_corridor(db, status="pending")
    found = await find_corridors_by_destination(db, "banex", only_approved=False)
    assert len(found) == 1


async def test_find_by_destination_scoped_to_city(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    assert len(await find_corridors_by_destination(db, "banex", city="abuja")) == 1
    assert list(await find_corridors_by_destination(db, "banex", city="lagos")) == []
    _ = seeded


async def test_find_by_destination_loads_segments_in_order(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    [c] = await find_corridors_by_destination(db, "banex")
    assert [s.sequence for s in c.segments] == [1, 2, 3, 4]
    assert seeded["corridor"].id == c.id


async def test_nearest_anchor_picks_closest_point_on_corridor(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    # User very close to Car Wash anchor
    car_wash = seeded["anchors"]["car_wash"]
    result = await nearest_anchor_in_corridor(
        db, seeded["corridor"].id, lat=car_wash.lat + 0.0005, lon=car_wash.lon
    )
    assert result is not None
    nearest, dist_m = result
    assert nearest.id == car_wash.id
    assert dist_m < 100  # within ~100m of the anchor


async def test_nearest_anchor_returns_none_for_empty_corridor(db):
    a = _anchor("solo")
    db.add(a)
    await db.flush()
    c = Corridor(destination_anchor_id=a.id)
    db.add(c)
    await db.flush()
    assert await nearest_anchor_in_corridor(db, c.id, lat=9.0, lon=7.5) is None


async def test_nearest_anchor_returns_none_for_unknown_corridor(db):
    assert await nearest_anchor_in_corridor(db, uuid.uuid4(), lat=9.0, lon=7.5) is None


async def test_clip_segments_from_join_point(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    [c] = await find_corridors_by_destination(db, "banex")
    berger_id = seeded["anchors"]["berger"].id

    # User joining at Berger should only get the final keke leg.
    clipped = clip_segments_from_anchor(c.segments, berger_id)
    assert len(clipped) == 1
    assert clipped[0].mode == "keke"
    assert clipped[0].sequence == 4


async def test_clip_segments_returns_full_route_from_origin_anchor(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    [c] = await find_corridors_by_destination(db, "banex")
    police_id = seeded["anchors"]["police"].id

    clipped = clip_segments_from_anchor(c.segments, police_id)
    assert [s.sequence for s in clipped] == [1, 2, 3, 4]


async def test_clip_segments_returns_empty_when_anchor_only_destination(db):
    seeded = await _seed_lugbe_to_banex_corridor(db)
    [c] = await find_corridors_by_destination(db, "banex")
    banex_id = seeded["anchors"]["banex"].id

    # Banex is only a `to_anchor`, never a `from_anchor` — user can't "join here" and continue.
    assert clip_segments_from_anchor(c.segments, banex_id) == []


def test_haversine_zero_distance():
    assert _haversine_m(9.0, 7.5, 9.0, 7.5) == pytest.approx(0.0, abs=1e-6)


def test_haversine_known_separation():
    # Police Signpost (~8.94, 7.36) → Banex Plaza (~9.075, 7.482): roughly 20 km crow-flies.
    d = _haversine_m(8.94, 7.36, 9.075, 7.482)
    assert 18_000 < d < 22_000
