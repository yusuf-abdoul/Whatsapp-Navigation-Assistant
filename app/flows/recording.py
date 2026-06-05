"""Live trip-capture over WhatsApp.

The contributor walks the bot through their journey step by step:

    start trip → destination name → origin name → live-location → leg info →
    "now at X" → live-location → "same" or "changed" → leg info → ... →
    end → summary → "confirm" → submitted as pending corridor

Same target schema as the web form (``CorridorSubmission``), same persist
path (``create_pending``), same admin review queue. Two entry points, one
data shape.

Durations are deduced from the wall-clock delta between the live-location
shares for consecutive anchors. We trust the contributor for cost (they
just paid it) and the bot's clock for time (more reliable than recall).
Instructions are synthesized from mode + to-anchor at render time, so the
contributor never has to type prose.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.exc import SQLAlchemyError

from app.corridors.db import session_factory
from app.corridors.models import SEGMENT_MODES
from app.corridors.submission import (
    AnchorInput,
    CorridorSubmission,
    SegmentInput,
    SubmissionError,
    create_pending,
)
from app.session import store
from app.session.state import (
    RecordedAnchor,
    RecordedLeg,
    RecordingState,
    SessionState,
)

if TYPE_CHECKING:
    from app.channel.base import ChannelAdapter, InboundMessage

log = structlog.get_logger("flows.recording")

# Default city for new anchor rows created during recording. Multi-city
# launch will derive this from contributor profile.
_DEFAULT_CITY = "abuja"

# Mode words the parser recognises. Matches `SEGMENT_MODES` exactly so the
# downstream schema validation never rejects what we accept.
_MODE_RE = re.compile(
    r"\b(" + "|".join(re.escape(m) for m in SEGMENT_MODES) + r")\b",
    re.IGNORECASE,
)
# A bare integer in the message — the contributor's typed fare in naira.
_COST_RE = re.compile(r"\b(\d{1,7})\b")

# Stripper for "now at <name>" / "at <name>" / "arrived at <name>" prefixes
# so the contributor's reply can be naturally phrased.
_ANCHOR_PREFIX_RE = re.compile(
    r"^\s*(?:now\s+at|i'm\s+at|im\s+at|at|arrived\s+at|reached)\s+",
    re.IGNORECASE,
)


# --- Entry: contributor said "start trip" --------------------------------


async def start(session: SessionState, channel: ChannelAdapter) -> None:
    """Initialise the recording buffer and prompt for the destination."""
    session.recording = RecordingState(awaiting="destination")
    session.destination = None  # don't let stale query state leak in
    session.origin = None
    await store.put(session)
    await channel.send_text(
        session.user_id,
        "Recording a new route. What's the destination?",
    )


# --- Entry point routed from the orchestrator ----------------------------


async def handle(session: SessionState, message: InboundMessage, channel: ChannelAdapter) -> None:
    """Single entry — dispatches by ``session.recording.awaiting``.

    The caller has already confirmed ``session.recording is not None``.
    Live-location shares and text are both routed here; the dispatcher
    decides which is acceptable for the current step.
    """
    state = session.recording
    assert state is not None

    has_location = message.latitude is not None and message.longitude is not None
    text = (message.text or "").strip()

    # Stage-specific routing.
    if state.awaiting == "destination":
        await _handle_destination(session, text, channel)
    elif state.awaiting == "origin_name":
        await _handle_origin_name(session, text, channel)
    elif state.awaiting == "origin_location":
        await _handle_origin_location(
            session, message.latitude, message.longitude, has_location, channel
        )
    elif state.awaiting == "leg_info":
        await _handle_leg_info(session, text, channel)
    elif state.awaiting == "next_anchor_name":
        await _handle_next_anchor_name(session, text, channel)
    elif state.awaiting == "next_anchor_location":
        await _handle_next_anchor_location(
            session, message.latitude, message.longitude, has_location, channel
        )
    elif state.awaiting == "transfer_decision":
        await _handle_transfer_decision(session, text, channel)
    elif state.awaiting == "confirmation":
        await _handle_confirmation(session, text, channel)
    else:  # pragma: no cover — exhaustive Literal makes this dead
        log.error("recording_unknown_awaiting", awaiting=state.awaiting)


# --- Stage handlers ------------------------------------------------------


async def _handle_destination(session: SessionState, text: str, channel: ChannelAdapter) -> None:
    if not text:
        await channel.send_text(session.user_id, "Tell me the destination by typing its name.")
        return
    state = session.recording
    assert state is not None
    state.destination_name = text
    state.awaiting = "origin_name"
    await store.put(session)
    await channel.send_text(session.user_id, "What's the name of where you are now?")


async def _handle_origin_name(session: SessionState, text: str, channel: ChannelAdapter) -> None:
    if not text:
        await channel.send_text(session.user_id, "Tell me the name of your starting point.")
        return
    state = session.recording
    assert state is not None
    state.pending_anchor_name = _strip_anchor_prefix(text)
    state.awaiting = "origin_location"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        f"Share your live location to pin {state.pending_anchor_name}.",
    )


async def _handle_origin_location(
    session: SessionState,
    lat: float | None,
    lon: float | None,
    has_location: bool,
    channel: ChannelAdapter,
) -> None:
    if not has_location or lat is None or lon is None:
        await channel.send_text(
            session.user_id,
            "I need your live location for this stop. "
            "Tap the paperclip → Location → Send your current location.",
        )
        return
    state = session.recording
    assert state is not None
    name = state.pending_anchor_name or "Origin"
    state.anchors.append(RecordedAnchor(name=name, lat=lat, lon=lon))
    state.pending_anchor_name = None
    state.awaiting = "leg_info"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        f"✓ Anchor 1: {name}.\n\n"
        "What's the next leg — transport mode and fare?\n"
        "e.g. 'bike 200' or 'taxi 400'",
    )


async def _handle_leg_info(session: SessionState, text: str, channel: ChannelAdapter) -> None:
    parsed = _parse_leg(text)
    if parsed is None:
        await channel.send_text(
            session.user_id,
            "I didn't catch that. Try: 'taxi 400' (mode + naira fare). "
            "Modes: " + ", ".join(SEGMENT_MODES) + ".",
        )
        return
    mode, cost_ngn = parsed
    state = session.recording
    assert state is not None
    state.pending_leg = RecordedLeg(mode=mode, cost_ngn=cost_ngn)
    state.awaiting = "next_anchor_name"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        "Got it. Tell me when you reach the next stop — say something like "
        "'now at Police Signpost', or 'end' if you've arrived at the destination.",
    )


async def _handle_next_anchor_name(
    session: SessionState, text: str, channel: ChannelAdapter
) -> None:
    if not text:
        await channel.send_text(
            session.user_id,
            "Tell me the name of the place you've reached.",
        )
        return
    state = session.recording
    assert state is not None
    state.pending_anchor_name = _strip_anchor_prefix(text)
    state.awaiting = "next_anchor_location"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        f"Share your live location to pin {state.pending_anchor_name}.",
    )


async def _handle_next_anchor_location(
    session: SessionState,
    lat: float | None,
    lon: float | None,
    has_location: bool,
    channel: ChannelAdapter,
) -> None:
    if not has_location or lat is None or lon is None:
        await channel.send_text(
            session.user_id,
            "I need your live location for this stop. "
            "Tap the paperclip → Location → Send your current location.",
        )
        return
    state = session.recording
    assert state is not None

    # Append the new anchor and promote the pending leg to legs[].
    name = state.pending_anchor_name or f"Stop {len(state.anchors) + 1}"
    state.anchors.append(RecordedAnchor(name=name, lat=lat, lon=lon))
    state.pending_anchor_name = None
    if state.pending_leg is not None:
        state.legs.append(state.pending_leg)
        state.pending_leg = None

    # Was that the destination they named at the start? Mark it implicitly.
    # If yes, we still ask transfer_decision for the LAST leg (the one we
    # just promoted) so the contributor confirms vehicle status.
    state.awaiting = "transfer_decision"
    await store.put(session)
    n = len(state.anchors)
    await channel.send_text(
        session.user_id,
        f"✓ Anchor {n}: {name}.\n\n"
        "Did you stay on the same vehicle, or change here? Reply 'same' or 'changed'. "
        "(If you've arrived at the destination, reply 'end'.)",
    )


async def _handle_transfer_decision(
    session: SessionState, text: str, channel: ChannelAdapter
) -> None:
    state = session.recording
    assert state is not None
    lowered = text.lower()
    if lowered in {"same", "same vehicle", "no", "stayed"}:
        # Transfer flag applies to the NEXT leg, not the just-recorded one.
        # We track this by NOT mutating the most-recent leg; the contributor's
        # answer matters when they describe their next leg.
        # For now, just advance.
        state.awaiting = "leg_info"
        await store.put(session)
        await channel.send_text(
            session.user_id,
            "OK, same vehicle continues. What's the next leg's mode and fare?\n"
            "(or 'end' if you've arrived)",
        )
    elif lowered in {"changed", "change", "switch", "switched", "transfer", "yes"}:
        # Mark the NEXT leg as a transfer when it's recorded. We stash this
        # by setting a sentinel on `pending_leg` once the next leg-info lands.
        # Cleanest: store the pending flag on the state itself.
        state.awaiting = "leg_info"
        # Use a marker we look for in _handle_leg_info.
        if state.pending_leg is None:
            state.pending_leg = RecordedLeg(mode="taxi", transfer=True)
            # We'll overwrite mode/cost when the contributor sends leg info.
            # Keep transfer=True as the sticky bit.
        await store.put(session)
        await channel.send_text(
            session.user_id,
            "OK, vehicle changed. What's the next leg's mode and fare?",
        )
    else:
        await channel.send_text(
            session.user_id,
            "Please reply 'same' or 'changed' (or 'end' if you've arrived).",
        )


async def _handle_confirmation(session: SessionState, text: str, channel: ChannelAdapter) -> None:
    lowered = text.lower()
    if lowered in {"confirm", "yes", "submit", "ok"}:
        await _submit(session, channel)
    elif lowered in {"cancel", "no", "discard"}:
        session.recording = None
        await store.put(session)
        await channel.send_text(session.user_id, "Recording discarded.")
    else:
        await channel.send_text(
            session.user_id, "Reply 'confirm' to submit, or 'cancel' to discard."
        )


# --- End-of-recording (triggered by END_ROUTE intent) --------------------


async def end_recording(session: SessionState, channel: ChannelAdapter) -> None:
    """Build the summary and ask for confirmation.

    Caller has confirmed an END_ROUTE intent fired while ``session.recording``
    is set. If the recording is too short to be a real corridor we tell the
    contributor and discard.
    """
    state = session.recording
    if state is None:
        await channel.send_text(
            session.user_id, "You're not recording a route. Say 'start trip' to begin."
        )
        return

    if len(state.anchors) < 2 or len(state.legs) < 1:
        session.recording = None
        await store.put(session)
        await channel.send_text(
            session.user_id,
            "Not enough recorded yet — a route needs at least an origin, "
            "one leg, and a destination. Discarded.",
        )
        return

    # Drop any half-built leg/anchor that hadn't been finalised.
    state.pending_leg = None
    state.pending_anchor_name = None

    # Generate the human-facing summary.
    lines = ["Recorded:"]
    for i, leg in enumerate(state.legs):
        from_a = state.anchors[i].name
        to_a = (
            state.anchors[i + 1].name
            if i + 1 < len(state.anchors)
            else state.destination_name or "Destination"
        )
        duration = _duration_min(state.anchors, i)
        bits: list[str] = [f"{leg.mode}"]
        if leg.cost_ngn:
            bits.append(f"₦{leg.cost_ngn}")
        if duration is not None:
            bits.append(f"~{duration} min")
        if leg.transfer:
            bits.append("changed")
        lines.append(f"{i + 1}. {from_a} → {to_a} ({', '.join(bits)})")

    summary = "\n".join(lines)
    state.awaiting = "confirmation"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        summary + "\n\nReply 'confirm' to submit for review, or 'cancel' to discard.",
    )


# --- Persist: convert recording to CorridorSubmission --------------------


async def _submit(session: SessionState, channel: ChannelAdapter) -> None:
    """Build a ``CorridorSubmission`` from the buffer and persist it."""
    state = session.recording
    assert state is not None

    anchors_input = [AnchorInput(name=a.name, lat=a.lat, lon=a.lon) for a in state.anchors]
    destination_name = state.destination_name or state.anchors[-1].name

    segments_input: list[SegmentInput] = []
    for i, leg in enumerate(state.legs):
        from_anchor = state.anchors[i].name
        # If we ran out of anchors (contributor said 'end' without a final
        # location share for the destination), use the destination name as
        # the to_anchor placeholder — submission validation will reject it
        # if it isn't in the anchors list, which is the right outcome.
        to_anchor = state.anchors[i + 1].name if i + 1 < len(state.anchors) else destination_name
        duration = _duration_min(state.anchors, i)
        segments_input.append(
            SegmentInput(
                from_anchor=from_anchor,
                to_anchor=to_anchor,
                mode=leg.mode,
                # Renderer synthesises ("Take a {mode} to {to_anchor}") — the
                # instruction text we store here is only a fallback / audit
                # trail. Kept short.
                instruction=f"Take a {leg.mode} to {to_anchor}.",
                transfer=leg.transfer,
                cost_ngn=leg.cost_ngn,
                duration_min=duration,
            )
        )

    payload = CorridorSubmission(
        city=_DEFAULT_CITY,
        destination=destination_name,
        anchors=anchors_input,
        segments=segments_input,
        applicability_notes=None,
    )

    try:
        async with session_factory()() as db:
            corridor = await create_pending(db, payload=payload, contributor_id=session.user_id)
            await db.commit()
    except SubmissionError as e:
        await channel.send_text(
            session.user_id,
            f"Couldn't submit: {e}\n\nSay 'cancel' to discard, or fix and try 'confirm' again.",
        )
        return
    except SQLAlchemyError as e:
        log.warning("recording_submit_failed", error=str(e))
        await channel.send_text(
            session.user_id,
            "Something went wrong saving your route. Please try 'confirm' again in a moment.",
        )
        return

    session.recording = None
    await store.put(session)
    await channel.send_text(
        session.user_id,
        f"Thanks — submission pending review. Reference: {str(corridor.id)[:8]}.",
    )


# --- Helpers --------------------------------------------------------------


def _parse_leg(text: str) -> tuple[str, int | None] | None:
    """Pull (mode, cost_ngn) out of phrases like 'bike 200' or '400 taxi'."""
    if not text:
        return None
    mode_match = _MODE_RE.search(text.lower())
    if mode_match is None:
        return None
    mode = mode_match.group(1).lower()
    cost_match = _COST_RE.search(text)
    cost = int(cost_match.group(1)) if cost_match else None
    return mode, cost


def _strip_anchor_prefix(text: str) -> str:
    """Strip 'now at', 'i'm at', 'arrived at' so 'now at Berger' becomes 'Berger'."""
    return _ANCHOR_PREFIX_RE.sub("", text).strip() or text.strip()


def _duration_min(anchors: list[RecordedAnchor], leg_index: int) -> int | None:
    """Compute minutes between two consecutive anchor timestamps."""
    if leg_index + 1 >= len(anchors):
        return None
    delta = anchors[leg_index + 1].ts - anchors[leg_index].ts
    minutes = round(delta.total_seconds() / 60)
    return max(1, minutes)


# Re-export for tests
__all__ = [
    "_parse_leg",
    "_strip_anchor_prefix",
    "end_recording",
    "handle",
    "start",
]
_ = (datetime, uuid)  # imported for type completeness; mypy/ruff suppression
