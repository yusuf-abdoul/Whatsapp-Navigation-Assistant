import pytest

from app.intent.detector import detect
from app.intent.types import Intent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("How do I get to Banex Plaza?", "Banex Plaza"),
        ("how do i get to jabi lake mall", "jabi lake mall"),
        ("directions to NNPC towers", "NNPC towers"),
        ("route to wuse market", "wuse market"),
        ("where is Transcorp Hilton", "Transcorp Hilton"),
        ("take me to Silverbird Cinema", "Silverbird Cinema"),
        ("go to Jabi Lake Mall.", "Jabi Lake Mall"),
        ("navigate to Dunes Centre", "Dunes Centre"),
    ],
)
def test_direction_intent_extracts_query(text: str, expected: str) -> None:
    result = detect(text)
    assert result.intent == Intent.DIRECTION
    assert result.query == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("nearest pharmacy", "pharmacy"),
        ("closest ATM", "ATM"),
        ("pharmacies near me", "pharmacies"),
        ("restaurants nearby", "restaurants"),
        ("hospitals around me", "hospitals"),
    ],
)
def test_nearby_intent_extracts_category(text: str, expected: str) -> None:
    result = detect(text)
    assert result.intent == Intent.NEARBY
    assert result.query == expected


@pytest.mark.parametrize("text", ["help", "HELP", "?", "menu", "hi", "hello"])
def test_help_intent(text: str) -> None:
    assert detect(text).intent == Intent.HELP


@pytest.mark.parametrize(
    "text", ["cancel", "stop", "reset", "start over", "nvm", "never mind", "quit"]
)
def test_cancel_intent(text: str) -> None:
    assert detect(text).intent == Intent.CANCEL


@pytest.mark.parametrize("text", ["", "   ", "asdfgh", "random words"])
def test_unknown_intent(text: str) -> None:
    assert detect(text).intent == Intent.UNKNOWN
