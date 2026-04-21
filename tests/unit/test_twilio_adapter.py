import base64
import hmac
from hashlib import sha1
from unittest.mock import MagicMock, patch
from urllib.parse import urlencode

import pytest

from app.channel.base import InboundRequest
from app.channel.twilio import TwilioAdapter

URL = "https://example.ngrok.app/webhook/twilio"
TOKEN = "test_auth_token_123"


def _sign(url: str, form: dict[str, str], token: str = TOKEN) -> str:
    data = url + "".join(f"{k}{form[k]}" for k in sorted(form))
    return base64.b64encode(
        hmac.new(token.encode(), data.encode("utf-8"), sha1).digest()
    ).decode()


def _req(form: dict[str, str], signature: str | None = None, url: str = URL) -> InboundRequest:
    headers = {"x-twilio-signature": signature} if signature is not None else {}
    return InboundRequest(url=url, headers=headers, body=urlencode(form).encode())


@pytest.fixture
def adapter() -> TwilioAdapter:
    settings = MagicMock(
        twilio_account_sid="ACtest",
        twilio_auth_token=TOKEN,
        twilio_whatsapp_from="whatsapp:+14155238886",
    )
    with patch("app.channel.twilio.get_settings", return_value=settings):
        return TwilioAdapter()


def test_verify_accepts_valid_signature(adapter: TwilioAdapter) -> None:
    form = {"From": "whatsapp:+234800", "Body": "How do I get to Wuse?"}
    assert adapter.verify(_req(form, signature=_sign(URL, form))) is True


def test_verify_rejects_tampered_body(adapter: TwilioAdapter) -> None:
    form = {"From": "whatsapp:+234800", "Body": "hello"}
    sig = _sign(URL, form)
    tampered = {"From": "whatsapp:+234800", "Body": "goodbye"}
    assert adapter.verify(_req(tampered, signature=sig)) is False


def test_verify_rejects_missing_signature(adapter: TwilioAdapter) -> None:
    form = {"From": "whatsapp:+234800", "Body": "hello"}
    assert adapter.verify(_req(form)) is False


def test_parse_text_message(adapter: TwilioAdapter) -> None:
    msg = adapter.parse(_req({"From": "whatsapp:+234800", "Body": "banex"}))
    assert msg is not None
    assert msg.user_id == "whatsapp:+234800"
    assert msg.text == "banex"
    assert msg.latitude is None


def test_parse_location_message(adapter: TwilioAdapter) -> None:
    msg = adapter.parse(
        _req({"From": "whatsapp:+234800", "Latitude": "9.0765", "Longitude": "7.3986"})
    )
    assert msg is not None
    assert msg.latitude == pytest.approx(9.0765)
    assert msg.longitude == pytest.approx(7.3986)
    assert msg.text is None


def test_parse_returns_none_without_sender(adapter: TwilioAdapter) -> None:
    assert adapter.parse(_req({"Body": "orphan"})) is None
