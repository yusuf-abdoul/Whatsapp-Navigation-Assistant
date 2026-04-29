"""Flow orchestrator — state-aware conversation dispatch.

State machine (derived from session contents, no explicit flag):
  IDLE → DIRECTION(X) → geocode → destination set → AWAITING_ORIGIN
  AWAITING_ORIGIN + live location → route → reply → IDLE (session cleared)
  AWAITING_ORIGIN + text(Y)       → geocode(Y) as origin → route → reply → IDLE
  any state + new DIRECTION       → overwrite destination, re-enter AWAITING_ORIGIN
  any state + CANCEL              → clear session
"""

import re

from app.analytics.events import Event, emit
from app.channel.base import ChannelAdapter, InboundMessage
from app.errors import ErrorKind, WNAError
from app.formatting.responses import (
    format_ambiguity,
    format_error,
    format_route,
    short_name,
)
from app.intent.detector import detect
from app.intent.types import Intent
from app.resolver.locationiq import geocode
from app.routing.locationiq import route
from app.session import store
from app.session.state import Place, SessionState

_HELP_TEXT = (
    "Hi! I help you get around Abuja.\n\n"
    "Try:\n"
    "• 'How do I get to Jabi Lake Mall?'\n"
    "• 'Pharmacies near me'\n\n"
    "To share your location: tap the paperclip (📎) → Location → Send your current location.\n"
    "Say 'cancel' to start over."
)

_CANCEL_TEXT = "Cleared. Ask me where you want to go."

_ORIGIN_PREFIX = re.compile(
    r"^\s*(?:"
    r"i\s*'?\s*m\s+(?:currently\s+)?(?:at|near|in)"
    r"|i\s+am\s+(?:currently\s+)?(?:at|near|in)"
    r"|from"
    r"|at"
    r"|near"
    r"|my\s+location\s+is"
    r")\s+",
    re.IGNORECASE,
)


async def handle(message: InboundMessage, channel: ChannelAdapter) -> None:
    emit(Event.FIRST_CONTACT_RECEIVED, user_id=message.user_id)

    session = await store.get(message.user_id) or SessionState(user_id=message.user_id)

    # Location share always means "this is my origin" — it can't be anything else.
    if message.latitude is not None and message.longitude is not None:
        await _handle_location(session, message.latitude, message.longitude, channel)
        return

    if not message.text:
        await channel.send_text(message.user_id, format_error(ErrorKind.UNKNOWN_INTENT))
        return

    result = detect(message.text)

    if result.intent == Intent.CANCEL:
        await store.delete(session.user_id)
        emit(Event.SESSION_ABANDONED, user_id=session.user_id, reason="user_cancel")
        await channel.send_text(session.user_id, _CANCEL_TEXT)
        return

    if result.intent == Intent.HELP:
        await channel.send_text(session.user_id, _HELP_TEXT)
        return

    if result.intent == Intent.DIRECTION and result.query:
        await _handle_direction(session, result.query, channel)
        return

    # Non-command text while awaiting origin → treat as origin statement.
    if _awaiting_origin(session):
        await _handle_origin_text(session, message.text, channel)
        return

    if result.intent == Intent.NEARBY and result.query:
        await channel.send_text(
            session.user_id,
            f"I can help you find {result.query} nearby. Share your live location to continue.",
        )
        return

    await channel.send_text(session.user_id, format_error(ErrorKind.UNKNOWN_INTENT))


def _awaiting_origin(session: SessionState) -> bool:
    return session.destination is not None and session.origin is None


async def _handle_direction(session: SessionState, query: str, channel: ChannelAdapter) -> None:
    try:
        candidates = await geocode(query)
    except WNAError as e:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=query, kind=e.kind.value)
        await channel.send_text(session.user_id, format_error(e.kind))
        return

    if not candidates:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=query, kind="not_found")
        await channel.send_text(
            session.user_id,
            f"I couldn't find '{query}' in Abuja. Try a fuller name or a nearby landmark.",
        )
        return

    if len(candidates) > 1:
        emit(
            Event.AMBIGUITY_PROMPT_SENT,
            user_id=session.user_id,
            query=query,
            count=len(candidates),
        )
        prompt, options = format_ambiguity(query, candidates[:3])
        await channel.send_options(session.user_id, prompt, options)
        return

    place = candidates[0]
    emit(Event.GEOCODE_SUCCESS, user_id=session.user_id, query=query)
    emit(Event.DESTINATION_RECEIVED, user_id=session.user_id, query=query)

    session.destination = place
    session.origin = None
    session.last_intent = Intent.DIRECTION
    await store.put(session)

    emit(Event.ORIGIN_REQUESTED, user_id=session.user_id)
    name = short_name(place.display_name) or query
    await channel.send_text(
        session.user_id,
        f"Found {name}. Now share your live location, "
        "or tell me where you're starting from (e.g. 'I'm at Lugbe').",
    )


async def _handle_location(
    session: SessionState, lat: float, lon: float, channel: ChannelAdapter
) -> None:
    if not _awaiting_origin(session) or session.destination is None:
        await channel.send_text(
            session.user_id,
            "Got your location. Tell me where you want to go: 'How do I get to Jabi Lake Mall?'",
        )
        return

    emit(Event.ORIGIN_RECEIVED_LOCATION, user_id=session.user_id)
    origin = Place(query="current location", lat=lat, lon=lon, display_name="your location")
    await _route_and_reply(session, origin, channel)


async def _handle_origin_text(session: SessionState, text: str, channel: ChannelAdapter) -> None:
    cleaned = _strip_origin_prefix(text)

    try:
        candidates = await geocode(cleaned)
    except WNAError as e:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=cleaned, kind=e.kind.value)
        await channel.send_text(session.user_id, format_error(e.kind))
        return

    if not candidates:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=cleaned, kind="not_found")
        await channel.send_text(
            session.user_id,
            f"I couldn't find '{cleaned}' as a starting point. "
            "Try sharing your live location instead.",
        )
        return

    emit(Event.ORIGIN_RECEIVED_TEXT, user_id=session.user_id)
    await _route_and_reply(session, candidates[0], channel)


async def _route_and_reply(session: SessionState, origin: Place, channel: ChannelAdapter) -> None:
    dest = session.destination
    if dest is None or dest.lat is None or dest.lon is None:
        await channel.send_text(session.user_id, format_error(ErrorKind.UNKNOWN_INTENT))
        return
    if origin.lat is None or origin.lon is None:
        await channel.send_text(session.user_id, format_error(ErrorKind.GEOCODE_FAIL))
        return

    try:
        r = await route(
            origin_lat=origin.lat,
            origin_lon=origin.lon,
            dest_lat=dest.lat,
            dest_lon=dest.lon,
        )
    except WNAError as e:
        emit(Event.ROUTE_FAILURE, user_id=session.user_id, kind=e.kind.value)
        await channel.send_text(session.user_id, format_error(e.kind))
        return

    emit(
        Event.ROUTE_SUCCESS,
        user_id=session.user_id,
        distance_m=r.distance_m,
        duration_s=r.duration_s,
    )
    emit(Event.SESSION_COMPLETED, user_id=session.user_id)

    await channel.send_text(session.user_id, format_route(dest, r))
    await store.delete(session.user_id)


def _strip_origin_prefix(text: str) -> str:
    cleaned = _ORIGIN_PREFIX.sub("", text).strip()
    return cleaned or text.strip()
