from enum import StrEnum


class Intent(StrEnum):
    DIRECTION = "direction"
    NEARBY = "nearby"
    HELP = "help"
    CLARIFY = "clarify"
    CANCEL = "cancel"
    UNKNOWN = "unknown"
