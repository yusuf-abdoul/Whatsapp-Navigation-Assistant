from enum import StrEnum


class Intent(StrEnum):
    DIRECTION = "direction"
    NEARBY = "nearby"
    HELP = "help"
    CLARIFY = "clarify"
    CANCEL = "cancel"
    START_ROUTE = "start_route"  # contributor starts live trip-capture
    END_ROUTE = "end_route"  # contributor finishes the live capture
    UNKNOWN = "unknown"
