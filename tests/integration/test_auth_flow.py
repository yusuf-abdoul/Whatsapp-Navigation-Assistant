"""End-to-end auth flow over the web routes.

Covers the happy path (signup → verify → session cookie set → /submit shows the
user) plus the most important error paths (invalid number, wrong code, login
without an existing account).

OTP storage uses fakeredis. OTP delivery is patched so we don't actually call
Twilio in tests — we capture the code instead.

Tests are sync (not async) because Starlette's ``TestClient`` manages its own
event loop and mixing it with ``async def`` fixtures corrupts the asyncpg
connection pool between requests.
"""

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.auth import otp
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
    captured: list[tuple[str, str]] = []

    async def _capture(wa_number: str, code: str) -> None:
        captured.append((wa_number, code))

    with patch("app.web.routes.sender.send_otp", AsyncMock(side_effect=_capture)):
        yield captured


@pytest.fixture
def client() -> Iterator[TestClient]:
    # Use as a context manager so a single event loop spans all requests in a test.
    # Without this, each request can spin up a new loop and the asyncpg pool ends
    # up with connections bound to the wrong one.
    with TestClient(app) as c:
        yield c


def test_signup_then_verify_creates_user_and_sets_session(
    fake_redis, captured_send, client
) -> None:
    r = client.post("/signup", data={"name": "Amina", "wa_number": "08123456789"})
    assert r.status_code == 200
    assert "Code sent to" in r.text
    assert "+2348123456789" in r.text
    assert captured_send, "OTP delivery should have been invoked"
    sent_to, code = captured_send[-1]
    assert sent_to == "+2348123456789"

    r = client.post(
        "/verify",
        data={"kind": "signup", "wa_number": "+2348123456789", "code": code, "name": "Amina"},
    )
    assert r.status_code == 204
    assert r.headers.get("hx-redirect") == "/submit"

    r = client.get("/submit")
    assert r.status_code == 200
    assert "Amina" in r.text


def test_signup_rejects_unparseable_phone(fake_redis, captured_send, client) -> None:
    r = client.post("/signup", data={"name": "X", "wa_number": "abc"})
    assert r.status_code == 200
    assert "valid phone number" in r.text.lower()
    assert captured_send == []


def test_verify_rejects_wrong_code(fake_redis, captured_send, client) -> None:
    client.post("/signup", data={"name": "X", "wa_number": "08123456789"})
    r = client.post(
        "/verify",
        data={"kind": "signup", "wa_number": "+2348123456789", "code": "000000", "name": "X"},
    )
    assert r.status_code == 200
    # HTML-escapes the apostrophe (didn&#39;t), so search around it instead.
    assert "match" in r.text.lower()
    assert "expired" in r.text.lower()


def test_login_for_unknown_number_is_rejected(fake_redis, captured_send, client) -> None:
    r = client.post("/login", data={"wa_number": "08199999999"})
    assert r.status_code == 200
    assert "sign up" in r.text.lower()
    assert captured_send == []


def test_login_works_for_existing_user(fake_redis, captured_send, client) -> None:
    client.post("/signup", data={"name": "Returning", "wa_number": "08123450000"})
    _, code = captured_send[-1]
    client.post(
        "/verify",
        data={"kind": "signup", "wa_number": "+2348123450000", "code": code, "name": "Returning"},
    )

    client.post("/logout")
    captured_send.clear()

    r = client.post("/login", data={"wa_number": "08123450000"})
    assert r.status_code == 200
    assert "Code sent to" in r.text
    assert captured_send, "Login should re-send an OTP"
    _, code = captured_send[-1]

    r = client.post(
        "/verify",
        data={"kind": "login", "wa_number": "+2348123450000", "code": code},
    )
    assert r.status_code == 204
    assert r.headers.get("hx-redirect") == "/submit"


def test_logout_clears_session(fake_redis, captured_send, client) -> None:
    client.post("/signup", data={"name": "X", "wa_number": "08123456700"})
    _, code = captured_send[-1]
    client.post(
        "/verify",
        data={"kind": "signup", "wa_number": "+2348123456700", "code": code, "name": "X"},
    )

    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"

    r = client.get("/submit")
    assert "Sign up to contribute" in r.text
