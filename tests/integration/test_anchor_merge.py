"""Same-place anchor merging on submission.

When a contributor submits an anchor whose name closely matches an existing
anchor in the same city AND sits near enough to it geographically, we treat
them as the same place: reuse the existing row and add the new name as an
alias. This prevents a corpus littered with "Shoprite Lugbe" and "Shoprite
Lugbe Bridge" as distinct anchors at the same physical location.

The proximity check is required — name similarity alone would falsely merge
two anchors with the same base name in different neighbourhoods.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.corridors.models import Anchor
from app.corridors.submission import (
    AnchorInput,
    _find_same_place,
    _is_token_subsequence,
    _tokens,
    _upsert_anchors,
)


def test_tokens_lowers_and_strips_punctuation() -> None:
    assert _tokens("Shoprite Lugbe, Abuja") == ["shoprite", "lugbe", "abuja"]
    assert _tokens("Police-Signpost") == ["police", "signpost"]


def test_token_subsequence_matches_contiguous_prefix() -> None:
    assert _is_token_subsequence(["shoprite", "lugbe"], ["shoprite", "lugbe", "bridge"])


def test_token_subsequence_matches_contiguous_suffix() -> None:
    assert _is_token_subsequence(["housing", "bridge"], ["federal", "housing", "bridge"])


def test_token_subsequence_rejects_non_contiguous() -> None:
    assert not _is_token_subsequence(["a", "c"], ["a", "b", "c"])


def test_token_subsequence_rejects_empty_shorter() -> None:
    assert not _is_token_subsequence([], ["a"])


# --- DB-backed merge behavior ------------------------------------------


async def test_upsert_reuses_anchor_when_name_extends_existing_and_close(db) -> None:
    """The existing 'Shoprite Lugbe' should be reused for a new
    'Shoprite Lugbe Bridge' with coordinates 300m away."""
    existing = Anchor(
        name="Shoprite Lugbe",
        lat=8.99,
        lon=7.39,
        city="abuja",
        aliases=[],
    )
    db.add(existing)
    await db.flush()

    raw = AnchorInput(name="Shoprite Lugbe Bridge", lat=8.9927, lon=7.3928)
    result = await _upsert_anchors(db, [raw], city="abuja")

    # The dict is keyed by raw name; the row it points to is the pre-existing one.
    assert result["Shoprite Lugbe Bridge"].id == existing.id
    assert "shoprite lugbe bridge" in existing.aliases
    # Coordinates were NOT overwritten.
    assert existing.lat == 8.99


async def test_upsert_does_not_merge_when_far_apart(db) -> None:
    """Two 'Shoprite' anchors in different neighborhoods stay distinct."""
    existing = Anchor(
        name="Shoprite Wuse",
        lat=9.07,
        lon=7.48,
        city="abuja",
        aliases=[],
    )
    db.add(existing)
    await db.flush()

    # Same base token but ~11km away — proximity check rejects.
    raw = AnchorInput(name="Shoprite Wuse Extension", lat=8.98, lon=7.40)
    result = await _upsert_anchors(db, [raw], city="abuja")

    assert result["Shoprite Wuse Extension"].id != existing.id
    # A new row was created.
    all_anchors = (await db.execute(select(Anchor))).scalars().all()
    assert len(all_anchors) == 2


async def test_upsert_synonym_merges_signpost_signboard(db) -> None:
    """Police Signpost and Police Signboard at the same spot in Lugbe should
    resolve to the same anchor via the synonym dictionary."""
    existing = Anchor(
        name="Police Signpost",
        lat=8.972,
        lon=7.364,
        city="abuja",
        aliases=[],
    )
    db.add(existing)
    await db.flush()

    raw = AnchorInput(name="Police Signboard", lat=8.9723, lon=7.3642)
    result = await _upsert_anchors(db, [raw], city="abuja")

    assert result["Police Signboard"].id == existing.id
    assert "police signboard" in existing.aliases


async def test_upsert_synonym_does_not_merge_when_far_apart(db) -> None:
    """Same synonym pair but different neighborhoods → separate rows."""
    existing = Anchor(
        name="Police Signpost",
        lat=8.972,
        lon=7.364,
        city="abuja",
        aliases=[],
    )
    db.add(existing)
    await db.flush()

    # Same synonym pair, but this one is >10km away.
    raw = AnchorInput(name="Police Signboard", lat=9.08, lon=7.48)
    result = await _upsert_anchors(db, [raw], city="abuja")

    assert result["Police Signboard"].id != existing.id


async def test_upsert_matches_existing_alias(db) -> None:
    """If the incoming name is already stored as an alias on an existing
    anchor nearby, reuse that anchor even without a token match."""
    existing = Anchor(
        name="Federal Housing",
        lat=8.978,
        lon=7.375,
        city="abuja",
        aliases=["fed housing", "fh"],
    )
    db.add(existing)
    await db.flush()

    raw = AnchorInput(name="FH", lat=8.9782, lon=7.3751)  # ~30m away
    result = await _upsert_anchors(db, [raw], city="abuja")
    assert result["FH"].id == existing.id


async def test_find_same_place_returns_none_when_no_match(db) -> None:
    """No existing anchor → None, no merge attempted."""
    result = await _find_same_place(
        db, "abuja", AnchorInput(name="Brand New Place", lat=9.0, lon=7.4)
    )
    assert result is None


@pytest.mark.parametrize("city", ["lagos", "wuse", "kaduna"])
async def test_find_same_place_scoped_by_city(db, city: str) -> None:
    """An anchor in a different city is invisible to the merge search."""
    existing = Anchor(
        name="Shoprite Lugbe",
        lat=8.99,
        lon=7.39,
        city="abuja",
        aliases=[],
    )
    db.add(existing)
    await db.flush()

    result = await _find_same_place(
        db, city, AnchorInput(name="Shoprite Lugbe Bridge", lat=8.99, lon=7.39)
    )
    assert result is None
