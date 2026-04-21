"""Analytics event schema — the single shared contract for product metrics.

Every event emission must go through `emit()` so the dashboard funnel stays consistent.
Schema mirrors PRD v2.0 §9.2.
"""

from enum import StrEnum
from typing import Any

import structlog

log = structlog.get_logger("analytics")


class Event(StrEnum):
    FIRST_CONTACT_RECEIVED = "first_contact_received"
    ORIGIN_REQUESTED = "origin_requested"
    ORIGIN_RECEIVED_LOCATION = "origin_received_location"
    ORIGIN_RECEIVED_TEXT = "origin_received_text"
    DESTINATION_RECEIVED = "destination_received"
    GEOCODE_SUCCESS = "geocode_success"
    GEOCODE_FAILURE = "geocode_failure"
    AMBIGUITY_PROMPT_SENT = "ambiguity_prompt_sent"
    ROUTE_SUCCESS = "route_success"
    ROUTE_FAILURE = "route_failure"
    NEARBY_SUCCESS = "nearby_success"
    NEARBY_FAILURE = "nearby_failure"
    SESSION_COMPLETED = "session_completed"
    SESSION_ABANDONED = "session_abandoned"


def emit(event: Event, user_id: str, **attrs: Any) -> None:
    log.info(event.value, user_id=user_id, **attrs)
