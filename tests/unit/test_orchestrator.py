from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest

from app.channel.base import ChannelAdapter, InboundMessage
from app.errors import ErrorKind, WNAError
from app.flows.orchestrator import handle
from app.routing.locationiq import Route
from app.session import store
from app.session.state import Place


class FakeChannel(ChannelAdapter):
    def __init__(self) -> None:
        self.texts: list[tuple[str, str]] = []
        self.options: list[tuple[str, str, list[str]]] = []

    def verify(self, req):  # type: ignore[no-untyped-def]
        return True

    def parse(self, req):  # type: ignore[no-untyped-def]
        return None

    async def send_text(self, user_id: str, text: str) -> None:
        self.texts.append((user_id, text))

    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None:
        self.options.append((user_id, prompt, options))


@pytest.fixture
async def fake_redis() -> AsyncIterator[fakeredis.aioredis.FakeRedis]:
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    original = store._client
    store._client = client
    try:
        yield client
    finally:
        store._client = original
        await client.aclose()


@pytest.fixture(autouse=True)
def _no_corridors() -> AsyncIterator[None]:
    """Default: corridor lookups return nothing so tests exercise the LocationIQ
    fallback path. Tests covering the corridor path live in tests/integration/."""
    with (
        patch("app.flows.orchestrator._lookup_corridor", AsyncMock(return_value=None)),
        patch("app.flows.orchestrator._try_corridor_reply", AsyncMock(return_value=False)),
    ):
        yield


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


USER = "whatsapp:+234800"


def _msg(
    *, text: str | None = None, lat: float | None = None, lon: float | None = None
) -> InboundMessage:
    return InboundMessage(user_id=USER, text=text, latitude=lat, longitude=lon)


async def test_direction_single_match_stores_destination_and_prompts_for_origin(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(
            return_value=[
                Place(
                    query="Jabi Lake Mall",
                    lat=9.08,
                    lon=7.42,
                    display_name="Jabi Lake Mall, Jabi, Abuja",
                )
            ]
        ),
    ):
        await handle(_msg(text="How do I get to jabi lake mall"), channel)

    assert channel.texts
    assert "Jabi Lake Mall" in channel.texts[-1][1]
    assert "share your live location" in channel.texts[-1][1].lower()

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert session.destination.lat == pytest.approx(9.08)
    assert session.origin is None


async def test_live_location_in_awaiting_origin_triggers_route(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    # Arrange: put session into AWAITING_ORIGIN.
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(
            return_value=[
                Place(query="Jabi Lake Mall", lat=9.08, lon=7.42, display_name="Jabi Lake Mall")
            ]
        ),
    ):
        await handle(_msg(text="How do I get to jabi lake mall"), channel)

    channel.texts.clear()

    with patch(
        "app.flows.orchestrator.route",
        AsyncMock(
            return_value=Route(distance_m=18500.0, duration_s=1620.0, deep_link="https://maps")
        ),
    ):
        await handle(_msg(lat=9.001, lon=7.400), channel)

    assert channel.texts
    reply = channel.texts[-1][1]
    assert "km" in reply
    assert "min" in reply
    assert "https://maps" in reply
    # Session cleared after completion.
    assert await store.get(USER) is None


async def test_text_origin_in_awaiting_state_geocodes_and_routes(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(
            return_value=[
                Place(query="Jabi Lake Mall", lat=9.08, lon=7.42, display_name="Jabi Lake Mall")
            ]
        ),
    ):
        await handle(_msg(text="How do I get to jabi lake mall"), channel)

    channel.texts.clear()

    origin_place = Place(query="lugbe", lat=8.96, lon=7.37, display_name="Lugbe, Abuja")
    with (
        patch("app.flows.orchestrator.geocode", AsyncMock(return_value=[origin_place])),
        patch(
            "app.flows.orchestrator.route",
            AsyncMock(
                return_value=Route(distance_m=18500.0, duration_s=1620.0, deep_link="https://maps")
            ),
        ),
    ):
        await handle(_msg(text="I'm currently at lugbe"), channel)

    assert channel.texts
    assert "km" in channel.texts[-1][1]
    assert await store.get(USER) is None


async def test_cancel_clears_session(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(return_value=[Place(query="X", lat=9.08, lon=7.42, display_name="X")]),
    ):
        await handle(_msg(text="How do I get to jabi"), channel)

    await handle(_msg(text="cancel"), channel)

    assert await store.get(USER) is None
    assert "cleared" in channel.texts[-1][1].lower()


