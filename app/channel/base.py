"""Channel adapter interface — swap Twilio (dev) and Meta (prod) behind one seam."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundRequest:
    """Provider-neutral webhook input. Endpoint builds this from the HTTP request."""

    url: str
    headers: dict[str, str]
    body: bytes


@dataclass
class InboundMessage:
    user_id: str
    text: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    @abstractmethod
    def verify(self, req: InboundRequest) -> bool: ...

    @abstractmethod
    def parse(self, req: InboundRequest) -> InboundMessage | None: ...

    @abstractmethod
    async def send_text(self, user_id: str, text: str) -> None: ...

    @abstractmethod
    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None: ...
