"""LocationIQ geocoding + POI client.

Forward geocoding is biased to Abuja via viewbox + Nigeria country filter.
Applies the alias dictionary before hitting the network so "banex" →
"Banex Plaza" without a round trip.

Strategy: first try bounded to the Abuja viewbox. If that returns nothing,
retry unbounded (still Nigeria-filtered and viewbox-biased) — this catches
places whose OSM coordinates tip slightly outside our box. The unbounded
results are then clipped to an Abuja-plus-0.5°-buffer box so a sparse name
like "Jabi motor park" can't return Ibadan / Bauchi / Benin City matches.
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

# Abuja-plus-buffer for the unbounded fallback. The strict viewbox lives at
# lng 7.24-7.70, lat 8.80-9.20. We widen by 0.5 deg in each direction. That
# covers nearby Nasarawa / Niger State edges where a few Abuja places have
# OSM coordinates that drift slightly outside the FCT box, but excludes
# Ibadan (lng ~3.9), Lagos (lng ~3.4), Bauchi (lat ~10.3), Benin (lng ~5.6).
_FALLBACK_BUFFER_DEG = 0.5
_FALLBACK_LON_MIN = 7.24 - _FALLBACK_BUFFER_DEG  # 6.74
_FALLBACK_LON_MAX = 7.70 + _FALLBACK_BUFFER_DEG  # 8.20
_FALLBACK_LAT_MIN = 8.80 - _FALLBACK_BUFFER_DEG  # 8.30
_FALLBACK_LAT_MAX = 9.20 + _FALLBACK_BUFFER_DEG  # 9.70


def _within_abuja_buffer(lat: float, lon: float) -> bool:
    return (
        _FALLBACK_LAT_MIN <= lat <= _FALLBACK_LAT_MAX
        and _FALLBACK_LON_MIN <= lon <= _FALLBACK_LON_MAX
    )


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
        # Unbounded fallback — but clip the results to an Abuja-plus-buffer
        # box. Without that filter a query like "Jabi motor park" (no Abuja
        # match in OSM) would surface motor parks from Ibadan or Bauchi.
        fallback = await _search(base_params)
        before = len(fallback)
        items = [it for it in fallback if _within_abuja_buffer(float(it["lat"]), float(it["lon"]))]
        log.info(
            "geocode_unbounded_fallback",
            query=expanded,
            raw=before,
            within_buffer=len(items),
        )

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
