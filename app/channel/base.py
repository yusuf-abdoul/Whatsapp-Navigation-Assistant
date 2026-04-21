"""Channel adapter interface — swap Twilio (dev) and Meta (prod) behind one seam."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class InboundMessage:
    user_id: str
    text: str | None
    latitude: float | None
    longitude: float | None
    raw: dict[str, Any]


class ChannelAdapter(ABC):
    @abstractmethod
    def parse_webhook(self, payload: dict[str, Any]) -> InboundMessage | None: ...

    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None: ...

    @abstractmethod
    def verify_signature(self, headers: dict[str, str], body: bytes) -> bool: ...
