"""WhatsApp response formatting — concise, skimmable, forwardable to drivers.

Guiding rule (PRD §6.4): practical over technical, local over formal.
"""

from app.errors import ErrorKind
from app.routing.locationiq import Route
from app.session.state import Place


def format_route(origin: Place, destination: Place, route: Route) -> str:
    raise NotImplementedError


def format_nearby(category: str, places: list[Place]) -> str:
    raise NotImplementedError


def format_ambiguity(candidates: list[Place]) -> str:
    raise NotImplementedError


def format_error(kind: ErrorKind) -> str:
    raise NotImplementedError
