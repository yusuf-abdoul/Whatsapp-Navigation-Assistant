"""Tests for the passthrough-anchor feature.

Passthroughs let a step list intermediate anchors the vehicle passes through
without breaking the step into more legs. A rider near a passthrough joins
mid-step and gets the same instruction.

Covers:
- Repository: passthrough anchors are returned by ``nearest_anchor_in_corridor``
- Repository: ``clip_segments_from_anchor`` clips at a passthrough match
- Submission: passthroughs validate against the anchor list; persist as IDs
- Seed loader: ``passes_through:`` field in YAML works
- Web: ``seg_passthroughs`` form field flows through to the DB
"""

import uuid
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import otp
from app.corridors.models import Anchor, Corridor, Segment
from app.corridors.repository import (
    clip_segments_from_anchor,
    find_corridors_by_destination,
    nearest_anchor_in_corridor,
)
from app.corridors.seed import SeedError, load_file
from app.corridors.submission import (
    AnchorInput,
    CorridorSubmission,
    SegmentInput,
    SubmissionError,
    create_pending,
)
from app.main import app

# --- repository --------------------------------------------------------


async def test_nearest_anchor_picks_a_passthrough(db) -> None:
    """A rider physically at a passthrough anchor should match the corridor."""
    start = Anchor(name="Start", lat=9.0, lon=7.0, city="abuja", aliases=[])
    pass_a = Anchor(name="Mid-A", lat=9.05, lon=7.05, city="abuja", aliases=[])
    pass_b = Anchor(name="Mid-B", lat=9.10, lon=7.10, city="abuja", aliases=[])
    end = Anchor(name="End", lat=9.15, lon=7.15, city="abuja", aliases=[])
    db.add_all([start, pass_a, pass_b, end])
    await db.flush()

    corridor = Corridor(destination_anchor_id=end.id, status="approved")
    db.add(corridor)
    await db.flush()
    db.add(
        Segment(
            corridor_id=corridor.id,
            sequence=1,
            from_anchor_id=start.id,
            to_anchor_id=end.id,
            mode="taxi",
            instruction="Take a taxi to End.",
            passthrough_anchor_ids=[pass_a.id, pass_b.id],
        )
    )
    await db.flush()
    await db.commit()  # commit so a separate session sees it

    # User very near Mid-A — should match the corridor via the passthrough.
    result = await nearest_anchor_in_corridor(
        db, corridor.id, lat=9.0501, lon=7.0501
    )
    assert result is not None
    nearest, _ = result
    assert nearest.id == pass_a.id


async def test_clip_segments_starts_at_passthrough_match(db) -> None:
    """If the user joins at a passthrough, clipping starts at that step."""
    a = Anchor(name="A", lat=9.0, lon=7.0, city="abuja", aliases=[])
    b = Anchor(name="B", lat=9.05, lon=7.05, city="abuja", aliases=[])
    c = Anchor(name="C", lat=9.10, lon=7.10, city="abuja", aliases=[])
    pass_x = Anchor(name="X", lat=9.07, lon=7.07, city="abuja", aliases=[])
    db.add_all([a, b, c, pass_x])
    await db.flush()

    corridor = Corridor(destination_anchor_id=c.id, status="approved")
    db.add(corridor)
    await db.flush()
    db.add_all(
        [
            Segment(
                corridor_id=corridor.id,
                sequence=1,
                from_anchor_id=a.id,
                to_anchor_id=b.id,
                mode="taxi",
                instruction="leg 1",
            ),
            Segment(
                corridor_id=corridor.id,
                sequence=2,
                from_anchor_id=b.id,
                to_anchor_id=c.id,
                mode="taxi",
                instruction="leg 2",
                passthrough_anchor_ids=[pass_x.id],
            ),
        ]
    )
    await db.flush()
    await db.refresh(corridor, attribute_names=["segments"])

    # Joining at X (passthrough on segment 2) should give segments [2].
    clipped = clip_segments_from_anchor(corridor.segments, pass_x.id)
    assert [s.sequence for s in clipped] == [2]


# --- submission --------------------------------------------------------


def _payload_with_passthrough() -> CorridorSubmission:
    return CorridorSubmission(
        city="testcity",
        destination="End",
        anchors=[
            AnchorInput(name="Start", lat=9.0, lon=7.0),
            AnchorInput(name="Mid", lat=9.05, lon=7.05),
            AnchorInput(name="End", lat=9.1, lon=7.1),
        ],
        segments=[
            SegmentInput(
                from_anchor="Start",
                to_anchor="End",
                mode="taxi",
                instruction="Take a taxi to End.",
                passthroughs=["Mid"],
            )
        ],
    )


def test_submission_accepts_comma_separated_passthroughs() -> None:
    seg = SegmentInput(
        from_anchor="A",
        to_anchor="B",
        mode="taxi",
        instruction="x",
        passthroughs="One, Two ,  Three",
    )
    assert seg.passthroughs == ["One", "Two", "Three"]


def test_submission_rejects_passthrough_not_in_anchor_list() -> None:
    payload = CorridorSubmission(
        city="testcity",
        destination="End",
        anchors=[
            AnchorInput(name="Start", lat=9.0, lon=7.0),
            AnchorInput(name="End", lat=9.1, lon=7.1),
        ],
        segments=[
            SegmentInput(
                from_anchor="Start",
                to_anchor="End",
                mode="taxi",
                instruction="x",
                passthroughs=["Ghost"],
            )
        ],
    )
    with pytest.raises(SubmissionError, match="Ghost"):
        payload.cross_validate()


