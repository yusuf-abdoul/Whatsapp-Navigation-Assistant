"""Rule-based intent detection.

Matches the PRD v2.0 intent set. No LLM — regex + a short vocabulary is enough
for the launch corpus, and misclassifications fall back to UNKNOWN cleanly.
"""

import re
from dataclasses import dataclass

from app.intent.types import Intent

_DIRECTION = re.compile(
    r"^\s*(?:"
    r"how\s+(?:do\s+i|to|can\s+i)\s+get\s+to"
    r"|directions?\s+to"
    r"|route\s+to"
    r"|where\s+is"
    r"|get\s+me\s+to"
    r"|take\s+me\s+to"
    r"|go\s+to"
    r"|navigate\s+to"
    r")\s+(.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_NEARBY_PREFIX = re.compile(
    r"^\s*(?:nearest|closest)\s+(.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_NEARBY_SUFFIX = re.compile(
    r"^\s*(.+?)\s+(?:near\s+me|nearby|near\s+here|around\s+me|around\s+here)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_HELP = re.compile(r"^\s*(?:help|menu|start|/help|\?|hi|hello)\s*[?.!]*\s*$", re.IGNORECASE)

_CANCEL = re.compile(
    r"^\s*(?:cancel|stop|reset|start\s+over|nvm|never\s*mind|quit|exit)\s*[?.!]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    query: str | None = None


def detect(text: str) -> IntentResult:
    if not text or not text.strip():
        return IntentResult(Intent.UNKNOWN)
    if _CANCEL.match(text):
        return IntentResult(Intent.CANCEL)
    if _HELP.match(text):
        return IntentResult(Intent.HELP)
    if m := _DIRECTION.match(text):
        return IntentResult(Intent.DIRECTION, m.group(1).strip())
    if m := _NEARBY_PREFIX.match(text):
        return IntentResult(Intent.NEARBY, m.group(1).strip())
    if m := _NEARBY_SUFFIX.match(text):
        return IntentResult(Intent.NEARBY, m.group(1).strip())
    return IntentResult(Intent.UNKNOWN)
