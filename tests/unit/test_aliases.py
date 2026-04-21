from app.resolver.aliases import expand


def test_known_alias_expands() -> None:
    assert expand("banex") == "Banex Plaza, Wuse 2, Abuja"


def test_unknown_returns_input() -> None:
    assert expand("some random query") == "some random query"


def test_case_insensitive() -> None:
    assert expand("BANEX") == "Banex Plaza, Wuse 2, Abuja"
