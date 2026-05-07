"""OTP delivery via the configured channel (Twilio in dev, Meta later).

Reuses the channel adapter so the dev sandbox just works. Production OTP via
Meta Direct will need a verified WhatsApp business template — flagged as
follow-up, not blocking the MVP.
"""

from __future__ import annotations

import structlog

from app.channel import get_channel

log = structlog.get_logger("auth.sender")


async def send_otp(wa_number: str, code: str) -> None:
    """Send the 6-digit code to the user's WhatsApp number.

    The Twilio sandbox requires the recipient to have joined first; sends to
    unjoined numbers will fail. We log warnings but don't raise — the caller
    has already reported success to the user (the code is in Redis), and a
    delivery failure surfaces when the user can't enter a code that arrived.
    """
    channel = get_channel()
    text = f"Your WNA verification code is {code}. It expires in 5 minutes."
    # Twilio expects a "whatsapp:+number" recipient.
    recipient = wa_number if wa_number.startswith("whatsapp:") else f"whatsapp:{wa_number}"
    try:
        await channel.send_text(recipient, text)
    except Exception as e:  # last-mile delivery is best-effort
        log.warning("otp_send_failed", wa_number=wa_number, error=str(e))
