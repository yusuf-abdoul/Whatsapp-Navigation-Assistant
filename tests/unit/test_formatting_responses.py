from unittest.mock import MagicMock

from app.errors import ErrorKind
from app.formatting.responses import (
    format_ambiguity,
    format_corridor,
    format_error,
    format_route,
    short_name,
)
from app.routing.locationiq import Route
from app.session.state import Place


def _seg(*, mode, instruction, transfer=False, cost_ngn=None, duration_min=None):
    s = MagicMock()
    s.mode = mode
    s.instruction = instruction
    s.transfer = transfer
    s.cost_ngn = cost_ngn
    s.duration_min = duration_min
    return s


def _corridor(dest_name, *, applicability_notes=None):
    c = MagicMock()
    c.destination.name = dest_name
    c.applicability_notes = applicability_notes
    return c


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


def test_format_corridor_collapses_consecutive_same_mode_segments() -> None:
    c = _corridor("Banex Plaza")
    segs = [
        _seg(
            mode="taxi", instruction="Take a taxi to Berger junction.", cost_ngn=200, duration_min=5
        ),
        _seg(mode="taxi", instruction="Take a taxi to Berger junction.", duration_min=5),
        _seg(
            mode="taxi",
            instruction="Take a taxi to Berger junction.",
            cost_ngn=200,
            duration_min=10,
        ),
        _seg(
            mode="taxi",
            transfer=True,
            instruction="Take another taxi from Berger to Banex Plaza.",
            cost_ngn=300,
            duration_min=10,
        ),
    ]
    out = format_corridor(c, segs)
    # Three same-mode segments collapse into step 1; transfer-marked seg is step 2.
    assert "1." in out
    assert "2." in out
    assert "3." not in out
    assert "Take a taxi to Berger junction" in out
    assert "Take another taxi from Berger to Banex Plaza" in out
    # Cost/duration sum across the collapsed run (200+0+200=400, 5+5+10=20).
    assert "₦400" in out
    assert "20 min" in out
    # Transfer step keeps its own totals.
    assert "₦300" in out


def test_format_corridor_breaks_runs_on_mode_change() -> None:
    c = _corridor("Area 1 Shopping Complex")
    segs = [
        _seg(
            mode="bike", instruction="Take a bike to Police Signpost.", cost_ngn=200, duration_min=5
        ),
        _seg(mode="walk", instruction="Cross the pedestrian bridge.", duration_min=3),
        _seg(mode="car", instruction="Take a car to Area 1 Bridge.", cost_ngn=400, duration_min=25),
        _seg(mode="walk", instruction="Walk to the complex.", duration_min=12),
    ]
    out = format_corridor(c, segs)
    # Each mode change starts a fresh step.
    for n in ("1.", "2.", "3.", "4."):
        assert n in out


def test_format_corridor_includes_applicability_and_footer() -> None:
    c = _corridor("Banex Plaza", applicability_notes="Rush-hour traffic 7-9am.")
    segs = [_seg(mode="taxi", instruction="Take a taxi to Banex.", cost_ngn=200, duration_min=10)]
    out = format_corridor(c, segs, distance_m=18500.0, duration_s=1620.0, deep_link="https://maps")
    assert "Rush-hour traffic 7-9am." in out
    assert "About 18 km" in out
    assert "27 min" in out
    assert "Map: https://maps" in out


def test_format_corridor_handles_no_extras_gracefully() -> None:
    c = _corridor("Somewhere")
    segs = [_seg(mode="walk", instruction="Walk down the street.")]
    out = format_corridor(c, segs)
    assert "1. Walk down the street." in out
    assert "₦" not in out
    assert "min" not in out
