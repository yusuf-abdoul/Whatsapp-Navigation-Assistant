"""End-to-end tests for the admin review console.

Covers the gate (anonymous and non-admin both redirect), the queue view,
the detail view, approve / reject, and the anchor-coord edit — which is
the SOLE path for changing lat/lon since Phase 2c locked the contribution
flow against overwriting existing coordinates.
"""

import asyncio
import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import otp
from app.config import get_settings
from app.corridors.models import Anchor, Corridor
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


def _sign_in(client: TestClient, captured_send: list[tuple[str, str]], wa: str, name: str) -> str:
    """Helper: signup + verify. Returns the canonical wa_number."""
    client.post("/signup", data={"name": name, "wa_number": wa})
    _, code = captured_send[-1]
    canonical = "+234" + wa.lstrip("0") if wa.startswith("0") else wa
    r = client.post(
        "/verify",
        data={"kind": "signup", "wa_number": canonical, "code": code, "name": name},
    )
    assert r.status_code == 204
    return canonical


def _promote_to_admin(wa_number: str) -> None:
    """Flip is_admin on a user. Tests can call this between signup and the
    NEXT login so the session picks it up — or simply manipulate the row and
    have the test do a fresh login."""

    async def _run() -> None:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                # Local import to avoid touching the model at module load.
                from app.users.models import User

                user = (
                    await db.execute(select(User).where(User.wa_number == wa_number))
                ).scalar_one()
                user.is_admin = True
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(_run())


def _seed_pending_corridor() -> tuple[uuid.UUID, uuid.UUID]:
    """Insert one approved-anchors + pending corridor combo for tests to
    operate on. Returns (corridor_id, anchor_to_edit_id)."""

    async def _run() -> tuple[uuid.UUID, uuid.UUID]:
        from app.corridors.models import Corridor as _Corridor
        from app.corridors.models import Segment as _Segment

        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                a = Anchor(name="Origin", city="testcity", lat=9.0, lon=7.0, aliases=[])
                b = Anchor(name="Destination", city="testcity", lat=9.1, lon=7.1, aliases=[])
                db.add_all([a, b])
                await db.flush()
                c = _Corridor(destination_anchor_id=b.id, status="pending", contributor_id="anon")
                db.add(c)
                await db.flush()
                db.add(
                    _Segment(
                        corridor_id=c.id,
                        sequence=1,
                        from_anchor_id=a.id,
                        to_anchor_id=b.id,
                        mode="taxi",
                        instruction="Take a taxi to Destination.",
                    )
                )
                await db.commit()
                return c.id, a.id
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _fetch_anchor(anchor_id: uuid.UUID) -> Anchor:
    async def _run() -> Anchor:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return (await db.execute(select(Anchor).where(Anchor.id == anchor_id))).scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


def _fetch_corridor(corridor_id: uuid.UUID) -> Corridor:
    async def _run() -> Corridor:
        engine = create_async_engine(get_settings().database_url)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                return (
                    await db.execute(select(Corridor).where(Corridor.id == corridor_id))
                ).scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(_run())


# --- gate ---------------------------------------------------------------


def test_admin_queue_anonymous_redirects_to_login(client) -> None:
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_admin_queue_non_admin_user_redirects_to_login(fake_redis, captured_send, client) -> None:
    _sign_in(client, captured_send, "08123456001", "Regular")
    r = client.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


def test_verify_redirects_admin_to_admin_queue(fake_redis, captured_send, client) -> None:
    """After OTP verify, admins land on /admin, not /submit."""
    wa = _sign_in(client, captured_send, "08123456099", "Admin Landing")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456099"})
    _, code = captured_send[-1]
    r = client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})
    assert r.status_code == 204
    assert r.headers.get("HX-Redirect") == "/admin"


def test_verify_redirects_regular_user_to_submit(fake_redis, captured_send, client) -> None:
    """Non-admin sign-in still lands on /submit."""
    client.post("/signup", data={"name": "Regular", "wa_number": "08123456098"})
    _, code = captured_send[-1]
    r = client.post(
        "/verify",
        data={"kind": "signup", "wa_number": "+2348123456098", "code": code, "name": "Regular"},
    )
    assert r.status_code == 204
    assert r.headers.get("HX-Redirect") == "/submit"


# --- happy path: queue + approve + reject + edit coords -----------------


def test_admin_can_view_queue_and_detail(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456010", "Admin User")
    _promote_to_admin(wa)
    # Re-login so the session picks up is_admin.
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456010"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    corridor_id, _ = _seed_pending_corridor()

    r = client.get("/admin")
    assert r.status_code == 200
    assert "Destination" in r.text

    r = client.get(f"/admin/corridors/{corridor_id}")
    assert r.status_code == 200
    assert "Take a taxi to Destination" in r.text
    assert "Approve" in r.text
    assert "Reject" in r.text


def test_admin_approve_sets_status_and_approved_at(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456011", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456011"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    corridor_id, _ = _seed_pending_corridor()

    r = client.post(f"/admin/corridors/{corridor_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"

    fresh = _fetch_corridor(corridor_id)
    assert fresh.status == "approved"
    assert fresh.approved_at is not None


def test_admin_reject_sets_status(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456012", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456012"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    corridor_id, _ = _seed_pending_corridor()

    r = client.post(f"/admin/corridors/{corridor_id}/reject", follow_redirects=False)
    assert r.status_code == 303
    fresh = _fetch_corridor(corridor_id)
    assert fresh.status == "rejected"


def test_admin_can_correct_anchor_coords(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456013", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456013"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    corridor_id, anchor_id = _seed_pending_corridor()

    r = client.post(
        f"/admin/anchors/{anchor_id}",
        data={"lat": "9.555", "lon": "7.555", "return_to": f"/admin/corridors/{corridor_id}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/corridors/{corridor_id}"

    fresh = _fetch_anchor(anchor_id)
    assert fresh.lat == pytest.approx(9.555)
    assert fresh.lon == pytest.approx(7.555)


def test_admin_anchor_edit_rejects_out_of_range(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456014", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456014"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    _, anchor_id = _seed_pending_corridor()

    r = client.post(
        f"/admin/anchors/{anchor_id}", data={"lat": "99", "lon": "7"}, follow_redirects=False
    )
    assert r.status_code == 400


def test_admin_corridor_detail_404_for_unknown_id(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456015", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456015"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    r = client.get(f"/admin/corridors/{uuid.uuid4()}", follow_redirects=False)
    assert r.status_code == 404


def test_admin_approve_is_idempotent_on_already_approved(fake_redis, captured_send, client) -> None:
    wa = _sign_in(client, captured_send, "08123456016", "A")
    _promote_to_admin(wa)
    client.post("/logout")
    client.post("/login", data={"wa_number": "08123456016"})
    _, code = captured_send[-1]
    client.post("/verify", data={"kind": "login", "wa_number": wa, "code": code})

    corridor_id, _ = _seed_pending_corridor()
    client.post(f"/admin/corridors/{corridor_id}/approve")
    first = _fetch_corridor(corridor_id)
    approved_at_first = first.approved_at

    # Approving again is a no-op — status stays approved, approved_at unchanged.
    r = client.post(f"/admin/corridors/{corridor_id}/approve", follow_redirects=False)
    assert r.status_code == 303
    second = _fetch_corridor(corridor_id)
    assert second.status == "approved"
    assert second.approved_at == approved_at_first
