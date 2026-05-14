"""Tests for Gap 2 — intermediate-anchor destinations.

A user can ask for a destination that's not the END of any corridor but
appears mid-route on one. We resolve the destination to an anchor row, find
corridors that contain that anchor anywhere, and clip the route to the slice
between the user's origin and that anchor.

Covers:
- Repository: ``find_corridors_containing_anchor`` matches by endpoint /
  passthrough / destination
- Repository: ``clip_segments_between`` handles origin-before-destination,
  passthrough at either end, and wrong-direction
- Renderer: ``end_anchor`` overrides the displayed destination in the
  header AND in the last step's "to"
"""

import uuid

from app.corridors.models import Anchor, Corridor, Segment
from app.corridors.repository import (
    clip_segments_between,
    find_corridors_containing_anchor,
)

# --- clip_segments_between ---------------------------------------------


def _seg_with_ids(
    sequence: int,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    passthroughs: list[uuid.UUID] | None = None,
) -> Segment:
    s = Segment(
        sequence=sequence,
        from_anchor_id=from_id,
        to_anchor_id=to_id,
        mode="taxi",
        instruction="x",
    )
    s.passthrough_anchor_ids = passthroughs or []
    return s


def test_clip_between_returns_full_slice_in_order():
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    segs = [
        _seg_with_ids(1, a, b),
        _seg_with_ids(2, b, c),
        _seg_with_ids(3, c, d),
    ]
    clipped = clip_segments_between(segs, a, c)
    assert [s.sequence for s in clipped] == [1, 2]


def test_clip_between_origin_at_passthrough():
    a, b, c, mid = (uuid.uuid4() for _ in range(4))
    segs = [
        _seg_with_ids(1, a, b, passthroughs=[mid]),
        _seg_with_ids(2, b, c),
    ]
    clipped = clip_segments_between(segs, mid, c)
    # Origin is a passthrough on seg 1 → clip starts at seg 1.
    assert [s.sequence for s in clipped] == [1, 2]


def test_clip_between_destination_at_passthrough():
    a, b, c, mid = (uuid.uuid4() for _ in range(4))
    segs = [
        _seg_with_ids(1, a, b),
        _seg_with_ids(2, b, c, passthroughs=[mid]),
    ]
    clipped = clip_segments_between(segs, a, mid)
    # Destination is a passthrough on seg 2 → clip ends at seg 2.
    assert [s.sequence for s in clipped] == [1, 2]


def test_clip_between_origin_and_destination_in_same_segment():
    a, b, mid_a, mid_b = (uuid.uuid4() for _ in range(4))
    segs = [
        _seg_with_ids(1, a, b, passthroughs=[mid_a, mid_b]),
    ]
    clipped = clip_segments_between(segs, mid_a, mid_b)
    assert [s.sequence for s in clipped] == [1]


def test_clip_between_returns_empty_for_wrong_direction():
    a, b, c = (uuid.uuid4() for _ in range(3))
    segs = [
        _seg_with_ids(1, a, b),
        _seg_with_ids(2, b, c),
    ]
    # Asking for the slice c -> a is invalid; corridors are one-way.
    assert clip_segments_between(segs, c, a) == []


def test_clip_between_returns_empty_when_origin_missing():
    a, b = uuid.uuid4(), uuid.uuid4()
    ghost = uuid.uuid4()
    segs = [_seg_with_ids(1, a, b)]
    assert clip_segments_between(segs, ghost, b) == []


def test_clip_between_returns_empty_when_destination_missing():
    a, b = uuid.uuid4(), uuid.uuid4()
    ghost = uuid.uuid4()
    segs = [_seg_with_ids(1, a, b)]
    assert clip_segments_between(segs, a, ghost) == []


# --- find_corridors_containing_anchor ----------------------------------


