"""Flow orchestrator — state-aware conversation dispatch.

State machine (derived from session contents, no explicit flag):
  IDLE → DIRECTION(X) → corridor or geocode → destination set → AWAITING_ORIGIN
  AWAITING_ORIGIN + live location → render corridor or LocationIQ route → IDLE
  AWAITING_ORIGIN + text(Y)       → resolve(Y) as origin → render → IDLE
  IDLE + DIRECTION(X, from Y)     → resolve both, render directly (skip prompt)
  any state + new DIRECTION       → overwrite destination, re-enter AWAITING_ORIGIN
  any state + CANCEL              → clear session

Origin resolution order: corridor anchor (by name/alias) → LocationIQ geocode.
This catches "Police signpost" as a known anchor before letting LocationIQ
guess at nearby addresses.

Reply preference: a curated corridor (numbered commuter steps) when one exists
for the destination AND the user is near a corridor anchor. Otherwise we fall
back to the LocationIQ-only "X km, ~Y min, map link" reply.
"""

import re

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.analytics.events import Event, emit
from app.channel.base import ChannelAdapter, InboundMessage
from app.corridors.db import session_factory
from app.corridors.models import Anchor, Corridor
from app.corridors.repository import (
    clip_segments_from_anchor,
    find_anchor_by_name,
    find_corridors_by_destination,
    nearest_anchor_in_corridor,
)
from app.errors import ErrorKind, WNAError
from app.formatting.responses import (
    format_ambiguity,
    format_corridor,
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
    "• 'How about Banex from Police Signpost?'\n"
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

# Default city for corridor lookups. Multi-city launch will derive this per-user.
_DEFAULT_CITY = "abuja"

log = structlog.get_logger("orchestrator")

# A corridor reply is only useful if the user is reasonably close to one of its
# anchors. Beyond this distance the corridor's instructions don't apply to the
# user's actual starting point — fall back to LocationIQ.
_CORRIDOR_JOIN_RADIUS_M = 2000


async def handle(message: InboundMessage, channel: ChannelAdapter) -> None:
    emit(Event.FIRST_CONTACT_RECEIVED, user_id=message.user_id)

    session = await store.get(message.user_id) or SessionState(user_id=message.user_id)

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
        await _handle_direction(session, result.query, result.origin, channel)
        return

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


async def _handle_direction(
    session: SessionState, query: str, origin_text: str | None, channel: ChannelAdapter
) -> None:
    """Set up the destination, then either prompt for origin or — if the user
    already named one inline ("from Police Signpost") — route directly."""
    destination = await _resolve_destination(session, query, channel)
    if destination is None:
        return  # caller already replied (ambiguity / not-found / error)

    session.destination = destination
    session.origin = None
    session.last_intent = Intent.DIRECTION
    await store.put(session)

    if origin_text:
        origin = await _resolve_origin_text(origin_text)
        if origin is not None:
            emit(Event.ORIGIN_RECEIVED_TEXT, user_id=session.user_id, source=origin.query)
            await _route_and_reply(session, origin, channel)
            return
        # Origin text didn't resolve — prompt as if the user only gave a destination.

    emit(Event.ORIGIN_REQUESTED, user_id=session.user_id)
    await channel.send_text(
        session.user_id,
        f"Found {destination.display_name or query}. Now share your live location, "
        "or tell me where you're starting from (e.g. 'I'm at Lugbe').",
    )


async def _resolve_destination(
    session: SessionState, query: str, channel: ChannelAdapter
) -> Place | None:
    """Resolve a destination string to a Place. Replies on the channel for
    failure cases (ambiguity, not-found, error) and returns None then."""
    corridor = await _lookup_corridor(query)
    if corridor is not None:
        anchor = corridor.destination
        emit(Event.DESTINATION_RECEIVED, user_id=session.user_id, query=query, source="corridor")
        return Place(query=query, lat=anchor.lat, lon=anchor.lon, display_name=anchor.name)

    try:
        candidates = await geocode(query)
    except WNAError as e:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=query, kind=e.kind.value)
        await channel.send_text(session.user_id, format_error(e.kind))
        return None

    if not candidates:
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=query, kind="not_found")
        await channel.send_text(
            session.user_id,
            f"I couldn't find '{query}' in Abuja. Try a fuller name or a nearby landmark.",
        )
        return None

    if len(candidates) > 1:
        emit(
            Event.AMBIGUITY_PROMPT_SENT,
            user_id=session.user_id,
            query=query,
            count=len(candidates),
        )
        prompt, options = format_ambiguity(query, candidates[:3])
        await channel.send_options(session.user_id, prompt, options)
        return None

    place = candidates[0]
    emit(Event.GEOCODE_SUCCESS, user_id=session.user_id, query=query)
    emit(Event.DESTINATION_RECEIVED, user_id=session.user_id, query=query, source="locationiq")
    return Place(
        query=query,
        lat=place.lat,
        lon=place.lon,
        display_name=short_name(place.display_name) or place.display_name,
    )


