"""WhatsApp response formatting — concise, skimmable, forwardable to drivers.

Guiding rule (PRD §6.4): practical over technical, local over formal. Strip
bureaucratic tails ("Federal Capital Territory", postal codes, "Nigeria") that
users don't care about in a chat reply.
"""

from app.errors import ErrorKind
from app.routing.locationiq import Route
from app.session.state import Place

_NOISE_PARTS = {
    "nigeria",
    "federal capital territory",
    "abuja municipal area council (amac)",
    "abuja municipal area council",
    "amac",
}


def short_name(display_name: str | None, *, max_parts: int = 3) -> str:
    """Trim a LocationIQ display_name to something chat-friendly.

    LocationIQ returns e.g. "Banex Plaza, Kampala St, Wuse 2, Abuja, AMAC, FCT,
    900288, Nigeria". We keep leading meaningful parts, skip administrative
    noise, and cap length.
    """
    if not display_name:
        return ""
    parts = [p.strip() for p in display_name.split(",")]
    kept: list[str] = []
    for p in parts:
        if p.lower() in _NOISE_PARTS:
            continue
        if p.isdigit():  # postal code
            continue
        kept.append(p)
        if len(kept) >= max_parts:
            break
    return ", ".join(kept)


def format_ambiguity(query: str, candidates: list[Place]) -> tuple[str, list[str]]:
    prompt = f"I found a few matches for '{query}'. Which one?"
    options = [short_name(c.display_name) or query for c in candidates]
    return prompt, options


def format_route(destination: Place, route: Route) -> str:
    name = short_name(destination.display_name) or destination.query
    km = route.distance_m / 1000
    minutes = round(route.duration_s / 60)
    km_str = f"{km:.1f} km" if km < 10 else f"{round(km)} km"
    return f"{name} is ~{km_str} away, ~{minutes} min by car.\nMap: {route.deep_link}"


_ERROR_MESSAGES: dict[ErrorKind, str] = {
    ErrorKind.GEOCODE_FAIL: "I had trouble looking that up. Please try again in a moment.",
    ErrorKind.ROUTE_FAIL: "I couldn't work out a route right now. Please try again.",
    ErrorKind.AMBIGUOUS: "I'm not sure which one you mean. Could you be more specific?",
    ErrorKind.RATE_LIMITED: (
        "You're sending messages a bit fast. Please wait a moment and try again."
    ),
    ErrorKind.PROVIDER_TIMEOUT: "My map service is slow to respond. Please try again.",
    ErrorKind.UNKNOWN_INTENT: (
        "I didn't understand. Try: 'How do I get to Jabi Lake Mall?' or 'pharmacies near me'."
    ),
}


def format_error(kind: ErrorKind) -> str:
    return _ERROR_MESSAGES.get(kind, _ERROR_MESSAGES[ErrorKind.UNKNOWN_INTENT])
