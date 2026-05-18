from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx

from app.errors import ErrorKind, WNAError
from app.resolver.locationiq import _SEARCH_URL, geocode


@pytest.fixture(autouse=True)
def _settings() -> None:
    with patch("app.resolver.locationiq.get_settings") as gs:
        gs.return_value = MagicMock(locationiq_key="test_key")
        yield


@respx.mock
async def test_geocode_single_match_returns_one_place() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "9.0742",
                    "lon": "7.4817",
                    "display_name": "Banex Plaza, Wuse II, Abuja, Nigeria",
                    "importance": 0.5,
                }
            ],
        )
    )
    places = await geocode("banex")
    assert len(places) == 1
    assert places[0].lat == pytest.approx(9.0742)
    assert places[0].lon == pytest.approx(7.4817)
    assert places[0].display_name == "Banex Plaza, Wuse II, Abuja, Nigeria"


@respx.mock
async def test_geocode_keeps_low_importance_results_but_ranks_them_lower() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"lat": "9.07", "lon": "7.48", "display_name": "Banex Plaza", "importance": 0.5},
                {"lat": "9.08", "lon": "7.49", "display_name": "Random House", "importance": 0.05},
            ],
        )
    )
    places = await geocode("banex")
    assert [p.display_name for p in places] == ["Banex Plaza", "Random House"]


@respx.mock
async def test_geocode_dedupes_nearby_duplicates() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"lat": "9.0742", "lon": "7.4817", "display_name": "A", "importance": 0.5},
                {"lat": "9.0743", "lon": "7.4818", "display_name": "B", "importance": 0.5},
            ],
        )
    )
    places = await geocode("anywhere")
    assert len(places) == 1


@respx.mock
async def test_geocode_sorts_by_importance_descending() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"lat": "9.01", "lon": "7.40", "display_name": "low", "importance": 0.3},
                {"lat": "9.05", "lon": "7.45", "display_name": "high", "importance": 0.8},
            ],
        )
    )
    places = await geocode("anywhere")
    assert places[0].display_name == "high"


@respx.mock
async def test_geocode_applies_alias_before_request() -> None:
    route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=[]))
    await geocode("banex")
    sent_query = route.calls.last.request.url.params["q"]
    assert "Banex Plaza" in sent_query


@respx.mock
async def test_geocode_biases_to_abuja_and_nigeria() -> None:
    route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=[]))
    await geocode("somewhere")
    # First call is bounded-to-viewbox; fallback then retries unbounded.
    first = route.calls[0].request.url.params
    assert first["countrycodes"] == "ng"
    assert first["bounded"] == "1"
    assert "viewbox" in first


@respx.mock
async def test_geocode_retries_unbounded_when_bounded_is_empty() -> None:
    route = respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(
                200,
                json=[
                    {
                        "lat": "9.07",
                        "lon": "7.48",
                        "display_name": "Edge Place",
                        "importance": 0.4,
                    }
                ],
            ),
        ]
    )
    places = await geocode("somewhere")
    assert len(route.calls) == 2
    assert "bounded" not in route.calls[1].request.url.params
    assert len(places) == 1


@respx.mock
async def test_unbounded_fallback_drops_out_of_buffer_results() -> None:
    """A national fallback that returns Ibadan / Bauchi / Benin motor parks
    must be filtered out — they fail the Abuja-plus-buffer check."""
    respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=[]),  # bounded: empty
            httpx.Response(
                200,
                json=[
                    # Ibadan (lat ~7.4, lon ~3.9) — way south + west of Abuja.
                    {
                        "lat": "7.39",
                        "lon": "3.91",
                        "display_name": "Challenge Motor Park, Ibadan",
                        "importance": 0.3,
                    },
                    # Bauchi (lat ~10.3, lon ~9.8) — too far north + east.
                    {
                        "lat": "10.31",
                        "lon": "9.84",
                        "display_name": "Yankari Motor park, Bauchi",
                        "importance": 0.3,
                    },
                    # Benin City (lat ~6.3, lon ~5.6) — far south.
                    {
                        "lat": "6.34",
                        "lon": "5.62",
                        "display_name": "Osaro Motors Park, Benin City",
                        "importance": 0.3,
                    },
                ],
            ),
        ]
    )
    places = await geocode("jabi motor park")
    assert places == []


@respx.mock
async def test_unbounded_fallback_keeps_results_inside_buffer() -> None:
    """Real Abuja places sometimes have OSM coordinates that drift just
    outside the strict viewbox — the buffer is what catches them."""
    respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=[]),  # bounded: empty
            httpx.Response(
                200,
                json=[
                    # Just outside the strict viewbox (lat 8.80-9.20) but inside the buffer.
                    {
                        "lat": "8.75",  # below 8.80, above 8.30 (buffer min)
                        "lon": "7.45",
                        "display_name": "Edge of Abuja",
                        "importance": 0.4,
                    },
                ],
            ),
        ]
    )
    places = await geocode("edge place")
    assert len(places) == 1
    assert places[0].display_name == "Edge of Abuja"


@respx.mock
async def test_unbounded_fallback_mixed_results_keeps_only_in_buffer() -> None:
    """A mixed fallback — keep the Abuja-area row, drop the others."""
    respx.get(_SEARCH_URL).mock(
        side_effect=[
            httpx.Response(200, json=[]),
            httpx.Response(
                200,
                json=[
                    {
                        "lat": "7.39",
                        "lon": "3.91",
                        "display_name": "Ibadan thing",
                        "importance": 0.5,
                    },
                    {
                        "lat": "9.05",
                        "lon": "7.50",
                        "display_name": "Abuja thing",
                        "importance": 0.3,
                    },
                ],
            ),
        ]
    )
    places = await geocode("ambiguous")
    assert [p.display_name for p in places] == ["Abuja thing"]


@respx.mock
async def test_geocode_404_returns_empty_list() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(404, json={"error": "Unable to geocode"})
    )
    assert await geocode("nowhere") == []


@respx.mock
async def test_geocode_5xx_raises_geocode_fail() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(WNAError) as exc:
        await geocode("somewhere")
    assert exc.value.kind == ErrorKind.GEOCODE_FAIL


@respx.mock
async def test_geocode_timeout_raises_provider_timeout() -> None:
    respx.get(_SEARCH_URL).mock(side_effect=httpx.TimeoutException("slow"))
    with pytest.raises(WNAError) as exc:
        await geocode("somewhere")
    assert exc.value.kind == ErrorKind.PROVIDER_TIMEOUT


async def test_geocode_missing_key_raises() -> None:
    with patch("app.resolver.locationiq.get_settings") as gs:
        gs.return_value = MagicMock(locationiq_key="")
        with pytest.raises(WNAError) as exc:
            await geocode("banex")
    assert exc.value.kind == ErrorKind.GEOCODE_FAIL
