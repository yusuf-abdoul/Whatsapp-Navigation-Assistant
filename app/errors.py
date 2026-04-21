"""Error taxonomy — every user-facing failure maps to one of these.

Rule: never guess silently. Each error yields a specific honest response.
"""

from enum import StrEnum


class ErrorKind(StrEnum):
    GEOCODE_FAIL = "geocode_fail"
    ROUTE_FAIL = "route_fail"
    AMBIGUOUS = "ambiguous"
    RATE_LIMITED = "rate_limited"
    PROVIDER_TIMEOUT = "provider_timeout"
    UNKNOWN_INTENT = "unknown_intent"


class WNAError(Exception):
    def __init__(self, kind: ErrorKind, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail
        super().__init__(f"{kind.value}: {detail}" if detail else kind.value)
