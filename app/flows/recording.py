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
    elif state.awaiting == "destination_location":
        await _handle_destination_location(
            session, message.latitude, message.longitude, has_location, channel
        )
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
    # transfer=True when this leg starts at a vehicle-change point (contributor
    # answered "changed" for the previous anchor). Flag is reset once consumed.
    transfer = state.next_leg_is_transfer
    state.pending_leg = RecordedLeg(mode=mode, cost_ngn=cost_ngn, transfer=transfer)
    state.next_leg_is_transfer = False
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

    # Append the new anchor as an endpoint (default). The next transfer_decision
    # answer may reclassify it as a passthrough. Promote the pending leg exactly
    # once — subsequent same-vehicle anchors on the same segment won't have a
    # pending_leg to promote.
    name = state.pending_anchor_name or f"Stop {len(state.anchors) + 1}"
    state.anchors.append(RecordedAnchor(name=name, lat=lat, lon=lon))
    state.pending_anchor_name = None
    if state.pending_leg is not None:
        state.legs.append(state.pending_leg)
        state.pending_leg = None

    # Contributor named a mid-anchor with the destination's name → treat as
    # arrival, skip the transfer question, go straight to summary.
    if _looks_like_destination(name, state.destination_name):
        await _show_summary_and_confirm(session, channel)
        return

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
    lowered = text.lower().strip()
    if lowered in {"same", "same vehicle", "no", "stayed"}:
        # Same vehicle continues — the just-recorded anchor is a landmark on
        # the current segment, not a segment endpoint. Fare already captured
        # for this segment; no re-prompt.
        if state.anchors:
            state.anchors[-1].is_passthrough = True
        state.awaiting = "next_anchor_name"
        current_mode = state.legs[-1].mode if state.legs else "vehicle"
        await store.put(session)
        await channel.send_text(
            session.user_id,
            f"OK, same {current_mode} continues. Tell me when you reach the next stop "
            "(say 'now at ...'), or 'end' if you've arrived.",
        )
    elif lowered in {"changed", "change", "switch", "switched", "transfer", "yes"}:
        # New segment starts at this anchor — it stays as an endpoint. The
        # next leg the contributor describes gets transfer=True.
        state.next_leg_is_transfer = True
        state.awaiting = "leg_info"
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
    """Contributor said 'end'. Prompt for destination location if we still
    need it, otherwise show the summary.

    Cases:
    - No recording running → gentle "you're not recording" nudge.
    - Awaiting a mid-anchor location share (they said 'now at X' but never
      shared) → nudge them to finish that step first.
    - pending_leg is set OR last anchor's name isn't the destination → we
      still need the destination's location. Transition to
      ``destination_location`` and prompt.
    - Everything captured → build summary directly.
    """
    state = session.recording
    if state is None:
        await channel.send_text(
            session.user_id, "You're not recording a route. Say 'start trip' to begin."
        )
        return

    # Too early: no anchors yet, no leg described yet, or still in the
    # intro questions.
    intro_states = {"destination", "origin_name", "origin_location"}
    if (
        not state.anchors
        or state.awaiting in intro_states
        or (not state.legs and state.pending_leg is None)
    ):
        session.recording = None
        await store.put(session)
        await channel.send_text(
            session.user_id,
            "Not enough recorded yet — a route needs at least an origin, "
            "one leg, and a destination. Discarded.",
        )
        return

    # Contributor said "now at X" but hadn't shared its location yet — don't
    # discard, ask them to complete or cancel explicitly.
    if state.awaiting == "next_anchor_location" and state.pending_anchor_name:
        await channel.send_text(
            session.user_id,
            f"Share your live location to pin {state.pending_anchor_name} first, "
            "or send 'cancel' to discard the recording.",
        )
        return

    # Do we still need to capture the destination's live location?
    last_anchor_name = state.anchors[-1].name if state.anchors else ""
    at_destination = _looks_like_destination(last_anchor_name, state.destination_name)
    needs_destination = state.pending_leg is not None or not at_destination

    if not needs_destination:
        await _show_summary_and_confirm(session, channel)
        return

    # A 'changed' with no leg described yet: we need the new segment's fare
    # before we can close. Nudge for it.
    if state.next_leg_is_transfer and state.pending_leg is None:
        state.awaiting = "leg_info"
        await store.put(session)
        await channel.send_text(
            session.user_id,
            f"Before I can finish, tell me the mode + fare for the leg from "
            f"{last_anchor_name} to {state.destination_name or 'the destination'}.",
        )
        return

    # Otherwise (pending_leg set OR mid-segment same-vehicle to destination):
    # we have a leg for the final segment already, we just need the
    # destination's coordinates.
    state.pending_anchor_name = state.destination_name or "Destination"
    state.awaiting = "destination_location"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        f"Almost done — share your live location to pin "
        f"{state.pending_anchor_name}, the destination.",
    )


async def _handle_destination_location(
    session: SessionState,
    lat: float | None,
    lon: float | None,
    has_location: bool,
    channel: ChannelAdapter,
) -> None:
    """Receive the destination's live location, close the final segment,
    and show the summary."""
    if not has_location or lat is None or lon is None:
        await channel.send_text(
            session.user_id,
            "I need your live location for the destination. "
            "Tap the paperclip → Location → Send your current location.",
        )
        return
    state = session.recording
    assert state is not None
    name = state.pending_anchor_name or state.destination_name or "Destination"
    state.anchors.append(RecordedAnchor(name=name, lat=lat, lon=lon))
    state.pending_anchor_name = None
    if state.pending_leg is not None:
        state.legs.append(state.pending_leg)
        state.pending_leg = None
    await _show_summary_and_confirm(session, channel)


