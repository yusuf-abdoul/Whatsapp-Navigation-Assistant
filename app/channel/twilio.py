"""Twilio WhatsApp sandbox adapter — development target.

Signature algorithm: base64(hmac_sha1(auth_token, url + sorted(k+v for k,v in form))).
See https://www.twilio.com/docs/usage/webhooks/webhooks-security.
"""

import base64
import hmac
from hashlib import sha1
from urllib.parse import parse_qsl

import httpx

from app.channel.base import ChannelAdapter, InboundMessage, InboundRequest
from app.config import get_settings


class TwilioAdapter(ChannelAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._sid = s.twilio_account_sid
        self._token = s.twilio_auth_token
        self._from = s.twilio_whatsapp_from

    @staticmethod
    def _form(body: bytes) -> dict[str, str]:
        return dict(parse_qsl(body.decode("utf-8"), keep_blank_values=True))

    def verify(self, req: InboundRequest) -> bool:
        if not self._token:
            return False
        signature = req.headers.get("x-twilio-signature", "")
        if not signature:
            return False
        form = self._form(req.body)
        data = req.url + "".join(f"{k}{form[k]}" for k in sorted(form))
        expected = base64.b64encode(
            hmac.new(self._token.encode(), data.encode("utf-8"), sha1).digest()
        ).decode()
        return hmac.compare_digest(expected, signature)

    def parse(self, req: InboundRequest) -> InboundMessage | None:
        form = self._form(req.body)
        sender = form.get("From")
        if not sender:
            return None
        lat = form.get("Latitude")
        lon = form.get("Longitude")
        return InboundMessage(
            user_id=sender,
            text=form.get("Body") or None,
            latitude=float(lat) if lat else None,
            longitude=float(lon) if lon else None,
            raw=dict(form),
        )

    async def send_text(self, user_id: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json",
                data={"From": self._from, "To": user_id, "Body": text},
                auth=(self._sid, self._token),
            )
            r.raise_for_status()

    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None:
        numbered = "\n".join(f"{i + 1}. {opt}" for i, opt in enumerate(options))
        await self.send_text(user_id, f"{prompt}\n\n{numbered}")
