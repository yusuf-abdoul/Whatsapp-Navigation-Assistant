from functools import lru_cache

from app.channel.base import ChannelAdapter, InboundMessage, InboundRequest
from app.channel.meta import MetaAdapter
from app.channel.twilio import TwilioAdapter
from app.config import get_settings

__all__ = ["ChannelAdapter", "InboundMessage", "InboundRequest", "get_channel"]


@lru_cache
def get_channel() -> ChannelAdapter:
    provider = get_settings().channel_provider
    if provider == "twilio":
        return TwilioAdapter()
    if provider == "meta":
        return MetaAdapter()
    raise ValueError(f"unknown channel provider: {provider}")
