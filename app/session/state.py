from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.intent.types import Intent


class Place(BaseModel):
    query: str
    lat: float | None = None
    lon: float | None = None
    display_name: str | None = None


# --- Live trip-capture recording -----------------------------------------
#
# A contributor starts a recording, names their destination + origin, shares
# their live location for each anchor as they reach it, and answers a few
# short prompts per leg (mode, fare, transfer-or-not). At "end" the buffer
# is converted to a CorridorSubmission and persisted as a pending corridor —
# the exact same path the web form takes.


class RecordedAnchor(BaseModel):
    name: str
    lat: float
    lon: float
    # Server timestamp at the moment the contributor shared their location
    # for this anchor. Inter-anchor wall-clock deltas give us segment durations.
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # A "passthrough" anchor sits INSIDE the current segment — the contributor
    # kept the same vehicle through it. Endpoints (is_passthrough=False) are
    # the origin, destination, and any anchor where the contributor changed
    # vehicles. At submission time endpoints delimit segments; passthroughs
    # go into the segment's `passthroughs` list.
    is_passthrough: bool = False


class RecordedLeg(BaseModel):
    mode: str
    cost_ngn: int | None = None
    # True when the leg starts at a vehicle-change point (contributor said
    # "changed" for the previous anchor). Flows through to the persisted
    # segment for renderer formatting.
    transfer: bool = False


# Conversation cursor — what the bot is waiting for the contributor to send next.
RecordingAwaiting = Literal[
    "destination",
    "origin_name",
    "origin_location",
    "leg_info",
    "next_anchor_name",
    "next_anchor_location",
    "transfer_decision",
    "destination_location",
    "confirmation",
]


class RecordingState(BaseModel):
    destination_name: str | None = None
    anchors: list[RecordedAnchor] = Field(default_factory=list)
    legs: list[RecordedLeg] = Field(default_factory=list)
    # Holds the half-built leg between `leg_info` (mode+cost) and the next
    # anchor being recorded. Promoted to `legs` once the next anchor lands.
    pending_leg: RecordedLeg | None = None
    # Holds the next anchor's name between `next_anchor_name` and
    # `next_anchor_location`. Promoted to `anchors` once the location share lands.
    pending_anchor_name: str | None = None
    # Set by "changed" in transfer_decision — the NEXT leg described becomes
    # transfer=True. Reset once consumed in leg_info.
    next_leg_is_transfer: bool = False
    awaiting: RecordingAwaiting = "destination"


class SessionState(BaseModel):
    user_id: str
    origin: Place | None = None
    destination: Place | None = None
    last_intent: Intent = Intent.UNKNOWN
    pending_clarification: list[dict[str, Any]] = Field(default_factory=list)
    recording: RecordingState | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
