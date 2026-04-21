"""Meta WhatsApp Cloud API adapter — production target.

Implementation deferred until Meta business verification clears.
See docs/decisions/0002-meta-direct-with-twilio-dev-fallback.md.
"""

from app.channel.base import ChannelAdapter, InboundMessage, InboundRequest
from app.config import get_settings


class MetaAdapter(ChannelAdapter):
    def __init__(self) -> None:
        s = get_settings()
        self._phone_number_id = s.meta_phone_number_id
        self._access_token = s.meta_access_token
        self._app_secret = s.meta_app_secret

    def verify(self, req: InboundRequest) -> bool:
        raise NotImplementedError

    def parse(self, req: InboundRequest) -> InboundMessage | None:
        raise NotImplementedError

    async def send_text(self, user_id: str, text: str) -> None:
        raise NotImplementedError

    async def send_options(self, user_id: str, prompt: str, options: list[str]) -> None:
        raise NotImplementedError