async def _seed_lugbe_to_banex(db) -> dict[str, Anchor]:
    police = Anchor(name="Police Signpost", lat=8.94, lon=7.36, city="abuja", aliases=[])
    car_wash = Anchor(name="Car Wash", lat=8.97, lon=7.39, city="abuja", aliases=[])
    fed = Anchor(name="Federal Housing", lat=9.00, lon=7.42, city="abuja", aliases=[])
    berger = Anchor(name="Berger", lat=9.04, lon=7.46, city="abuja", aliases=[])
    banex = Anchor(name="Banex Plaza", lat=9.075, lon=7.482, city="abuja", aliases=["banex"])
    db.add_all([police, car_wash, fed, berger, banex])
    await db.flush()

    corridor = Corridor(destination_anchor_id=banex.id, status="approved")
    db.add(corridor)
    await db.flush()
    db.add_all(
        [
            Segment(
                corridor_id=corridor.id,
                sequence=1,
                from_anchor_id=police.id,
                to_anchor_id=berger.id,
                mode="taxi",
                instruction="x",
                passthrough_anchor_ids=[car_wash.id, fed.id],
            ),
            Segment(
                corridor_id=corridor.id,
                sequence=2,
                from_anchor_id=berger.id,
                to_anchor_id=banex.id,
                mode="taxi",
                instruction="y",
                transfer=True,
            ),
        ]
    )
    await db.flush()
    return {
        "police": police,
        "car_wash": car_wash,
        "fed": fed,
        "berger": berger,
        "banex": banex,
    }


async def test_find_corridors_containing_anchor_matches_destination(db):
    anchors = await _seed_lugbe_to_banex(db)
    found = await find_corridors_containing_anchor(db, anchors["banex"].id, city="abuja")
    assert len(found) == 1


async def test_find_corridors_containing_anchor_matches_segment_endpoint(db):
    anchors = await _seed_lugbe_to_banex(db)
    # Berger is the to_anchor of segment 1 (and from_anchor of segment 2).
    found = await find_corridors_containing_anchor(db, anchors["berger"].id, city="abuja")
    assert len(found) == 1


async def test_find_corridors_containing_anchor_matches_passthrough(db):
    anchors = await _seed_lugbe_to_banex(db)
    # Car Wash sits in segment 1's passthrough list, not as any endpoint.
    found = await find_corridors_containing_anchor(db, anchors["car_wash"].id, city="abuja")
    assert len(found) == 1


async def test_find_corridors_containing_anchor_respects_only_approved(db):
    anchors = await _seed_lugbe_to_banex(db)
    # Flip status to pending — should disappear from the default lookup.
    [corridor] = (await db.execute(__import__("sqlalchemy").select(Corridor))).scalars().all()
    corridor.status = "pending"
    await db.flush()
    found = await find_corridors_containing_anchor(db, anchors["berger"].id, city="abuja")
    assert found == []


async def test_find_corridors_containing_anchor_scoped_by_city(db):
    anchors = await _seed_lugbe_to_banex(db)
    found = await find_corridors_containing_anchor(db, anchors["berger"].id, city="lagos")
    assert found == []
    _ = anchors


# --- renderer with end_anchor ------------------------------------------


def test_format_corridor_end_anchor_overrides_header_and_last_step():
    """The header and the last step's "to" both use end_anchor.name."""
    from unittest.mock import MagicMock

    from app.formatting.responses import format_corridor

    def _seg(
        *, from_anchor, to_anchor, mode="taxi", transfer=False, cost_ngn=None, duration_min=None
    ):
        s = MagicMock()
        s.mode = mode
        s.transfer = transfer
        s.cost_ngn = cost_ngn
        s.duration_min = duration_min
        s.from_anchor = MagicMock()
        s.from_anchor.name = from_anchor
        s.to_anchor = MagicMock()
        s.to_anchor.name = to_anchor
        return s

    def _anchor(name):
        a = MagicMock()
        a.name = name
        return a

    corridor = MagicMock()
    corridor.destination.name = "Banex Plaza"
    corridor.applicability_notes = None

    # Corridor goes A -> B -> C, but the user wants to stop at B.
    segs = [_seg(from_anchor="A", to_anchor="B")]

    out = format_corridor(corridor, segs, end_anchor=_anchor("B"))

    assert "To B:" in out  # header uses end_anchor, not corridor.destination
    assert "Take a taxi from A to B." in out
    assert "Banex Plaza" not in out  # corridor.destination shouldn't show up
