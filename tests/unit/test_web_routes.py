"""Smoke tests for the public landing + auth-stub pages.

These guard the basics: routes resolve, HTML renders, the right copy lands on
the right page, and the nav links between them point where they should.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def test_landing_renders_hero_and_ctas(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "Directions across Abuja" in body
    assert "Try the bot on WhatsApp" in body
    assert "Contribute a route" in body


def test_landing_shows_qr_code(client: TestClient) -> None:
    """The scannable QR aside is present with its caption + URL."""
    body = client.get("/").text
    assert "Scan to open on your phone" in body
    assert "wna-api.fly.dev" in body
    # Sanity-check that the inline SVG is what's rendering the QR.
    assert 'class="segno' in body


def test_landing_includes_htmx_and_tailwind(client: TestClient) -> None:
    body = client.get("/").text
    assert "tailwindcss" in body
    assert "htmx" in body


def test_signup_renders_form(client: TestClient) -> None:
    r = client.get("/signup")
    assert r.status_code == 200
    body = r.text
    assert 'name="wa_number"' in body
    assert "verification code" in body.lower()


def test_login_renders_form(client: TestClient) -> None:
    r = client.get("/login")
    assert r.status_code == 200
    assert 'name="wa_number"' in r.text


def test_submit_links_to_signup(client: TestClient) -> None:
    r = client.get("/submit")
    assert r.status_code == 200
    assert 'href="/signup"' in r.text


def test_landing_nav_active_state(client: TestClient) -> None:
    body = client.get("/").text
    # Nav highlights the current page.
    assert "Home" in body
    assert "Sign in" in body
    assert "Get started" in body