def test_submission_rejects_passthrough_that_is_already_an_endpoint() -> None:
    payload = CorridorSubmission(
        city="testcity",
        destination="End",
        anchors=[
            AnchorInput(name="Start", lat=9.0, lon=7.0),
            AnchorInput(name="End", lat=9.1, lon=7.1),
        ],
        segments=[
            SegmentInput(
                from_anchor="Start",
                to_anchor="End",
                mode="taxi",
                instruction="x",
                passthroughs=["End"],  # already the to_anchor
            )
        ],
    )
    with pytest.raises(SubmissionError, match="endpoint"):
        payload.cross_validate()


async def test_create_pending_persists_passthrough_anchor_ids(db) -> None:
    corridor = await create_pending(
        db, payload=_payload_with_passthrough(), contributor_id=str(uuid.uuid4())
    )
    [seg] = corridor.segments
    assert len(seg.passthrough_anchor_ids) == 1
    # Fetched anchor row's id should match.
    mid = (
        await db.execute(select(Anchor).where(Anchor.name == "Mid"))
    ).scalar_one()
    assert seg.passthrough_anchor_ids[0] == mid.id


# --- seed loader -------------------------------------------------------


async def test_seed_loader_accepts_passes_through(db, tmp_path) -> None:
    path = tmp_path / "corr.yaml"
    path.write_text(
        dedent(
            """
            city: testcity
            anchors:
              - {name: Start, lat: 9.0, lon: 7.0}
              - {name: Mid, lat: 9.05, lon: 7.05}
              - {name: End, lat: 9.1, lon: 7.1}
            destination: End
            segments:
              - sequence: 1
                from: Start
                to: End
                mode: taxi
                instruction: Take a taxi to End.
                passes_through: [Mid]
            """
        ).lstrip()
    )

    await load_file(db, path)
    seg = (await db.execute(select(Segment))).scalar_one()
    assert len(seg.passthrough_anchor_ids) == 1


async def test_seed_loader_rejects_unknown_passthrough(db, tmp_path) -> None:
    path = tmp_path / "corr.yaml"
    path.write_text(
        dedent(
            """
            city: testcity
            anchors:
              - {name: Start, lat: 9.0, lon: 7.0}
              - {name: End, lat: 9.1, lon: 7.1}
            destination: End
            segments:
              - sequence: 1
                from: Start
                to: End
                mode: taxi
                instruction: x
                passes_through: [Ghost]
            """
        ).lstrip()
    )
    with pytest.raises(SeedError, match="Ghost"):
        await load_file(db, path)


# --- web flow ----------------------------------------------------------


@pytest.fixture
def fake_redis() -> Iterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    prev = otp._override_client_for_tests(client)
    try:
        yield client
    finally:
        otp._override_client_for_tests(prev)


@pytest.fixture
def captured_send() -> Iterator[list[tuple[str, str]]]:
    sent: list[tuple[str, str]] = []

    async def _capture(wa_number: str, code: str) -> None:
        sent.append((wa_number, code))

    with patch("app.web.routes.sender.send_otp", AsyncMock(side_effect=_capture)):
        yield sent


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _sign_in(client: TestClient, captured_send: list[tuple[str, str]], wa: str) -> str:
    client.post("/signup", data={"name": "Tester", "wa_number": wa})
    _, code = captured_send[-1]
    canonical = "+234" + wa.lstrip("0") if wa.startswith("0") else wa
    client.post(
        "/verify", data={"kind": "signup", "wa_number": canonical, "code": code, "name": "Tester"}
    )
    return canonical


def test_web_submit_persists_passthroughs(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123450100")

    r = client.post(
        "/submit",
        data={
            "city": "testcity",
            "destination": "Banex Plaza",
            "anchor_name": ["Police Signpost", "Federal Housing", "Berger", "Banex Plaza"],
            "anchor_lat": ["8.94", "9.00", "9.04", "9.075"],
            "anchor_lon": ["7.36", "7.42", "7.46", "7.482"],
            "anchor_aliases": ["", "", "", ""],
            "seg_from": ["Police Signpost", "Berger"],
            "seg_to": ["Berger", "Banex Plaza"],
            "seg_mode": ["taxi", "taxi"],
            "seg_instruction": ["Take a taxi to Berger.", "Take another taxi to Banex."],
            "seg_transfer": ["false", "true"],
            "seg_cost_ngn": ["400", "300"],
            "seg_duration_min": ["20", "10"],
            "seg_passthroughs": ["Federal Housing", ""],
        },
    )
    assert r.status_code == 200, r.text
    assert "pending admin review" in r.text.lower()

    # Reach in and confirm the passthrough id landed.
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    async def _check() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                segs = (
                    await db.execute(select(Segment).order_by(Segment.sequence))
                ).scalars().all()
                assert len(segs) == 2
                assert len(segs[0].passthrough_anchor_ids) == 1
                assert len(segs[1].passthrough_anchor_ids) == 0
                # The passthrough should reference Federal Housing.
                fh = (
                    await db.execute(select(Anchor).where(Anchor.name == "Federal Housing"))
                ).scalar_one()
                assert segs[0].passthrough_anchor_ids[0] == fh.id
        finally:
            await engine.dispose()

    asyncio.run(_check())


# --- silence unused-symbol warning --------------------------------------

_ = Path  # keep import; unused in this file but other test modules rely on it indirectly
_ = find_corridors_by_destination
