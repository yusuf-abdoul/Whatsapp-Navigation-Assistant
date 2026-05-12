"""Tests for the anchor-autocomplete search.

Covers both the repository function and the web endpoint that renders the
HTMX suggestion list contributors see while typing.
"""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.corridors.models import Anchor
from app.corridors.repository import search_anchors
from app.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c


def _seed(db, name: str, *, city: str = "abuja", aliases: list[str] | None = None) -> Anchor:
    a = Anchor(name=name, lat=9.0, lon=7.5, city=city, aliases=aliases or [])
    db.add(a)
    return a


# --- repository ---------------------------------------------------------


async def test_search_anchors_matches_substring_in_name(db) -> None:
    _seed(db, "Police Signpost")
    _seed(db, "Banex Plaza")
    await db.flush()
    results = await search_anchors(db, "police")
    assert [r.name for r in results] == ["Police Signpost"]


async def test_search_anchors_matches_alias(db) -> None:
    _seed(db, "Banex Plaza", aliases=["banex"])
    await db.flush()
    results = await search_anchors(db, "banex")
    assert [r.name for r in results] == ["Banex Plaza"]


async def test_search_anchors_is_case_insensitive(db) -> None:
    _seed(db, "Berger")
    await db.flush()
    assert (await search_anchors(db, "BER"))[0].name == "Berger"


async def test_search_anchors_returns_empty_for_short_query(db) -> None:
    _seed(db, "Berger")
    await db.flush()
    # Single-char queries are dropped to avoid expensive SQL on every keystroke.
    assert await search_anchors(db, "b") == []


async def test_search_anchors_scoped_by_city(db) -> None:
    _seed(db, "Berger", city="abuja")
    _seed(db, "Berger", city="lagos")
    await db.flush()
    abuja = await search_anchors(db, "berger", city="abuja")
    assert [a.city for a in abuja] == ["abuja"]


async def test_search_anchors_respects_limit(db) -> None:
    for i in range(8):
        _seed(db, f"Place {i}")
    await db.flush()
    results = await search_anchors(db, "place", limit=3)
    assert len(results) == 3


# --- web endpoint -------------------------------------------------------


def test_search_endpoint_short_query_returns_empty_body(client) -> None:
    r = client.get("/submit/anchor-search?q=b")
    assert r.status_code == 200
    assert r.text.strip() == ""


def test_search_endpoint_renders_suggestion_list(client) -> None:
    # Insert via a quick session — the autouse fixture wipes between tests so
    # this only persists for THIS test's run.
    import asyncio

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    async def _seed_one() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                db.add(
                    Anchor(
                        name="Berger",
                        lat=9.04,
                        lon=7.46,
                        city="abuja",
                        aliases=["berger junction"],
                    )
                )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_seed_one())

    r = client.get("/submit/anchor-search?q=ber&city=abuja")
    assert r.status_code == 200
    assert "Berger" in r.text
    # The data attributes the click handler reads must be present.
    assert 'data-name="Berger"' in r.text
    assert 'data-lat="9.04"' in r.text


def test_search_endpoint_returns_empty_when_no_match(client) -> None:
    r = client.get("/submit/anchor-search?q=nowherenowhere&city=abuja")
    assert r.status_code == 200
    # No `<ul>` rendered when suggestions is empty.
    assert "<ul" not in r.text
