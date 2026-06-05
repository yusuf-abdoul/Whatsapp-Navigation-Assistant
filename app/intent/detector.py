"""Rule-based intent detection.

Matches the PRD v2.0 intent set. No LLM — regex + a short vocabulary is enough
for the launch corpus, and misclassifications fall back to UNKNOWN cleanly.
"""

import re
from dataclasses import dataclass

from app.intent.types import Intent

# DIRECTION patterns. The "with-from" variants capture an origin too — used for
# inline phrasings like "How about Banex from Police Signpost" so we can skip
# the "share your live location" prompt when the user already named both ends.
_DIRECTION_VERB = (
    r"how\s+(?:do\s+i|to|can\s+i)\s+get\s+to"
    r"|how\s+about(?:\s+going\s+to)?"
    r"|directions?\s+to"
    r"|route\s+to"
    r"|where\s+is"
    r"|get\s+me\s+to"
    r"|take\s+me\s+to"
    r"|go\s+to"
    r"|navigate\s+to"
)

_DIRECTION_WITH_FROM = re.compile(
    rf"^\s*(?:{_DIRECTION_VERB})\s+(.+?)\s+from\s+(.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_FROM_TO = re.compile(
    r"^\s*from\s+(.+?)\s+to\s+(.+?)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

_DIRECTION = re.compile(
    rf"^\s*(?:{_DIRECTION_VERB})\s+(.+?)\s*[?.!]*\s*$",
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

# Contributor begins a live trip-capture session over WhatsApp.
_START_ROUTE = re.compile(
    r"^\s*(?:start|begin|record)\s+(?:a\s+|the\s+|my\s+)?(?:trip|route|recording|journey)\s*[?.!]*\s*$",
    re.IGNORECASE,
)

# Contributor finishes the live capture. Detected globally; the orchestrator
# only acts on it when a recording is actually active.
_END_ROUTE = re.compile(
    r"^\s*(?:end(?:\s+(?:trip|route|recording))?|done|arrived|finished|complete\s+(?:trip|route))\s*[?.!]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentResult:
    intent: Intent
    query: str | None = None
    origin: str | None = None


def detect(text: str) -> IntentResult:
    if not text or not text.strip():
        return IntentResult(Intent.UNKNOWN)
    if _CANCEL.match(text):
        return IntentResult(Intent.CANCEL)
    # START_ROUTE / END_ROUTE precede HELP so "start trip" / "end" don't
    # accidentally fall into HELP / DIRECTION semantics.
    if _START_ROUTE.match(text):
        return IntentResult(Intent.START_ROUTE)
    if _END_ROUTE.match(text):
        return IntentResult(Intent.END_ROUTE)
    if _HELP.match(text):
        return IntentResult(Intent.HELP)
    if m := _DIRECTION_WITH_FROM.match(text):
        return IntentResult(Intent.DIRECTION, m.group(1).strip(), origin=m.group(2).strip())
    if m := _FROM_TO.match(text):
        return IntentResult(Intent.DIRECTION, m.group(2).strip(), origin=m.group(1).strip())
    if m := _DIRECTION.match(text):
        return IntentResult(Intent.DIRECTION, m.group(1).strip())
    if m := _NEARBY_PREFIX.match(text):
        return IntentResult(Intent.NEARBY, m.group(1).strip())
    if m := _NEARBY_SUFFIX.match(text):
        return IntentResult(Intent.NEARBY, m.group(1).strip())
    return IntentResult(Intent.UNKNOWN)