async def _resolve_origin_text(text: str) -> Place | None:
    """Resolve a free-text origin to a Place.

    Tries known corridor anchors first (so 'Police signpost' uses the anchor's
    coordinates, not LocationIQ's nearest-address guess), then falls back to
    LocationIQ geocoding. Returns None if both layers fail.
    """
    cleaned = _strip_origin_prefix(text)

    anchor = await _lookup_anchor(cleaned)
    if anchor is not None:
        return Place(query=cleaned, lat=anchor.lat, lon=anchor.lon, display_name=anchor.name)

    try:
        candidates = await geocode(cleaned)
    except WNAError:
        return None
    if not candidates:
        return None
    return candidates[0]


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
    origin = await _resolve_origin_text(text)
    if origin is None:
        cleaned = _strip_origin_prefix(text)
        emit(Event.GEOCODE_FAILURE, user_id=session.user_id, query=cleaned, kind="not_found")
        await channel.send_text(
            session.user_id,
            f"I couldn't find '{cleaned}' as a starting point. "
            "Try sharing your live location instead.",
        )
        return

    emit(Event.ORIGIN_RECEIVED_TEXT, user_id=session.user_id, source=origin.query)
    await _route_and_reply(session, origin, channel)


async def _route_and_reply(session: SessionState, origin: Place, channel: ChannelAdapter) -> None:
    dest = session.destination
    if dest is None or dest.lat is None or dest.lon is None:
        await channel.send_text(session.user_id, format_error(ErrorKind.UNKNOWN_INTENT))
        return
    if origin.lat is None or origin.lon is None:
        await channel.send_text(session.user_id, format_error(ErrorKind.GEOCODE_FAIL))
        return

    if await _try_corridor_reply(session, origin, channel):
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
        source="locationiq",
    )
    emit(Event.SESSION_COMPLETED, user_id=session.user_id)
    await channel.send_text(session.user_id, format_route(dest, r))
    await store.delete(session.user_id)


async def _try_corridor_reply(
    session: SessionState, origin: Place, channel: ChannelAdapter
) -> bool:
    """Return True if a corridor reply was sent. False = caller should fall back."""
    dest = session.destination
    if dest is None or origin.lat is None or origin.lon is None:
        return False

    try:
        factory = session_factory()
        async with factory() as db:
            corridors = await find_corridors_by_destination(db, dest.query, city=_DEFAULT_CITY)
            if not corridors:
                return False
            corridor = corridors[0]

            result = await nearest_anchor_in_corridor(db, corridor.id, origin.lat, origin.lon)
            if result is None:
                return False
            nearest, dist_to_anchor_m = result
            if dist_to_anchor_m > _CORRIDOR_JOIN_RADIUS_M:
                return False

            clipped = clip_segments_from_anchor(corridor.segments, nearest.id)
            if not clipped:
                return False
    except SQLAlchemyError as e:
        log.warning("corridor_lookup_failed", error=str(e))
        return False

    distance_m: float | None = None
    duration_s: float | None = None
    deep_link: str | None = None
    if dest.lat is not None and dest.lon is not None:
        try:
            r = await route(
                origin_lat=origin.lat,
                origin_lon=origin.lon,
                dest_lat=dest.lat,
                dest_lon=dest.lon,
            )
            distance_m = r.distance_m
            duration_s = r.duration_s
            deep_link = r.deep_link
        except WNAError:
            pass

    emit(
        Event.ROUTE_SUCCESS,
        user_id=session.user_id,
        source="corridor",
        nearest_anchor=nearest.name,
        nearest_anchor_distance_m=round(dist_to_anchor_m),
        steps=len(clipped),
    )
    emit(Event.SESSION_COMPLETED, user_id=session.user_id)
    await channel.send_text(
        session.user_id,
        format_corridor(
            corridor,
            clipped,
            join_anchor=nearest,
            distance_m=distance_m,
            duration_s=duration_s,
            deep_link=deep_link,
        ),
    )
    await store.delete(session.user_id)
    return True


async def _lookup_corridor(query: str) -> Corridor | None:
    """Return the first matching corridor, or None on miss / DB error."""
    try:
        factory = session_factory()
        async with factory() as db:
            corridors = await find_corridors_by_destination(db, query, city=_DEFAULT_CITY)
            return corridors[0] if corridors else None
    except SQLAlchemyError as e:
        log.warning("corridor_lookup_failed", query=query, error=str(e))
        return None


async def _lookup_anchor(name: str) -> Anchor | None:
    """Return a known anchor by name/alias, or None on miss / DB error."""
    try:
        factory = session_factory()
        async with factory() as db:
            return await find_anchor_by_name(db, name, city=_DEFAULT_CITY)
    except SQLAlchemyError as e:
        log.warning("anchor_lookup_failed", name=name, error=str(e))
        return None


def _strip_origin_prefix(text: str) -> str:
    cleaned = _ORIGIN_PREFIX.sub("", text).strip()
    return cleaned or text.strip()
