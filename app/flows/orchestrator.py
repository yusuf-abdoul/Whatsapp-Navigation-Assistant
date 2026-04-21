"""Flow orchestrator — routes inbound messages to the right conversation flow."""

from app.channel.base import InboundMessage


async def handle(message: InboundMessage) -> None:
    raise NotImplementedError
