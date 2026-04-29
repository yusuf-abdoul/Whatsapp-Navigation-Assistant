# Phase 1b — Corridor Repository

**Date:** 2026-04-27
**Status:** Done. Not yet committed.

## What we built

The read-side data layer that sits between the orchestrator and the database. Three functions:

- **`find_corridors_by_destination(query, ...)`** — given a destination phrase ("banex", "Jabi Lake Mall"), returns approved corridors ending at a matching anchor. Matches the anchor's `name` *or* any of its `aliases`, case-insensitively. Optional filters: scope to a city, include unapproved corridors.
- **`nearest_anchor_in_corridor(corridor_id, lat, lon)`** — finds the closest anchor on a corridor to a user's coordinates. Returns the anchor and the distance in metres, or `None` if the corridor has no segments. Brute-force haversine — fine for thousands of anchors; PostGIS is the upgrade path.
- **`clip_segments_from_anchor(segments, anchor_id)`** — given an ordered segment list and an anchor the user is joining at, returns the suffix of the route from that point. If the anchor is only ever a destination (never a `from_anchor`), returns empty — there's no route forward from there.

Plus an internal `_haversine_m` helper exposed for testing.

## Why this exists separately from the orchestrator

The repository has one job: turn a query into rows. No formatting, no fallback logic, no LocationIQ calls, no events. That keeps the orchestrator small and lets us unit-test the data path in isolation.

## How a query flows through it

A user asks "How do I get to banex?" and shares their location:

1. Orchestrator passes `"banex"` to `find_corridors_by_destination` → gets one approved corridor ending at Banex Plaza.
2. Orchestrator iterates candidate corridors (just one for now), calls `nearest_anchor_in_corridor` with the user's lat/lon → gets the closest anchor and its distance.
3. If that distance is reasonable (e.g. < 2 km), call `clip_segments_from_anchor` to get the route suffix from that anchor onward.
4. Render those segments as numbered steps. Done.

If no corridor matches, fall back to LocationIQ-only (the existing path).

## Files added

- `app/corridors/repository.py` — the three functions above + haversine helper
- `tests/integration/test_corridors_repository.py` — 15 tests covering: case-insensitive name match, alias match, no-match returns empty, approved-only by default, city scoping, segments load in sequence order, nearest-anchor picks the closest point, returns None for empty/unknown corridor, segment clipping at every position on a corridor, haversine sanity (zero distance, known separation)

## Files changed

None — the repository is purely additive on top of the schema.

## Worth noting

- The seed data in tests now uses **four segments** (`police → carwash → fed_bridge → berger → banex`) instead of two. Originally I encoded "passes Car Wash" as text inside an instruction — but the repository can only reason over anchors that are first-class segment endpoints. Lesson for contributors: every notable stop along a corridor should be its own anchor, not a phrase in the prose.
- City scoping is optional and defaults to off. Once we launch in multiple cities we'll likely want to enforce it from the orchestrator side.
- "Approved only" is the default. Pending corridors are visible to admin tooling (when it exists) but never to end users.

## State checks

- 104 tests pass (78 original unit + 11 schema integration + 15 repository integration)
- `ruff check` clean
- `mypy` clean

## What's left in Phase 1

- **1c — Seed corridors.** Hand-author 2–3 real Abuja corridors (Lugbe→Banex, Lugbe→Area1, etc.) as YAML/JSON + a loader script. Without seed data, the orchestrator integration in 1d has nothing to look up.
- **1d — Orchestrator integration.** Hook corridor lookup into the conversation flow so a known route returns curated steps; unknown destinations still get the LocationIQ fallback.
- New reply renderer that formats a corridor hit as numbered steps (`format_corridor` to sit alongside `format_route` in `app/formatting/responses.py`).

## Open question for next phase

When two corridors match the same destination (e.g. one rush-hour, one off-peak), how does the orchestrator choose? The schema supports `applicability_windows` already, but ranking logic isn't built yet. Simplest first cut: if multiple match, prefer the one whose time window includes "now"; if none does, pick the most recently approved.
