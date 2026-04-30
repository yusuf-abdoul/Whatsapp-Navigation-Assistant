"""End-to-end integration tests for the corridor reply path.

Seeds a real corridor in Postgres, drives a fake WhatsApp conversation through
the orchestrator, and asserts the user receives the curated numbered-step reply
(not the LocationIQ-only fallback).
"""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.channel.base import ChannelAdapter, InboundMessage
from app.config import get_settings
from app.corridors.models import Anchor, Corridor, Segment
from app.flows.orchestrator import handle
from app.routing.locationiq import Route
from app.session import store


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


USER = "whatsapp:+234800"


def _msg(*, text: str | None = None, lat: float | None = None, lon: float | None = None):
    return InboundMessage(user_id=USER, text=text, latitude=lat, longitude=lon)


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


async def _seed_lugbe_to_banex() -> None:
    """Seed Lugbe→Banex via a dedicated engine so data is durably committed
    and visible to the orchestrator's own session factory."""
    eng = create_async_engine(get_settings().database_url)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    async with factory() as db:
        police = Anchor(
            name="Police Signpost",
            lat=8.940, lon=7.360, city="abuja",
            aliases=["police signboard"],
        )
        car_wash = Anchor(name="Car Wash", lat=8.970, lon=7.390, city="abuja", aliases=[])
        fed = Anchor(name="Federal Housing Bridge", lat=9.000, lon=7.420, city="abuja", aliases=[])
        berger = Anchor(name="Berger", lat=9.040, lon=7.460, city="abuja", aliases=[])
        banex = Anchor(
            name="Banex Plaza",
            lat=9.075, lon=7.482, city="abuja",
            aliases=["banex"],
        )
        db.add_all([police, car_wash, fed, berger, banex])
        await db.flush()

        c = Corridor(destination_anchor_id=banex.id, status="approved")
        db.add(c)
        await db.flush()

        db.add_all([
            Segment(corridor_id=c.id, sequence=1, from_anchor_id=police.id,
                    to_anchor_id=car_wash.id, mode="taxi",
                    instruction="Take a taxi heading Berger.",
                    cost_ngn=200, duration_min=5),
            Segment(corridor_id=c.id, sequence=2, from_anchor_id=car_wash.id,
                    to_anchor_id=fed.id, mode="taxi",
                    instruction="Stay on past Federal Housing Bridge.",
                    duration_min=5),
            Segment(corridor_id=c.id, sequence=3, from_anchor_id=fed.id,
                    to_anchor_id=berger.id, mode="taxi",
                    instruction="Stay on until Berger junction.",
                    cost_ngn=200, duration_min=10),
            Segment(corridor_id=c.id, sequence=4, from_anchor_id=berger.id,
                    to_anchor_id=banex.id, mode="taxi",
                    instruction="Take another taxi from Berger to Banex Plaza.",
                    cost_ngn=300, duration_min=10),
        ])
        await db.commit()
    await eng.dispose()


async def test_direction_with_known_corridor_replies_with_numbered_steps(fake_redis) -> None:
    await _seed_lugbe_to_banex()
    channel = FakeChannel()

    # Destination resolution should hit the corridor — no LocationIQ geocode call needed.
    await handle(_msg(text="How do I get to banex"), channel)
    assert channel.texts
    first_reply = channel.texts[-1][1]
    assert "Banex Plaza" in first_reply
    assert "share your live location" in first_reply.lower()

    channel.texts.clear()

    # User shares location near Police Signpost (corridor's first anchor).
    # Mock LocationIQ route (used for the distance/ETA footer).
    with patch(
        "app.flows.orchestrator.route",
        AsyncMock(return_value=Route(distance_m=18500.0, duration_s=1620.0, deep_link="https://maps")),
    ):
        await handle(_msg(lat=8.941, lon=7.361), channel)

    assert channel.texts
    reply = channel.texts[-1][1]
    # All four numbered corridor steps.
    assert "1." in reply
    assert "2." in reply
    assert "3." in reply
    assert "4." in reply
    assert "taxi" in reply.lower()
    assert "Banex Plaza" in reply
    # Footer with distance/ETA from LocationIQ.
    assert "km" in reply
    assert "min" in reply
    assert "https://maps" in reply
    # Session cleared after completion.
    assert await store.get(USER) is None


async def test_corridor_reply_clips_to_join_point(fake_redis) -> None:
    """User joining mid-corridor (near Berger) only gets the final step."""
    await _seed_lugbe_to_banex()
    channel = FakeChannel()

    await handle(_msg(text="How do I get to banex"), channel)
    channel.texts.clear()

    # Coordinates near Berger (9.040, 7.460).
    with patch(
        "app.flows.orchestrator.route",
        AsyncMock(return_value=Route(distance_m=4000.0, duration_s=600.0, deep_link="https://m")),
    ):
        await handle(_msg(lat=9.0405, lon=7.4605), channel)

    reply = channel.texts[-1][1]
    assert "1." in reply
    assert "2." not in reply  # only the final step from this join point
    assert "Banex Plaza" in reply


async def test_corridor_falls_back_to_locationiq_when_user_too_far(fake_redis) -> None:
    """User far from any corridor anchor → LocationIQ-only reply, not corridor steps."""
    await _seed_lugbe_to_banex()
    channel = FakeChannel()

    await handle(_msg(text="How do I get to banex"), channel)
    channel.texts.clear()

    # ~50km offset from every corridor anchor — far beyond the 2km join radius.
    with patch(
        "app.flows.orchestrator.route",
        AsyncMock(return_value=Route(distance_m=50000.0, duration_s=3600.0, deep_link="https://x")),
    ):
        await handle(_msg(lat=8.500, lon=7.000), channel)

    reply = channel.texts[-1][1]
    assert "1." not in reply
    assert "Banex Plaza" in reply
    assert "km" in reply
    assert "https://x" in reply