async def test_new_direction_while_awaiting_origin_overrides_destination(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    first = Place(query="A", lat=9.08, lon=7.42, display_name="A")
    second = Place(query="B", lat=9.05, lon=7.45, display_name="B")

    with patch("app.flows.orchestrator.geocode", AsyncMock(return_value=[first])):
        await handle(_msg(text="How do I get to A"), channel)

    with patch("app.flows.orchestrator.geocode", AsyncMock(return_value=[second])):
        await handle(_msg(text="How do I get to B"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert session.destination.display_name == "B"


async def test_location_without_session_prompts_for_destination(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await handle(_msg(lat=9.001, lon=7.400), channel)
    assert channel.texts
    assert "where you want to go" in channel.texts[-1][1].lower()


async def test_ambiguity_prompt_when_multiple_candidates(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    candidates = [
        Place(query="banex", lat=9.07, lon=7.48, display_name="Banex Plaza"),
        Place(query="banex", lat=9.08, lon=7.49, display_name="Banex Bakery"),
    ]
    with patch("app.flows.orchestrator.geocode", AsyncMock(return_value=candidates)):
        await handle(_msg(text="How do I get to banex"), channel)

    assert channel.options
    assert len(channel.options[-1][2]) == 2


async def test_geocode_failure_replies_with_error_text(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(side_effect=WNAError(ErrorKind.PROVIDER_TIMEOUT, "slow")),
    ):
        await handle(_msg(text="How do I get to somewhere"), channel)
    assert channel.texts
    assert "slow" in channel.texts[-1][1].lower() or "try again" in channel.texts[-1][1].lower()


async def test_help_does_not_clear_session(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(return_value=[Place(query="X", lat=9.08, lon=7.42, display_name="X")]),
    ):
        await handle(_msg(text="How do I get to X"), channel)

    await handle(_msg(text="help"), channel)
    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None


# --- ambiguity follow-up (Bug 2 fix) -----------------------------------------


async def _send_ambiguity_prompt(channel: FakeChannel) -> None:
    """Helper: trigger the ambiguity path so session.pending_clarification is set."""
    candidates = [
        Place(query="banex", lat=9.07, lon=7.48, display_name="Old Banex Plaza, Wuse 2"),
        Place(query="banex", lat=9.08, lon=7.49, display_name="New Banex Plaza, Aminu Kano"),
        Place(query="banex", lat=9.09, lon=7.50, display_name="Banex Bakery, Garki"),
    ]
    with patch("app.flows.orchestrator.geocode", AsyncMock(return_value=candidates)):
        await handle(_msg(text="How do I get to banex"), channel)


async def test_ambiguity_persists_candidates_on_session(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    session = await store.get(USER)
    assert session is not None
    assert len(session.pending_clarification) == 3
    assert session.pending_clarification[0]["display_name"].startswith("Old Banex Plaza")


async def test_ambiguity_pick_by_number(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    await handle(_msg(text="2"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert session.destination.display_name == "New Banex Plaza, Aminu Kano"
    assert session.pending_clarification == []
    assert "New Banex Plaza" in channel.texts[-1][1]
    assert "share your live location" in channel.texts[-1][1].lower()


async def test_ambiguity_pick_by_first_part_label(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    # User types just the first part of one of the displayed options.
    await handle(_msg(text="Old Banex Plaza"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert "Old Banex Plaza" in session.destination.display_name
    assert session.pending_clarification == []


async def test_ambiguity_pick_is_case_insensitive(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    await handle(_msg(text="banex bakery"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert session.destination.display_name.startswith("Banex Bakery")


async def test_ambiguity_out_of_range_number_falls_through(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    """A number outside the candidate range is not a valid pick — treat as a
    fresh query, and clear the pending slot so we don't keep guessing."""
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    # No matching candidate "9" and no direction intent — falls to UNKNOWN_INTENT.
    await handle(_msg(text="9"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.pending_clarification == []  # cleared after failed pick


async def test_new_direction_after_ambiguity_clears_pending(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    # Single-match destination so we get a clean "Found X" path.
    with patch(
        "app.flows.orchestrator.geocode",
        AsyncMock(return_value=[Place(query="jabi", lat=9.08, lon=7.42, display_name="Jabi Mall")]),
    ):
        await handle(_msg(text="How do I get to jabi"), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.destination is not None
    assert session.destination.display_name == "Jabi Mall"
    assert session.pending_clarification == []


async def test_live_location_after_ambiguity_clears_pending(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    # User sends a live location instead of picking — clear pending, fall to
    # the "I don't know where you want to go yet" branch.
    await handle(_msg(lat=9.001, lon=7.400), channel)

    session = await store.get(USER)
    assert session is not None
    assert session.pending_clarification == []
    assert "where you want to go" in channel.texts[-1][1].lower()


async def test_cancel_after_ambiguity_clears_session_entirely(
    fake_redis: fakeredis.aioredis.FakeRedis, channel: FakeChannel
) -> None:
    await _send_ambiguity_prompt(channel)
    channel.texts.clear()

    await handle(_msg(text="cancel"), channel)

    assert await store.get(USER) is None  # session deleted, pending gone