async def _show_summary_and_confirm(session: SessionState, channel: ChannelAdapter) -> None:
    """Build the segment view of the recording, show it, and prompt for confirm."""
    state = session.recording
    assert state is not None
    segments = _build_segments(state)

    if not segments:
        session.recording = None
        await store.put(session)
        await channel.send_text(
            session.user_id,
            "Not enough recorded to build a route. Discarded.",
        )
        return

    lines = ["Recorded:"]
    for i, seg in enumerate(segments):
        bits: list[str] = [seg["leg"].mode]
        if seg["leg"].cost_ngn:
            bits.append(f"₦{seg['leg'].cost_ngn}")
        if seg["duration_min"] is not None:
            bits.append(f"~{seg['duration_min']} min")
        if seg["leg"].transfer:
            bits.append("changed")
        header = f"{seg['from_anchor']} → {seg['to_anchor']}"
        if seg["passthroughs"]:
            header = (
                f"{seg['from_anchor']} → "
                + " → ".join(seg["passthroughs"])
                + f" → {seg['to_anchor']}"
            )
        lines.append(f"{i + 1}. {header} ({', '.join(bits)})")

    state.awaiting = "confirmation"
    await store.put(session)
    await channel.send_text(
        session.user_id,
        "\n".join(lines) + "\n\nReply 'confirm' to submit for review, or 'cancel' to discard.",
    )


# --- Persist: convert recording to CorridorSubmission --------------------


async def _submit(session: SessionState, channel: ChannelAdapter) -> None:
    """Build a ``CorridorSubmission`` from the buffer and persist it."""
    state = session.recording
    assert state is not None

    anchors_input = [AnchorInput(name=a.name, lat=a.lat, lon=a.lon) for a in state.anchors]
    destination_name = state.destination_name or state.anchors[-1].name

    segments_input: list[SegmentInput] = []
    for seg in _build_segments(state):
        segments_input.append(
            SegmentInput(
                from_anchor=seg["from_anchor"],
                to_anchor=seg["to_anchor"],
                mode=seg["leg"].mode,
                # Renderer synthesises ("Take a {mode} to {to_anchor}") from
                # the structured fields — the stored instruction is a short
                # fallback / audit trail.
                instruction=f"Take a {seg['leg'].mode} to {seg['to_anchor']}.",
                transfer=seg["leg"].transfer,
                cost_ngn=seg["leg"].cost_ngn,
                duration_min=seg["duration_min"],
                passthroughs=seg["passthroughs"],
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


def _duration_min_between(start: RecordedAnchor, end: RecordedAnchor) -> int | None:
    """Minutes between two anchor timestamps, floor-clamped to 1."""
    delta = end.ts - start.ts
    minutes = round(delta.total_seconds() / 60)
    return max(1, minutes)


def _norm(text: str | None) -> str:
    return (text or "").strip().lower()


def _looks_like_destination(candidate: str, destination_name: str | None) -> bool:
    """True when ``candidate`` matches the recording's destination name.

    Loose comparison: case + whitespace only. Alias / token-subset matching is
    handled at submission time (see ``app/corridors/submission.py``); at this
    stage we only need to recognise "the contributor typed the destination
    name" so we skip the transfer-decision prompt at the final anchor.
    """
    return bool(destination_name) and _norm(candidate) == _norm(destination_name)


def _build_segments(state: RecordingState) -> list[dict]:  # type: ignore[type-arg]
    """Group anchors + legs into segments for display / persistence.

    Endpoints are anchors where ``is_passthrough=False`` — the origin, the
    destination, and any anchor at a vehicle change. The i-th endpoint pair
    ``(endpoints[i], endpoints[i+1])`` defines segment i; anchors strictly
    between them (all passthroughs) become the segment's passthrough list.
    Duration = wall-clock delta between the segment's two endpoints. Each
    segment consumes one entry from ``state.legs`` in order.
    """
    if len(state.anchors) < 2 or not state.legs:
        return []

    endpoint_indices = [i for i, a in enumerate(state.anchors) if not a.is_passthrough]
    # Guarantee the last anchor closes a segment even if flagged passthrough.
    if endpoint_indices and endpoint_indices[-1] != len(state.anchors) - 1:
        endpoint_indices.append(len(state.anchors) - 1)

    segments: list[dict] = []  # type: ignore[type-arg]
    for seg_idx in range(len(endpoint_indices) - 1):
        if seg_idx >= len(state.legs):
            break
        start_i = endpoint_indices[seg_idx]
        end_i = endpoint_indices[seg_idx + 1]
        passthrough_names = [state.anchors[j].name for j in range(start_i + 1, end_i)]
        segments.append(
            {
                "from_anchor": state.anchors[start_i].name,
                "to_anchor": state.anchors[end_i].name,
                "passthroughs": passthrough_names,
                "leg": state.legs[seg_idx],
                "duration_min": _duration_min_between(state.anchors[start_i], state.anchors[end_i]),
            }
        )
    return segments


# Re-export for tests
__all__ = [
    "_build_segments",
    "_looks_like_destination",
    "_parse_leg",
    "_strip_anchor_prefix",
    "end_recording",
    "handle",
    "start",
]
_ = (datetime, uuid)  # imported for type completeness; mypy/ruff suppression
