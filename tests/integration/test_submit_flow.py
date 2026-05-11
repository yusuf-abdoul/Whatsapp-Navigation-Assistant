"""End-to-end submission flow over the web routes.

Covers the auth gate, the HTMX row partials, and the full happy/sad paths
for POST /submit.

Tests are sync — see test_auth_flow.py for the loop-scope reasoning.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import otp
from app.config import get_settings
from app.corridors.models import Corridor, Segment
from app.main import app


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


def _sign_in(client: TestClient, captured_send: list[tuple[str, str]], wa: str, name: str) -> None:
    """Helper: signup + verify, leaving the cookie set on `client`."""
    client.post("/signup", data={"name": name, "wa_number": wa})
    _, code = captured_send[-1]
    canonical = "+234" + wa.lstrip("0") if wa.startswith("0") else wa
    r = client.post(
        "/verify",
        data={"kind": "signup", "wa_number": canonical, "code": code, "name": name},
    )
    assert r.status_code == 204


# --- auth gating --------------------------------------------------------


def test_submit_page_anonymous_shows_signin_cta(client) -> None:
    r = client.get("/submit")
    assert r.status_code == 200
    assert "Sign up to contribute" in r.text


def test_submit_page_signed_in_shows_form(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123456789", "Amina")
    r = client.get("/submit")
    assert r.status_code == 200
    # Form sections are visible.
    assert "Submit a route" in r.text
    assert 'name="city"' in r.text
    assert 'name="destination"' in r.text
    assert "Add anchor" in r.text


def test_post_submit_anonymous_redirects_to_login(client) -> None:
    r = client.post("/submit", data={"city": "x"}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


# --- HTMX row partials --------------------------------------------------


def test_anchor_row_partial_returns_blank_inputs(client) -> None:
    r = client.get("/submit/anchor-row")
    assert r.status_code == 200
    assert 'name="anchor_name"' in r.text
    assert 'name="anchor_lat"' in r.text
    assert 'name="anchor_lon"' in r.text


def test_segment_row_partial_includes_mode_options(client) -> None:
    r = client.get("/submit/segment-row")
    assert r.status_code == 200
    # All allowed modes should render as options.
    for mode in ("taxi", "keke", "bike", "walk", "bus", "car", "mixed"):
        assert f'value="{mode}"' in r.text


# --- happy + sad submission paths --------------------------------------


VALID_BODY = {
    "city": "testcity",
    "destination": "Banex Plaza",
    "applicability_notes": "All day; some traffic at rush hour.",
    "anchor_name": ["Lugbe Gate", "Banex Plaza"],
    "anchor_lat": ["8.94", "9.075"],
    "anchor_lon": ["7.36", "7.482"],
    "anchor_aliases": ["lugbe", "banex, banex plaza"],
    "seg_from": ["Lugbe Gate"],
    "seg_to": ["Banex Plaza"],
    "seg_mode": ["taxi"],
    "seg_instruction": ["Take a taxi to Banex Plaza."],
    "seg_transfer": ["false"],
    "seg_cost_ngn": ["500"],
    "seg_duration_min": ["25"],
}


def test_valid_submission_inserts_pending_corridor(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123450001", "Contributor")
    r = client.post("/submit", data=VALID_BODY)
    assert r.status_code == 200, r.text
    assert "pending admin review" in r.text.lower()

    # Check it landed in the DB.
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        import asyncio

        async def _check() -> None:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                corridors = (await db.execute(select(Corridor))).scalars().all()
                assert len(corridors) == 1
                c = corridors[0]
                assert c.status == "pending"
                assert c.contributor_id is not None
                segs = (
                    await db.execute(
                        select(Segment).where(Segment.corridor_id == c.id)
                    )
                ).scalars().all()
                assert len(segs) == 1
                assert segs[0].mode == "taxi"
                assert segs[0].cost_ngn == 500

        asyncio.run(_check())
    finally:
        # dispose in the same loop the engine ran in — asyncio.run handles cleanup
        pass


def test_submission_rejects_destination_not_in_anchors(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123450002", "X")
    body = {**VALID_BODY, "destination": "Nowhere"}
    r = client.post("/submit", data=body)
    assert r.status_code == 200
    assert "destination" in r.text.lower()
    assert "not in the anchor list" in r.text.lower()


def test_submission_rejects_unknown_segment_anchor(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123450003", "X")
    body = {**VALID_BODY, "seg_from": ["Ghost"]}
    r = client.post("/submit", data=body)
    assert r.status_code == 200
    assert "ghost" in r.text.lower()


def test_submission_drops_empty_anchor_rows(fake_redis, captured_send, client) -> None:
    """A blank "+ Add" row at the end shouldn't count as a real anchor."""
    _sign_in(client, captured_send, "08123450004", "X")
    body = {
        **VALID_BODY,
        "anchor_name": ["Lugbe Gate", "", "Banex Plaza"],
        "anchor_lat": ["8.94", "", "9.075"],
        "anchor_lon": ["7.36", "", "7.482"],
        "anchor_aliases": ["", "", ""],
    }
    r = client.post("/submit", data=body)
    assert r.status_code == 200
    # No error — the empty row was silently dropped.
    assert "pending admin review" in r.text.lower()
