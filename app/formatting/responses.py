"""WhatsApp response formatting — concise, skimmable, forwardable to drivers.

Guiding rule (PRD §6.4): practical over technical, local over formal. Strip
bureaucratic tails ("Federal Capital Territory", postal codes, "Nigeria") that
users don't care about in a chat reply.
"""

from collections.abc import Sequence

from app.corridors.models import Corridor, Segment
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
    return (
        f"{name} is about {_format_km(route.distance_m)}, ~{_format_min(route.duration_s)}.\n"
        f"Map: {route.deep_link}"
    )


def format_corridor(
    corridor: Corridor,
    segments: Sequence[Segment],
    *,
    distance_m: float | None = None,
    duration_s: float | None = None,
    deep_link: str | None = None,
) -> str:
    """Render a corridor hit as numbered commuter steps.

    `segments` is the (possibly clipped) list to show — pass exactly what the
    user should follow from their join point onward.
    Distance/duration/deep_link are optional and come from LocationIQ between
    the user's actual coordinates and the corridor's destination, since the
    corridor's segments don't carry geographic distance themselves.
    """
    dest_name = corridor.destination.name
    lines: list[str] = [f"To {dest_name}:"]
    for i, s in enumerate(segments, start=1):
        line = f"{i}. {s.instruction}"
        extras: list[str] = []
        if s.cost_ngn:
            extras.append(f"₦{s.cost_ngn}")
        if s.duration_min:
            extras.append(f"~{s.duration_min} min")
        if extras:
            line += f" ({', '.join(extras)})"
        lines.append(line)

    footer: list[str] = []
    if distance_m is not None and duration_s is not None:
        footer.append(f"About {_format_km(distance_m)} · ~{_format_min(duration_s)}.")
    if corridor.applicability_notes:
        footer.append(corridor.applicability_notes)
    if deep_link:
        footer.append(f"Map: {deep_link}")
    if footer:
        lines.append("")
        lines.extend(footer)
    return "\n".join(lines)


def _format_km(distance_m: float) -> str:
    km = distance_m / 1000
    return f"{km:.1f} km" if km < 10 else f"{round(km)} km"


def _format_min(duration_s: float) -> str:
    return f"{round(duration_s / 60)} min"


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
