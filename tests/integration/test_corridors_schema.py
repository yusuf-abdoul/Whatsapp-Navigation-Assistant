"""Schema-shape tests for corridors / anchors / segments.

These guard the *invariants* (FKs, check constraints, cascades, defaults) so the
data layer can rely on them downstream. Each test runs in a rolled-back
transaction so they don't pollute each other.
"""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.corridors.models import Anchor, Corridor, Segment


def _anchor(**overrides) -> Anchor:
    base = dict(
        name=f"Anchor-{uuid.uuid4().hex[:8]}",
        lat=9.0,
        lon=7.5,
        city="abuja",
    )
    base.update(overrides)
    return Anchor(**base)


async def test_anchor_aliases_default_empty(db) -> None:
    a = _anchor()
    db.add(a)
    await db.flush()
    await db.refresh(a)
    assert a.aliases == []


async def test_anchor_unique_name_per_city(db) -> None:
    db.add(_anchor(name="Berger", city="abuja"))
    await db.flush()
    db.add(_anchor(name="Berger", city="abuja"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_same_anchor_name_allowed_in_different_cities(db) -> None:
    db.add(_anchor(name="Berger", city="abuja"))
    db.add(_anchor(name="Berger", city="lagos"))
    await db.flush()  # no error


async def test_corridor_status_default_is_pending(db) -> None:
    dest = _anchor()
    db.add(dest)
    await db.flush()

    c = Corridor(destination_anchor_id=dest.id)
    db.add(c)
    await db.flush()
    await db.refresh(c)
    assert c.status == "pending"
    assert c.applicability_windows == []


async def test_corridor_status_check_constraint(db) -> None:
    dest = _anchor()
    db.add(dest)
    await db.flush()

    db.add(Corridor(destination_anchor_id=dest.id, status="bogus"))
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_segment_mode_check_constraint(db) -> None:
    a, b = _anchor(), _anchor()
    db.add_all([a, b])
    await db.flush()
    c = Corridor(destination_anchor_id=b.id)
    db.add(c)
    await db.flush()

    db.add(
        Segment(
            corridor_id=c.id,
            sequence=1,
            from_anchor_id=a.id,
            to_anchor_id=b.id,
            mode="hovercraft",
            instruction="anything",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_segment_endpoints_must_differ(db) -> None:
    a = _anchor()
    db.add(a)
    await db.flush()
    c = Corridor(destination_anchor_id=a.id)
    db.add(c)
    await db.flush()

    db.add(
        Segment(
            corridor_id=c.id,
            sequence=1,
            from_anchor_id=a.id,
            to_anchor_id=a.id,
            mode="taxi",
            instruction="loop",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_segment_sequence_unique_per_corridor(db) -> None:
    a, b, mid = _anchor(), _anchor(), _anchor()
    db.add_all([a, b, mid])
    await db.flush()
    c = Corridor(destination_anchor_id=b.id)
    db.add(c)
    await db.flush()

    db.add(
        Segment(
            corridor_id=c.id,
            sequence=1,
            from_anchor_id=a.id,
            to_anchor_id=mid.id,
            mode="taxi",
            instruction="leg 1",
        )
    )
    db.add(
        Segment(
            corridor_id=c.id,
            sequence=1,
            from_anchor_id=mid.id,
            to_anchor_id=b.id,
            mode="taxi",
            instruction="leg 2 (collides)",
        )
    )
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_segments_cascade_delete_with_corridor(db) -> None:
    a, b = _anchor(), _anchor()
    db.add_all([a, b])
    await db.flush()
    c = Corridor(destination_anchor_id=b.id)
    db.add(c)
    await db.flush()
    db.add(
        Segment(
            corridor_id=c.id,
            sequence=1,
            from_anchor_id=a.id,
            to_anchor_id=b.id,
            mode="taxi",
            instruction="x",
        )
    )
    await db.flush()

    await db.delete(c)
    await db.flush()

    remaining = (await db.execute(select(Segment))).scalars().all()
    assert remaining == []


async def test_anchor_deletion_blocked_when_referenced(db) -> None:
    a, b = _anchor(), _anchor()
    db.add_all([a, b])
    await db.flush()
    db.add(Corridor(destination_anchor_id=b.id))
    await db.flush()

    await db.delete(b)
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_corridor_loads_segments_in_sequence_order(db) -> None:
    a, mid, b = _anchor(), _anchor(), _anchor()
    db.add_all([a, mid, b])
    await db.flush()
    c = Corridor(destination_anchor_id=b.id)
    db.add(c)
    await db.flush()

    # Insert out of order to verify the ORDER BY on the relationship.
    db.add_all(
        [
            Segment(
                corridor_id=c.id,
                sequence=2,
                from_anchor_id=mid.id,
                to_anchor_id=b.id,
                mode="keke",
                instruction="leg 2",
            ),
            Segment(
                corridor_id=c.id,
                sequence=1,
                from_anchor_id=a.id,
                to_anchor_id=mid.id,
                mode="taxi",
                instruction="leg 1",
            ),
        ]
    )
    await db.flush()
    await db.refresh(c, attribute_names=["segments"])
    assert [s.sequence for s in c.segments] == [1, 2]
    assert [s.instruction for s in c.segments] == ["leg 1", "leg 2"]
