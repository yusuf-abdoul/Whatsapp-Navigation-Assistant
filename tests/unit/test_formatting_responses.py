from app.errors import ErrorKind
from app.formatting.responses import (
    format_ambiguity,
    format_error,
    format_route,
    short_name,
)
from app.routing.locationiq import Route
from app.session.state import Place


def test_short_name_strips_noise_tail() -> None:
    display = (
        "Banex Plaza, Kampala Street, Wuse 2, Abuja, "
        "Abuja Municipal Area Council (AMAC), Federal Capital Territory, 900288, Nigeria"
    )
    assert short_name(display) == "Banex Plaza, Kampala Street, Wuse 2"


def test_short_name_handles_none() -> None:
    assert short_name(None) == ""


def test_short_name_respects_max_parts() -> None:
    display = "A, B, C, D, E, Nigeria"
    assert short_name(display, max_parts=2) == "A, B"


def test_short_name_skips_postal_codes() -> None:
    display = "Jabi Lake Mall, Jabi, 900108, Abuja, Nigeria"
    assert short_name(display) == "Jabi Lake Mall, Jabi, Abuja"


def test_format_ambiguity_returns_prompt_and_short_options() -> None:
    candidates = [
        Place(query="banex", display_name="Banex Plaza, Wuse 2, Abuja, FCT, Nigeria"),
        Place(query="banex", display_name="Banex Bakery, Garki, Abuja, FCT, Nigeria"),
    ]
    prompt, options = format_ambiguity("banex", candidates)
    assert "banex" in prompt
    assert options == ["Banex Plaza, Wuse 2, Abuja", "Banex Bakery, Garki, Abuja"]


def test_format_error_returns_copy_per_kind() -> None:
    assert "trouble" in format_error(ErrorKind.GEOCODE_FAIL).lower()
    assert "route" in format_error(ErrorKind.ROUTE_FAIL).lower()
    assert "fast" in format_error(ErrorKind.RATE_LIMITED).lower()


def test_format_route_includes_distance_duration_and_link() -> None:
    dest = Place(query="jabi", display_name="Jabi Lake Mall, Jabi, Abuja")
    r = Route(distance_m=18500.0, duration_s=1620.0, deep_link="https://maps.example/route")
    out = format_route(dest, r)
    assert "Jabi Lake Mall" in out
    assert "18 km" in out  # 18.5 km, banker's rounding → 18
    assert "27 min" in out
    assert "https://maps.example/route" in out


def test_format_route_shows_one_decimal_under_10km() -> None:
    dest = Place(query="close", display_name="Close Place")
    r = Route(distance_m=3200.0, duration_s=600.0, deep_link="https://x")
    out = format_route(dest, r)
    assert "3.2 km" in out
    assert "10 min" in out
