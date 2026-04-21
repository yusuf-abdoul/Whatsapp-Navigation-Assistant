"""Twilio WhatsApp sandbox adapter — for development and Meta-verification fallback."""

from typing import Any

from app.channel.base import ChannelAdapter, InboundMessage


class TwilioAdapter(ChannelAdapter):
    def parse_webhook(self, payload: dict[str, Any]) -> InboundMessage | None:
        raise NotImplementedError

    async def send_text(self, user_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None:
        raise NotImplementedError

    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool:
        raise NotImplementedError
