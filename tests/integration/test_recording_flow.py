"""End-to-end tests for live trip-capture via WhatsApp.

Walks the bot through a full recording session and verifies the result
lands in the corridors table as a pending submission — the same schema
the web form produces.

Includes the happy path, cancellation mid-flow, and the input edge cases
that broke during dogfooding (bad leg syntax, missing live location).
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from sqlalchemy import select

from app.abuse import limits as abuse_limits
from app.channel.base import ChannelAdapter, InboundMessage
from app.corridors.models import Anchor, Corridor, Segment
from app.flows.orchestrator import handle
from app.flows.recording import _parse_leg, _strip_anchor_prefix
from app.session import store


class FakeChannel(ChannelAdapter):
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.options: list[tuple[str, str, list[str]]] = []

    def verify(self, req) -> bool:  # type: ignore[no-untyped-def]
        return True

    def parse(self, req):  # type: ignore[no-untyped-def]
        return None

    async def send_text(self, user_id: str, text: str) -> None:
        self.texts.append((user_id, text))

    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None:
        self.options.append((user_id, prompt, options))


USER = "whatsapp:+2348100000001"


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    store_prev = store._client
    abuse_prev = abuse_limits._client
    store._client = client
    abuse_limits._client = client
    try:
        yield client
    finally:
        store._client = store_prev
        abuse_limits._client = abuse_prev
        await client.aclose()


@pytest.fixture(autouse=True)
def _no_corridors_for_query_path() -> AsyncIterator[None]:
    """Recording tests shouldn't trip the corridor-lookup path the orchestrator
    runs for normal queries — patch the helpers to no-op."""
    with (
        patch("app.flows.orchestrator._lookup_corridor", AsyncMock(return_value=None)),
        patch("app.flows.orchestrator._try_corridor_reply", AsyncMock(return_value=False)),
    ):
        yield


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


def _text(t: str) -> InboundMessage:
    return InboundMessage(user_id=USER, text=t)


def _loc(lat: float, lon: float) -> InboundMessage:
    return InboundMessage(user_id=USER, latitude=lat, longitude=lon)


# --- leg parser unit tests ----------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("bike 200", ("bike", 200)),
        ("taxi 400", ("taxi", 400)),
        ("keke 300", ("keke", 300)),
        ("400 taxi", ("taxi", 400)),
        ("bike no cost", ("bike", None)),
        ("Taxi 500 naira", ("taxi", 500)),
    ],
)
def test_parse_leg_accepts_common_shapes(text: str, expected: tuple[str, int | None]) -> None:
    assert _parse_leg(text) == expected


@pytest.mark.parametrize("text", ["", "200", "hello", "naira"])
def test_parse_leg_rejects_without_mode(text: str) -> None:
    assert _parse_leg(text) is None


def test_strip_anchor_prefix() -> None:
    assert _strip_anchor_prefix("now at Police Signpost") == "Police Signpost"
    assert _strip_anchor_prefix("at Berger") == "Berger"
    assert _strip_anchor_prefix("arrived at Banex") == "Banex"
    assert _strip_anchor_prefix("Just a name") == "Just a name"


# --- happy path: full recording → pending corridor in DB ----------------


async def test_full_recording_persists_pending_corridor(fake_redis, channel) -> None:
    # Step 1: start
    await handle(_text("start trip"), channel)
    assert "destination" in channel.texts[-1][1].lower()

    # Step 2: destination
    await handle(_text("Banex Plaza"), channel)
    assert "where you are now" in channel.texts[-1][1].lower()

    # Step 3: origin name + location
    await handle(_text("AMAC Market"), channel)
    assert "share your live location" in channel.texts[-1][1].lower()
    await handle(_loc(8.95, 7.36), channel)
    assert "anchor 1" in channel.texts[-1][1].lower()

    # Step 4: leg 1 info + next anchor
    await handle(_text("bike 200"), channel)
    await handle(_text("now at Police Signpost"), channel)
    await handle(_loc(8.94, 7.36), channel)
    assert "anchor 2" in channel.texts[-1][1].lower()
    await handle(_text("changed"), channel)

    # Step 5: leg 2 info + next anchor
    await handle(_text("taxi 400"), channel)
    await handle(_text("now at Berger"), channel)
    await handle(_loc(9.04, 7.46), channel)
    await handle(_text("changed"), channel)

    # Step 6: leg 3 info + final anchor
    await handle(_text("taxi 300"), channel)
    await handle(_text("now at Banex Plaza"), channel)
    await handle(_loc(9.075, 7.482), channel)
    await handle(_text("same"), channel)

    # Step 7: end → summary → confirm
    await handle(_text("end"), channel)
    summary = channel.texts[-1][1]
    assert "Recorded:" in summary
    assert "AMAC Market" in summary and "Banex Plaza" in summary
    assert "₦200" in summary and "₦400" in summary and "₦300" in summary

    await handle(_text("confirm"), channel)
    assert "pending review" in channel.texts[-1][1].lower()

    # Now verify the DB.
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            [corridor] = (await db.execute(select(Corridor))).scalars().all()
            assert corridor.status == "pending"
            assert corridor.contributor_id == USER

            segments = (
                (
                    await db.execute(
                        select(Segment)
                        .where(Segment.corridor_id == corridor.id)
                        .order_by(Segment.sequence)
                    )
                )
                .scalars()
                .all()
            )
            assert [s.mode for s in segments] == ["bike", "taxi", "taxi"]
            assert [s.cost_ngn for s in segments] == [200, 400, 300]

            anchors = (await db.execute(select(Anchor))).scalars().all()
            assert {a.name for a in anchors} == {
                "AMAC Market",
                "Police Signpost",
                "Berger",
                "Banex Plaza",
            }
    finally:
        await engine.dispose()

    # Session was cleared after submit.
    state = await store.get(USER)
    assert state is None or state.recording is None


# --- cancellation -------------------------------------------------------


async def test_cancel_mid_recording_clears_session(fake_redis, channel) -> None:
    await handle(_text("start trip"), channel)
    await handle(_text("Banex"), channel)
    await handle(_text("AMAC"), channel)
    await handle(_loc(8.95, 7.36), channel)

    state = await store.get(USER)
    assert state is not None
    assert state.recording is not None

    await handle(_text("cancel"), channel)
    assert await store.get(USER) is None


# --- bad input handling -------------------------------------------------


async def test_unparseable_leg_info_keeps_user_at_same_step(fake_redis, channel) -> None:
    await handle(_text("start trip"), channel)
    await handle(_text("Banex"), channel)
    await handle(_text("AMAC"), channel)
    await handle(_loc(8.95, 7.36), channel)

    # Garbage leg — should be rejected.
    channel.texts.clear()
    await handle(_text("blah blah"), channel)
    assert "didn't catch" in channel.texts[-1][1].lower()

    # Still awaiting leg_info — a proper one should now advance us.
    await handle(_text("bike 200"), channel)
    state = await store.get(USER)
    assert state is not None and state.recording is not None
    assert state.recording.awaiting == "next_anchor_name"


async def test_text_when_location_expected_reprompts(fake_redis, channel) -> None:
    await handle(_text("start trip"), channel)
    await handle(_text("Banex"), channel)
    await handle(_text("AMAC"), channel)
    # Bot now wants a location share; send text instead.
    channel.texts.clear()
    await handle(_text("here"), channel)
    assert "live location" in channel.texts[-1][1].lower()


async def test_end_without_enough_anchors_is_rejected(fake_redis, channel) -> None:
    await handle(_text("start trip"), channel)
    await handle(_text("Banex"), channel)
    await handle(_text("AMAC"), channel)
    await handle(_loc(8.95, 7.36), channel)
    # Only one anchor recorded — too little to be a corridor.
    channel.texts.clear()
    await handle(_text("end"), channel)
    assert "not enough" in channel.texts[-1][1].lower()
    assert await store.get(USER) is None or (await store.get(USER)).recording is None


async def test_end_before_start_is_a_no_op(fake_redis, channel) -> None:
    await handle(_text("end"), channel)
    assert "not recording" in channel.texts[-1][1].lower()


# --- normal query intents are blocked mid-recording ---------------------


async def test_direction_query_during_recording_is_routed_to_recording_handler(
    fake_redis, channel
) -> None:
    """While recording, the user's text is interpreted in the recording
    context — a stray 'How do I get to X' shouldn't kick off a fresh
    direction lookup."""
    await handle(_text("start trip"), channel)
    await handle(_text("Banex"), channel)  # destination
    # Bot expects origin_name. Sending a direction query — it's used as the
    # next free-form text input (origin_name), not a fresh direction lookup.
    await handle(_text("How do I get to Lugbe"), channel)
    state = await store.get(USER)
    assert state is not None and state.recording is not None
    # The text became our origin name — we're now waiting for the location.
    assert state.recording.awaiting == "origin_location"
