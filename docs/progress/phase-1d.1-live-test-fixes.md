# Phase 1d.1 — Live-Test Fixes

**Date:** 2026-05-05
**Status:** Done. About to be committed and pushed.

## What we built

Three concrete fixes from the first real WhatsApp smoke test, plus the data-model change needed to support them properly.

### Background

The first live test surfaced three problems in the corridor reply path:

1. **"Stay on until Berger junction"** as the FIRST step shown to a user joining at Car Wash made no sense — they hadn't boarded anything yet.
2. **"How about area 1 from police signpost"** got an UNKNOWN reply because the detector only handled destination phrasings, not inline origins.
3. **Typing "Police signpost"** as origin got LocationIQ-geocoded to the wrong coordinates — the system didn't try matching against known corridor anchors first.

## What's in place

### 1. Renderer collapses same-mode segments + a `transfer` flag

Same-mode consecutive segments now collapse into one user-facing step. A "transfer" flag on a segment marks an explicit vehicle change, which breaks the run even when the mode is unchanged.

Schema:
- New `segments.transfer` boolean column (default `false`, server default `false`)
- Migration: `alembic/versions/c4fa94f4d38c_add_transfer_flag_to_segments.py`

Renderer logic (`app/formatting/responses.py:format_corridor`):
- Walks the segment list, grouping consecutive segments by mode
- A new run starts when the mode changes OR when the next segment has `transfer=True`
- Each run is one rendered step using the LAST segment's instruction (so it names the run's destination)
- Cost and duration sum across the run

For the user joining at Car Wash on Lugbe→Banex:
```
To Banex Plaza:
1. Take a taxi to Berger junction. (₦200, ~15 min)
2. Take another taxi from Berger to Banex Plaza. (₦300, ~10 min)
```

Two steps instead of three "stay on" lines that didn't apply.

### 2. Anchor-aware origin resolution

A new repository function `find_anchor_by_name(query, *, city)` matches by exact name or alias, case-insensitive. The orchestrator's text-origin path tries this first; only falls back to LocationIQ when no anchor matches.

This means typing "Police signpost" now uses the anchor's lat/lon directly rather than letting LocationIQ find a nearby address.

### 3. Inline origin in direction queries

Detector now recognises the user's preferred phrasings:
- "How about Banex from Police Signpost"
- "How do I get to Banex from Lugbe"
- "from Lugbe to Banex"
- "directions to NNPC from Maitama"

`IntentResult` carries an optional `origin` field. When set, the orchestrator resolves both ends in one go and replies directly (no "share your live location" prompt). The same anchor-first lookup is used for the origin.

### 4. Seed authoring rules

Each segment's `instruction` is now a fresh boarding directive that names the segment's `to` anchor. Contributors authoring corridors should:
- Phrase as if the user is at `from_anchor` and boarding fresh ("Take a taxi to Berger junction.")
- Set `transfer: true` whenever the user must change vehicles, even if the mode is the same as the previous segment

The Lugbe→Banex YAML was rewritten following this rule. Other seeds didn't need changes (their consecutive segments are already different modes).

## Files added

- `alembic/versions/c4fa94f4d38c_add_transfer_flag_to_segments.py` — migration

## Files changed

- `app/corridors/models.py` — `Segment.transfer` boolean
- `app/corridors/seed.py` — reads `transfer` from YAML
- `app/corridors/repository.py` — added `find_anchor_by_name`
- `app/formatting/responses.py` — `format_corridor` collapses mode runs
- `app/intent/detector.py` — `_DIRECTION_WITH_FROM`, `_FROM_TO`; `IntentResult.origin`
- `app/flows/orchestrator.py` — anchor-first origin resolution; inline-origin path skips the location prompt
- `data/corridors/abuja/lugbe-to-banex.yaml` — rewritten per authoring rules
- `tests/unit/test_intent_detector.py` — 8 new tests for inline-origin parsing
- `tests/unit/test_formatting_responses.py` — 4 new tests for renderer collapse
- `tests/integration/test_corridors_repository.py` — 4 new tests for anchor lookup
- `tests/integration/conftest.py` — autouse truncate so tests don't see committed dev data

## State checks

- 129 tests pass (78 unit + 51 integration)
- `ruff check` clean
- `mypy` clean
- Re-seeded local Postgres: 12 anchors, 3 corridors, 10 segments; Banex corridor has its transfer flag set

## What's left in Phase 1

- **1d (rest)** — orchestrator integration is done; remaining is the next live smoke test against the rewritten Lugbe→Banex corridor and any further iteration that uncovers
- **More seed corridors** — the user wanted to widen the corpus before Phase 2
- **`format_route` reply still says "by car"** — already removed in directions-core; the live reply is "X km, ~Y min" with the map link

## Open questions for next phase

- Inline origin only resolves to known anchors right now. Should LocationIQ also be tried for origins given inline (e.g., "from Wuse 2" when Wuse 2 isn't an anchor in the destination corridor)? Current behaviour: fall back to the "share your location" prompt if anchor lookup misses, which feels OK but isn't tested live yet.
- The 2 km join radius for corridor selection is unverified. Will revisit after more live runs.
