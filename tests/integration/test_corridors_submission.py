"""Tests for the contributor submission path.

Covers both the Pydantic validation (per-field + cross-field) and the persist
step (anchors upserted by name+city, pending corridor inserted with the
contributor's id).
"""

import uuid

import pytest
from sqlalchemy import select

from app.corridors.models import Anchor, Segment
from app.corridors.submission import (
    AnchorInput,
    CorridorSubmission,
    SegmentInput,
    SubmissionError,
    create_pending,
)


def _valid_payload(**overrides) -> CorridorSubmission:
    base = dict(
        city="testcity",
        destination="B",
        applicability_notes=None,
        anchors=[
            AnchorInput(name="A", lat=9.0, lon=7.0, aliases=["a-alias"]),
            AnchorInput(name="B", lat=9.1, lon=7.1),
        ],
        segments=[
            SegmentInput(
                from_anchor="A", to_anchor="B", mode="taxi", instruction="Take a taxi to B."
            )
        ],
    )
    base.update(overrides)
    return CorridorSubmission(**base)


# --- schema-level validation --------------------------------------------


def test_aliases_string_is_split_on_commas() -> None:
    a = AnchorInput(name="A", lat=9.0, lon=7.0, aliases="banex, banex plaza")
    assert a.aliases == ["banex", "banex plaza"]


def test_aliases_are_lowercased() -> None:
    a = AnchorInput(name="A", lat=9.0, lon=7.0, aliases=["Banex", "POLICE"])
    assert a.aliases == ["banex", "police"]


def test_segment_rejects_unknown_mode() -> None:
    with pytest.raises(ValueError, match="mode"):
        SegmentInput(from_anchor="A", to_anchor="B", mode="rocket", instruction="x")


def test_cross_validate_requires_destination_in_anchors() -> None:
    payload = _valid_payload(destination="NotThere")
    with pytest.raises(SubmissionError, match="Destination"):
        payload.cross_validate()


def test_cross_validate_requires_segment_endpoints_in_anchors() -> None:
    payload = _valid_payload(
        segments=[SegmentInput(from_anchor="Ghost", to_anchor="B", mode="taxi", instruction="x")]
    )
    with pytest.raises(SubmissionError, match="Ghost"):
        payload.cross_validate()


def test_cross_validate_rejects_self_loop_segment() -> None:
    payload = _valid_payload(
        segments=[SegmentInput(from_anchor="A", to_anchor="A", mode="taxi", instruction="x")]
    )
    with pytest.raises(SubmissionError, match="must differ"):
        payload.cross_validate()


def test_cross_validate_rejects_duplicate_anchor_names() -> None:
    payload = _valid_payload(
        anchors=[
            AnchorInput(name="A", lat=9.0, lon=7.0),
            AnchorInput(name="A", lat=9.1, lon=7.1),
        ]
    )
    with pytest.raises(SubmissionError, match="unique"):
        payload.cross_validate()


# --- persist layer ------------------------------------------------------


async def test_create_pending_inserts_corridor_with_status_pending(db) -> None:
    contributor_id = str(uuid.uuid4())
    corridor = await create_pending(db, payload=_valid_payload(), contributor_id=contributor_id)
    assert corridor.status == "pending"
    assert corridor.contributor_id == contributor_id
    assert len(corridor.segments) == 1
    assert corridor.segments[0].sequence == 1


async def test_create_pending_upserts_anchors_by_name_and_city(db) -> None:
    contributor_id = str(uuid.uuid4())

    # Pre-existing anchor (same name, same city) — should be reused, not duplicated.
    existing = Anchor(name="A", lat=9.0, lon=7.0, city="testcity", aliases=["old"])
    db.add(existing)
    await db.flush()

    payload = _valid_payload(
        anchors=[
            AnchorInput(name="A", lat=9.0, lon=7.0, aliases=["new"]),
            AnchorInput(name="B", lat=9.1, lon=7.1),
        ]
    )
    await create_pending(db, payload=payload, contributor_id=contributor_id)

    rows = (await db.execute(select(Anchor).where(Anchor.city == "testcity"))).scalars().all()
    a_row = next(r for r in rows if r.name == "A")
    assert sorted(a_row.aliases) == ["new", "old"]  # aliases unioned
    assert {r.name for r in rows} == {"A", "B"}


async def test_create_pending_does_not_overwrite_existing_anchor_coords(db) -> None:
    """A pending submission MUST NOT mutate an established anchor's coordinates.

    Only the admin review path (Phase 2d) may correct lat/lon — otherwise a
    single bad submission could silently move pins for every corridor that
    references the same anchor.
    """
    db.add(Anchor(name="Berger", lat=9.040, lon=7.460, city="abuja", aliases=[]))
    await db.flush()

    payload = _valid_payload(
        anchors=[
            AnchorInput(name="Berger", lat=8.000, lon=6.000, aliases=["berger junction"]),
            AnchorInput(name="Destination", lat=9.1, lon=7.1),
        ],
        destination="Destination",
        segments=[
            SegmentInput(
                from_anchor="Berger", to_anchor="Destination", mode="taxi", instruction="go"
            )
        ],
    )
    # Override the test's default city to land on the existing Berger row.
    payload_with_city = CorridorSubmission(
        city="abuja",
        destination=payload.destination,
        anchors=payload.anchors,
        segments=payload.segments,
    )

    await create_pending(db, payload=payload_with_city, contributor_id=str(uuid.uuid4()))

    row = (
        await db.execute(select(Anchor).where(Anchor.name == "Berger", Anchor.city == "abuja"))
    ).scalar_one()
    # Coords preserved from the first contributor; aliases merged with the new submission.
    assert row.lat == pytest.approx(9.040)
    assert row.lon == pytest.approx(7.460)
    assert "berger junction" in row.aliases


async def test_create_pending_preserves_segment_order(db) -> None:
    contributor_id = str(uuid.uuid4())
    payload = _valid_payload(
        anchors=[
            AnchorInput(name="A", lat=9.0, lon=7.0),
            AnchorInput(name="B", lat=9.1, lon=7.1),
            AnchorInput(name="C", lat=9.2, lon=7.2),
        ],
        destination="C",
        segments=[
            SegmentInput(from_anchor="A", to_anchor="B", mode="taxi", instruction="A->B"),
            SegmentInput(from_anchor="B", to_anchor="C", mode="keke", instruction="B->C"),
        ],
    )
    corridor = await create_pending(db, payload=payload, contributor_id=contributor_id)

    segs = (
        (
            await db.execute(
                select(Segment).where(Segment.corridor_id == corridor.id).order_by(Segment.sequence)
            )
        )
        .scalars()
        .all()
    )
    assert [s.sequence for s in segs] == [1, 2]
    assert [s.instruction for s in segs] == ["A->B", "B->C"]


async def test_create_pending_propagates_transfer_and_costs(db) -> None:
    contributor_id = str(uuid.uuid4())
    payload = _valid_payload(
        segments=[
            SegmentInput(
                from_anchor="A",
                to_anchor="B",
                mode="taxi",
                instruction="Take a taxi to B.",
                transfer=True,
                cost_ngn=300,
                duration_min=12,
            )
        ]
    )
    corridor = await create_pending(db, payload=payload, contributor_id=contributor_id)
    seg = corridor.segments[0]
    assert seg.transfer is True
    assert seg.cost_ngn == 300
    assert seg.duration_min == 12
