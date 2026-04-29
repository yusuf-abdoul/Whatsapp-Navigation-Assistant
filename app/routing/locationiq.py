"""LocationIQ routing (OSRM) client.

Returns distance (meters), duration (seconds), and a deep link for users who
prefer a visual map. Turn-by-turn steps are deferred — they translate poorly
to WhatsApp without the map context, and local drivers rarely follow them.
"""

from dataclasses import dataclass

import httpx
import structlog

from app.config import get_settings
from app.errors import ErrorKind, WNAError

log = structlog.get_logger("routing")

_DIRECTIONS_URL_TMPL = "https://us1.locationiq.com/v1/directions/driving/{coords}"
_TIMEOUT_SECONDS = 8.0


@dataclass
class Route:
    distance_m: float
    duration_s: float
    deep_link: str


async def route(
    origin_lat: float,
    origin_lon: float,
    dest_lat: float,
    dest_lon: float,
) -> Route:
    settings = get_settings()
    if not settings.locationiq_key:
        raise WNAError(ErrorKind.ROUTE_FAIL, "locationiq key missing")

    # OSRM expects lon,lat ordering, semicolon-separated waypoints.
    coords = f"{origin_lon},{origin_lat};{dest_lon},{dest_lat}"
    url = _DIRECTIONS_URL_TMPL.format(coords=coords)
    params = {
        "key": settings.locationiq_key,
        "overview": "false",
        "steps": "false",
        "alternatives": "false",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(url, params=params)
    except httpx.TimeoutException as e:
        raise WNAError(ErrorKind.PROVIDER_TIMEOUT, "route timeout") from e

    if r.is_error:
        log.warning("route_http_error", status=r.status_code, body=r.text[:300])
        raise WNAError(ErrorKind.ROUTE_FAIL, f"route http {r.status_code}")

    payload = r.json()
    routes = payload.get("routes") or []
    if not routes:
        raise WNAError(ErrorKind.ROUTE_FAIL, "no route returned")

    top = routes[0]
    deep_link = _gmaps_link(origin_lat, origin_lon, dest_lat, dest_lon)
    return Route(
        distance_m=float(top["distance"]),
        duration_s=float(top["duration"]),
        deep_link=deep_link,
    )


def _gmaps_link(origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> str:
    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&origin={origin_lat},{origin_lon}"
        f"&destination={dest_lat},{dest_lon}"
        "&travelmode=driving"
    )
