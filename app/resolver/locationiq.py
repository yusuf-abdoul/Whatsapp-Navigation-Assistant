"""LocationIQ geocoding + POI client.

Forward geocoding is biased to Abuja via viewbox + Nigeria country filter.
Applies the alias dictionary before hitting the network so "banex" →
"Banex Plaza" without a round trip.

Strategy: first try bounded to the Abuja viewbox. If that returns nothing,
retry unbounded (still Nigeria-filtered and viewbox-biased) — this catches
places whose OSM coordinates tip slightly outside our box.
"""

from typing import Any

import httpx
import structlog

from app.config import get_settings
from app.errors import ErrorKind, WNAError
from app.resolver.aliases import expand
from app.session.state import Place

log = structlog.get_logger("resolver")

_SEARCH_URL = "https://api.locationiq.com/v1/search"
_TIMEOUT_SECONDS = 8.0

# Abuja FCT bounding box (lng_nw, lat_nw, lng_se, lat_se)
_ABUJA_VIEWBOX = "7.24,9.20,7.70,8.80"


async def geocode(query: str, limit: int = 5) -> list[Place]:
    expanded = expand(query)
    settings = get_settings()
    if not settings.locationiq_key:
        raise WNAError(ErrorKind.GEOCODE_FAIL, "locationiq key missing")

    base_params: dict[str, str] = {
        "key": settings.locationiq_key,
        "q": expanded,
        "format": "json",
        "limit": str(limit),
        "countrycodes": "ng",
        "viewbox": _ABUJA_VIEWBOX,
        "addressdetails": "0",
        "dedupe": "1",
    }

    items = await _search(base_params | {"bounded": "1"})
    if not items:
        items = await _search(base_params)  # fallback: unbounded
        if items:
            log.info("geocode_unbounded_fallback_hit", query=expanded, count=len(items))

    if not items:
        return []

    items.sort(key=lambda it: float(it.get("importance", 0.0)), reverse=True)
    log.info(
        "geocode_results",
        query=expanded,
        count=len(items),
        top_importance=float(items[0].get("importance", 0.0)),
        top_display=items[0].get("display_name", "")[:80],
    )

    places: list[Place] = []
    seen: set[tuple[float, float]] = set()
    for item in items:
        lat = float(item["lat"])
        lon = float(item["lon"])
        key = (round(lat, 3), round(lon, 3))  # ~100m grid
        if key in seen:
            continue
        seen.add(key)
        places.append(
            Place(query=expanded, lat=lat, lon=lon, display_name=item.get("display_name"))
        )
    return places


async def _search(params: dict[str, str]) -> list[dict[str, Any]]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            r = await client.get(_SEARCH_URL, params=params)
    except httpx.TimeoutException as e:
        raise WNAError(ErrorKind.PROVIDER_TIMEOUT, "geocode timeout") from e

    # LocationIQ returns 404 with a JSON body when no matches are found.
    if r.status_code == 404:
        return []
    if r.is_error:
        log.warning("geocode_http_error", status=r.status_code, body=r.text[:300])
        raise WNAError(ErrorKind.GEOCODE_FAIL, f"geocode http {r.status_code}")

    data = r.json()
    return list(data) if isinstance(data, list) else []


async def nearby(lat: float, lon: float, category: str, limit: int = 5) -> list[Place]:
    raise NotImplementedError
