"""WhatsApp response formatting — concise, skimmable, forwardable to drivers.

Guiding rule (PRD §6.4): practical over technical, local over formal. Strip
bureaucratic tails ("Federal Capital Territory", postal codes, "Nigeria") that
users don't care about in a chat reply.
"""

from collections.abc import Sequence

from app.corridors.models import Anchor, Corridor, Segment
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
    join_anchor: Anchor | None = None,
    end_anchor: Anchor | None = None,
    distance_m: float | None = None,
    duration_s: float | None = None,
    deep_link: str | None = None,
) -> str:
    """Render a corridor hit as numbered commuter steps.

    Consecutive same-mode segments are collapsed into one user-facing step.
    A run breaks when the mode changes OR when the next segment has
    ``transfer=True`` (explicit vehicle change).

    Instructions are synthesized from structure — mode, from-anchor, and
    to-anchor — not taken verbatim from the contributor's ``instruction``
    field. That way a rider joining mid-corridor at a passthrough sees their
    actual boarding point in the step.

    ``join_anchor`` is the user's boarding point. ``end_anchor`` is the user's
    actual destination — if set, the LAST step's "to" and the header line use
    its name instead of the corridor's canonical destination. That's what
    powers intermediate-anchor destinations ("How do I get to Berger" when
    Berger is on the Lugbe→Banex corridor but isn't its end).
    """
    final_dest = (end_anchor or corridor.destination).name
    lines: list[str] = [f"To {final_dest}:"]

    runs = _group_runs(segments)
    prev_to: str | None = None
    for i, run in enumerate(runs, start=1):
        if i == 1 and join_anchor is not None:
            effective_from = join_anchor.name
        elif prev_to is not None:
            effective_from = prev_to
        else:
            effective_from = run[0].from_anchor.name
        if i == len(runs) and end_anchor is not None:
            effective_to = end_anchor.name
        else:
            effective_to = run[-1].to_anchor.name

        line = f"{i}. {_synthesize_instruction(run[0].mode, effective_from, effective_to)}"
        cost = sum(s.cost_ngn or 0 for s in run) or None
        duration = sum(s.duration_min or 0 for s in run) or None
        extras: list[str] = []
        if cost:
            extras.append(f"₦{cost}")
        if duration:
            extras.append(f"~{duration} min")
        if extras:
            line += f" ({', '.join(extras)})"
        lines.append(line)
        prev_to = effective_to

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


def _synthesize_instruction(mode: str, from_anchor: str, to_anchor: str) -> str:
    """Build a per-step instruction from structured data.

    The phrasing intentionally avoids depending on the contributor's prose —
    so the same corridor produces correct text whether the rider boards at
    the canonical start or joins mid-route via a passthrough.
    """
    if mode == "walk":
        return f"Walk from {from_anchor} to {to_anchor}."
    if mode == "mixed":
        return f"Travel from {from_anchor} to {to_anchor}."
    return f"Take a {mode} from {from_anchor} to {to_anchor}."


def _group_runs(segments: Sequence[Segment]) -> list[list[Segment]]:
    """Split into runs of consecutive same-mode segments. A new run starts when
    the mode changes or the next segment has ``transfer=True``."""
    if not segments:
        return []
    runs: list[list[Segment]] = [[segments[0]]]
    for s in segments[1:]:
        prev = runs[-1][-1]
        if s.mode == prev.mode and not s.transfer:
            runs[-1].append(s)
        else:
            runs.append([s])
    return runs


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
