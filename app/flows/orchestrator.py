"""Flow orchestrator — routes inbound messages to the right conversation flow.

Sprint 1: echo handler. Sprint 2 replaces this with intent + flow dispatch.
"""

from app.analytics.events import Event, emit
from app.channel.base import ChannelAdapter, InboundMessage


async def handle(message: InboundMessage, channel: ChannelAdapter) -> None:
    emit(Event.FIRST_CONTACT_RECEIVED, user_id=message.user_id)

    if message.text:
        reply = f"Echo: {message.text}"
    elif message.latitude is not None and message.longitude is not None:
        reply = f"Got your location: {message.latitude:.4f}, {message.longitude:.4f}"
    else:
        reply = "I didn't catch that. Try: 'How do I get to Jabi Lake Mall?'"

    await channel.send_text(message.user_id, reply)
