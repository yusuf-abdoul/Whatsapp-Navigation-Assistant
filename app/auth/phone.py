"""Phone-number normalization for WhatsApp identity.

Normalizes whatever the user typed into E.164 (``+<countrycode><number>``).
Defaults to Nigeria (+234) for the common local formats — leading 0,
or a bare 234... string. Anything else must already include a leading +.

Returns the canonical form or None if the input can't be parsed safely.
We never guess silently across countries — better to reject and ask.
"""

from __future__ import annotations

import re

_DIGITS_OR_PLUS = re.compile(r"[^\d+]")
_E164 = re.compile(r"^\+\d{8,15}$")


def normalize(raw: str | None) -> str | None:
    if not raw:
        return None

    cleaned = _DIGITS_OR_PLUS.sub("", raw)
    if not cleaned:
        return None

    # Already E.164 — accept as-is if it parses.
    if cleaned.startswith("+"):
        return cleaned if _E164.match(cleaned) else None

    # Nigerian conveniences:
    # 0 + 10 digits  ("0813...")  -> "+234813..."
    if cleaned.startswith("0") and len(cleaned) == 11:
        candidate = "+234" + cleaned[1:]
        return candidate if _E164.match(candidate) else None

    # 234 + 10 digits  ("234813...")  -> "+234813..."
    if cleaned.startswith("234") and len(cleaned) == 13:
        candidate = "+" + cleaned
        return candidate if _E164.match(candidate) else None

    # No country code we can safely infer.
    return None
