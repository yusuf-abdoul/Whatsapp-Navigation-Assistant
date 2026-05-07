"""Phone-number normalization tests."""

import pytest

from app.auth.phone import normalize


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Already E.164
        ("+2348123456789", "+2348123456789"),
        ("+1 415 523 8886", "+14155238886"),
        ("  +234 (812) 345-6789 ", "+2348123456789"),
        # Nigerian local — leading 0
        ("08123456789", "+2348123456789"),
        ("0812 345 6789", "+2348123456789"),
        # Nigerian without +
        ("2348123456789", "+2348123456789"),
        ("234 812 345 6789", "+2348123456789"),
    ],
)
def test_normalize_accepts_common_forms(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "abc",
        "1234",  # too short, no recognised prefix
        "9999",  # ditto
        "0812345",  # too short for Nigerian local
        "+2",  # too short for E.164
    ],
)
def test_normalize_rejects_unparseable(raw: str | None) -> None:
    assert normalize(raw) is None
