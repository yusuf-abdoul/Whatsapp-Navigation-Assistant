import re
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from app.errors import ErrorKind, WNAError
from app.routing.locationiq import route


@pytest.fixture(autouse=True)
def _settings() -> None:
    with patch("app.routing.locationiq.get_settings") as gs:
        gs.return_value = MagicMock(locationiq_key="test_key")
        yield


_ROUTE_URL = re.compile(r"https://us1\.locationiq\.com/v1/directions/driving/.*")


@respx.mock
async def test_route_returns_distance_duration_and_deep_link() -> None:
    respx.get(_ROUTE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"routes": [{"distance": 18500.0, "duration": 1620.0}]},
        )
    )
    r = await route(9.001, 7.400, 9.076, 7.421)
    assert r.distance_m == pytest.approx(18500.0)
    assert r.duration_s == pytest.approx(1620.0)
    assert "google.com/maps/dir" in r.deep_link
    assert "9.001,7.4" in r.deep_link
    assert "9.076,7.421" in r.deep_link


@respx.mock
async def test_route_sends_lon_lat_ordering() -> None:
    route_mock = respx.get(_ROUTE_URL).mock(
        return_value=httpx.Response(200, json={"routes": [{"distance": 0, "duration": 0}]})
    )
    await route(9.001, 7.400, 9.076, 7.421)
    path = route_mock.calls.last.request.url.path
    # OSRM convention: lon,lat;lon,lat
    assert "7.4,9.001;7.421,9.076" in path


@respx.mock
async def test_route_empty_routes_raises() -> None:
    respx.get(_ROUTE_URL).mock(return_value=httpx.Response(200, json={"routes": []}))
    with pytest.raises(WNAError) as exc:
        await route(9.001, 7.400, 9.076, 7.421)
    assert exc.value.kind == ErrorKind.ROUTE_FAIL


@respx.mock
async def test_route_5xx_raises_route_fail() -> None:
    respx.get(_ROUTE_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(WNAError) as exc:
        await route(9.001, 7.400, 9.076, 7.421)
    assert exc.value.kind == ErrorKind.ROUTE_FAIL


@respx.mock
async def test_route_timeout_raises_provider_timeout() -> None:
    respx.get(_ROUTE_URL).mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(WNAError) as exc:
        await route(9.001, 7.400, 9.076, 7.421)
    assert exc.value.kind == ErrorKind.PROVIDER_TIMEOUT


async def test_route_missing_key_raises() -> None:
    with patch("app.routing.locationiq.get_settings") as gs:
        gs.return_value = MagicMock(locationiq_key="")
        with pytest.raises(WNAError) as exc:
            await route(9.001, 7.400, 9.076, 7.421)
    assert exc.value.kind == ErrorKind.ROUTE_FAIL
