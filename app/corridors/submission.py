"""Contributor submission — accept, validate, persist as a pending corridor.

Mirrors the YAML seed loader in shape: anchors first (upsert by name+city),
then a corridor with ordered segments. The differences:

- Input comes from a web form (parsed into Pydantic models), not a file.
- Status is always ``"pending"`` until an admin approves it (Phase 2d).
- We record ``contributor_id`` so we can credit the user when their submission
  is approved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.corridors.models import SEGMENT_MODES, Anchor, Corridor, Segment

if TYPE_CHECKING:
    from collections.abc import Iterable


class SubmissionError(ValueError):
    """One or more validation errors caught after Pydantic-level checks."""


class AnchorInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    aliases: list[str] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        return v.strip()

    @field_validator("aliases", mode="before")
    @classmethod
    def _split_aliases(cls, v: object) -> list[str]:
        # Accept "a, b, c" from a single text input or already-list inputs.
        if isinstance(v, str):
            return [a.strip().lower() for a in v.split(",") if a.strip()]
        if isinstance(v, list):
            return [str(a).strip().lower() for a in v if str(a).strip()]
        return []


class SegmentInput(BaseModel):
    from_anchor: str = Field(min_length=1)
    to_anchor: str = Field(min_length=1)
    mode: str
    instruction: str = Field(min_length=1, max_length=500)
    transfer: bool = False
    cost_ngn: int | None = Field(default=None, ge=0)
    duration_min: int | None = Field(default=None, ge=0)
    # Anchor names this leg physically passes through. Must reference anchors
    # declared in the same submission. Comma-separated string or list accepted.
    passthroughs: list[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _mode_allowed(cls, v: str) -> str:
        if v not in SEGMENT_MODES:
            raise ValueError(f"mode must be one of {SEGMENT_MODES}, got '{v}'")
        return v

    @field_validator("from_anchor", "to_anchor", "instruction")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("passthroughs", mode="before")
    @classmethod
    def _split_passthroughs(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [a.strip() for a in v.split(",") if a.strip()]
        if isinstance(v, list):
            return [str(a).strip() for a in v if str(a).strip()]
        return []


class CorridorSubmission(BaseModel):
    city: str = Field(min_length=1, max_length=60)
    destination: str = Field(min_length=1, max_length=120)
    applicability_notes: str | None = Field(default=None, max_length=500)
    anchors: list[AnchorInput] = Field(min_length=1)
    segments: list[SegmentInput] = Field(min_length=1)

    @field_validator("city", "destination")
    @classmethod
    def _strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("applicability_notes")
    @classmethod
    def _strip_optional(cls, v: str | None) -> str | None:
        return v.strip() if v else None

    def cross_validate(self) -> None:
        """Catches inter-field rules AND canonicalizes anchor references.

        Anchor references (``destination``, ``from_anchor``, ``to_anchor``, and
        every entry in ``passthroughs``) are matched case-insensitively against
        anchor names AND aliases. After validation the fields are rewritten to
        the canonical anchor name so downstream code (the ``anchors_by_name``
        lookup in ``create_pending``) keeps working as a simple dict lookup.

        Contributors don't have to type the exact casing or remember which form
        they used in the anchors list — "fed housing" matches an anchor named
        "Federal Housing" with that alias just fine.
        """
        # Build a single lookup map: any name/alias (lowercased) → canonical anchor name.
        canonical_by_lookup: dict[str, str] = {}
        for a in self.anchors:
            canonical_by_lookup[a.name.lower()] = a.name
            for alias in a.aliases:  # already lowercased by AnchorInput's validator
                canonical_by_lookup.setdefault(alias, a.name)

        errors: list[str] = []

        canonical_names = [a.name for a in self.anchors]
        if len(set(canonical_names)) != len(canonical_names):
            errors.append("Anchor names must be unique within a submission.")

        dest_canon = canonical_by_lookup.get(self.destination.lower())
        if dest_canon is None:
            errors.append(f"Destination '{self.destination}' is not in the anchor list.")
        else:
            self.destination = dest_canon

        for i, s in enumerate(self.segments, start=1):
            from_canon = canonical_by_lookup.get(s.from_anchor.lower())
            to_canon = canonical_by_lookup.get(s.to_anchor.lower())
            if from_canon is None:
                errors.append(f"Segment {i}: 'from' anchor '{s.from_anchor}' is not declared.")
            else:
                s.from_anchor = from_canon
            if to_canon is None:
                errors.append(f"Segment {i}: 'to' anchor '{s.to_anchor}' is not declared.")
            else:
                s.to_anchor = to_canon
            if from_canon and to_canon and from_canon == to_canon:
                errors.append(f"Segment {i}: 'from' and 'to' anchors must differ.")

            resolved_passthroughs: list[str] = []
            for p in s.passthroughs:
                p_canon = canonical_by_lookup.get(p.lower())
                if p_canon is None:
                    errors.append(f"Segment {i}: passthrough '{p}' is not in the anchor list.")
                elif p_canon in (from_canon, to_canon):
                    errors.append(
                        f"Segment {i}: passthrough '{p_canon}' is already an endpoint of this step."
                    )
                else:
                    resolved_passthroughs.append(p_canon)
            s.passthroughs = resolved_passthroughs

        if errors:
            raise SubmissionError("\n".join(errors))


async def create_pending(
    db: AsyncSession, *, payload: CorridorSubmission, contributor_id: str
) -> Corridor:
    """Insert a pending corridor + segments, upserting anchors by (name, city).

    Caller is responsible for ``await db.commit()``.
    """
    payload.cross_validate()
    anchors_by_name = await _upsert_anchors(db, payload.anchors, payload.city)

    corridor = Corridor(
        destination_anchor_id=anchors_by_name[payload.destination].id,
        status="pending",
        applicability_notes=payload.applicability_notes,
        applicability_windows=[],
        contributor_id=contributor_id,
    )
    db.add(corridor)
    await db.flush()

    for i, s in enumerate(payload.segments, start=1):
        passthrough_ids = [anchors_by_name[name].id for name in s.passthroughs]
        db.add(
            Segment(
                corridor_id=corridor.id,
                sequence=i,
                from_anchor_id=anchors_by_name[s.from_anchor].id,
                to_anchor_id=anchors_by_name[s.to_anchor].id,
                mode=s.mode,
                instruction=s.instruction,
                transfer=s.transfer,
                cost_ngn=s.cost_ngn,
                duration_min=s.duration_min,
                passthrough_anchor_ids=passthrough_ids,
            )
        )
    await db.flush()
    # Eagerly load the segments collection so callers can iterate without
    # tripping the async lazy-load (`greenlet_spawn has not been called`).
    await db.refresh(corridor, attribute_names=["segments"])
    return corridor


async def _upsert_anchors(
    db: AsyncSession, raw_anchors: Iterable[AnchorInput], city: str
) -> dict[str, Anchor]:
    """Upsert anchors by (name, city), or reuse an existing anchor that's the
    same place under a different name.

    Match precedence:
    1. Exact ``(name, city)`` — the historical case.
    2. Alias match — new name already listed in an existing anchor's aliases.
    3. Token-subsequence match + geographic proximity — e.g. ``Shoprite
       Lugbe`` (existing) vs ``Shoprite Lugbe Bridge`` (new). One's tokenised
       name must be a contiguous run inside the other, AND the coordinates
       must be within ``_ANCHOR_MERGE_RADIUS_M``. Both required to avoid
       false-positive merges (a "Wuse Market" in Wuse vs a hypothetical
       "Wuse Market" further out would fail the proximity check).
    4. Synonym-normalised token-subsequence match + proximity — cheap
       dictionary of Nigerian landmark synonyms (see ``_LANDMARK_SYNONYMS``).
       Catches ``Police Signpost`` ↔ ``Police Signboard`` in the same area.

    On any match the incoming name is added as an alias of the existing anchor
    (unless it's already present). Coordinates are never overwritten — the
    admin console is the only path to correct pin drift.
    """
    by_name: dict[str, Anchor] = {}
    for raw in raw_anchors:
        # 1. Exact name match.
        existing = (
            await db.execute(select(Anchor).where(Anchor.name == raw.name, Anchor.city == city))
        ).scalar_one_or_none()

        # 2/3/4. Look for a plausible same-place match under a different name.
        if existing is None:
            existing = await _find_same_place(db, city, raw)

        if existing is None:
            anchor = Anchor(name=raw.name, lat=raw.lat, lon=raw.lon, city=city, aliases=raw.aliases)
            db.add(anchor)
        else:
            # Coordinates intentionally NOT updated — reviewer-only path.
            new_aliases = set(existing.aliases) | set(raw.aliases)
            # If we matched an existing anchor under a different name, record
            # the incoming name as an alias so future lookups succeed directly.
            if raw.name.strip().lower() != existing.name.strip().lower():
                new_aliases.add(raw.name.strip().lower())
            existing.aliases = sorted(new_aliases)
            anchor = existing
        by_name[raw.name] = anchor
    await db.flush()
    return by_name


# --- Same-place matching --------------------------------------------------
#
# Small dictionary of Nigerian landmark synonyms — pairs of tokens that name
# the same physical feature when they share a neighbourhood. Kept short and
# obvious; extend as the corpus grows. Applied both directions.
_LANDMARK_SYNONYMS: dict[str, set[str]] = {
    "signpost": {"signboard", "sign-post", "signage"},
    "signboard": {"signpost", "sign-board"},
    "bridge": {"overhead", "flyover"},
    "junction": {"roundabout", "intersection"},
    "estate": {"housing"},
}

# Merge radius. An existing anchor within this distance of the incoming
# coordinates, whose tokenised name overlaps, is treated as the same place.
_ANCHOR_MERGE_RADIUS_M = 800


def _tokens(name: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in name.lower())
    return [t for t in cleaned.split() if t]


def _is_token_subsequence(shorter: list[str], longer: list[str]) -> bool:
    """True when ``shorter`` appears as a contiguous run inside ``longer``."""
    if not shorter or len(shorter) > len(longer):
        return False
    for start in range(len(longer) - len(shorter) + 1):
        if longer[start : start + len(shorter)] == shorter:
            return True
    return False


def _synonymise(tokens: list[str]) -> list[list[str]]:
    """Return the token list plus one variant per known synonym substitution.

    Kept simple: at most one token replaced per variant. Enough to catch
    ``[police, signpost]`` matching ``[police, signboard]`` without exploding
    the search space.
    """
    variants = [tokens]
    for i, t in enumerate(tokens):
        for syn in _LANDMARK_SYNONYMS.get(t, set()):
            variant = list(tokens)
            variant[i] = syn
            variants.append(variant)
    return variants


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    from math import asin, cos, radians, sin, sqrt

    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * asin(sqrt(a))


async def _find_same_place(db: AsyncSession, city: str, raw: AnchorInput) -> Anchor | None:
    """Return an existing anchor that likely names the same physical place.

    Runs three checks in order, each requiring proximity within
    ``_ANCHOR_MERGE_RADIUS_M`` to guard against name-only false positives:
    alias match, token-subsequence match, synonym-normalised subsequence match.
    """
    candidates: list[Anchor] = list(
        (await db.execute(select(Anchor).where(Anchor.city == city))).scalars().all()
    )
    if not candidates:
        return None

    raw_name_lower = raw.name.strip().lower()
    raw_tokens = _tokens(raw.name)

    def _close_enough(a: Anchor) -> bool:
        return _haversine_m(raw.lat, raw.lon, a.lat, a.lon) <= _ANCHOR_MERGE_RADIUS_M

    # 2. Alias match.
    for a in candidates:
        if raw_name_lower in {alias.lower() for alias in a.aliases} and _close_enough(a):
            return a

    # 3. Token-subsequence match.
    for a in candidates:
        cand_tokens = _tokens(a.name)
        if not cand_tokens:
            continue
        shorter, longer = (
            (raw_tokens, cand_tokens)
            if len(raw_tokens) <= len(cand_tokens)
            else (cand_tokens, raw_tokens)
        )
        if _is_token_subsequence(shorter, longer) and _close_enough(a):
            return a

    # 4. Synonym-normalised subsequence match.
    for a in candidates:
        cand_tokens = _tokens(a.name)
        if not cand_tokens:
            continue
        for raw_variant in _synonymise(raw_tokens):
            for cand_variant in _synonymise(cand_tokens):
                shorter, longer = (
                    (raw_variant, cand_variant)
                    if len(raw_variant) <= len(cand_variant)
                    else (cand_variant, raw_variant)
                )
                if _is_token_subsequence(shorter, longer) and _close_enough(a):
                    return a
    return None
