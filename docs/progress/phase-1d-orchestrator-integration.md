# Phase 1d — Orchestrator Integration

**Date:** 2026-04-30
**Status:** Done. All 114 tests pass.

## What we built

Wired the corridor data layer into the actual conversation flow. The bot now answers a directions request with curated commuter steps when a corridor exists for that destination — and falls back to the LocationIQ distance/ETA reply when one doesn't. This is the first slice where the user sees a different reply because of the corridor work.

## How a real conversation flows now

1. User: **"How do I get to banex"**
2. Orchestrator looks up corridors by destination first.
3. Corridor hit → save the corridor's destination anchor (`Banex Plaza`) as the session destination, ask for origin. No LocationIQ geocode needed at this step.
4. User shares live location near Police Signpost.
5. Orchestrator finds the nearest corridor anchor (Police Signpost, ~50 m), checks it's within the 2 km join radius, clips the segment list to start from there, calls LocationIQ once for the distance/ETA footer, and sends:

```
To Banex Plaza:
1. Take a taxi heading Berger. (₦200, ~5 min)
2. Stay on past Federal Housing Bridge. (~5 min)
3. Stay on until Berger junction. (₦200, ~10 min)
4. Take another taxi from Berger to Banex Plaza. (₦300, ~10 min)

About 19 km · ~27 min.
All day; rush-hour traffic 7-9am and 5-7pm.
Map: https://maps.google.com/...
```

If no corridor exists, or the user is too far from any corridor anchor, the LocationIQ-only reply (distance + ETA + map link) takes over.

## Why this came when it did

Phases 1a–1c built the schema, the queries, and the seed data. Without 1d, none of that surfaced to the user — the orchestrator was still answering with raw LocationIQ replies. This phase is the payoff.

## Decisions made

- **Corridor first, LocationIQ second.** The orchestrator tries corridor lookup at both the destination-resolution step *and* the origin-resolution step. Origin step is the second check because we only know the user's location at that point.
- **Join radius = 2000 m.** If the user's nearest corridor anchor is more than 2 km away, the corridor's instructions don't apply ("take a bike from Newsite gate" makes no sense if you're 5 km away from the gate). Falls back to LocationIQ. Tunable constant in `app/flows/orchestrator.py`.
- **DB errors are non-fatal.** Both `_lookup_corridor` and `_try_corridor_reply` catch `SQLAlchemyError`, log a warning, and return the "no corridor" outcome. The user always gets *some* reply — corridor-down ≠ user-blocked.
- **Distance/ETA still come from LocationIQ.** Corridor segments don't carry geographic distance themselves; the LocationIQ route call gives us a single distance/ETA between the user's actual coordinates and the corridor's destination anchor for the footer.
- **Reply copy dropped "by car."** Users are commuters by default; the corridor steps name the actual mode (taxi / keke / walk / bike). The LocationIQ-fallback reply now reads "Banex Plaza is about 19 km, ~27 min." — neutral.

## Files added

- `tests/integration/test_orchestrator_corridor.py` — 3 end-to-end integration tests:
  - corridor reply with all four numbered steps when user is at the start
  - clipped reply when user joins mid-corridor (only the suffix shows)
  - LocationIQ fallback when user is too far from any anchor

## Files changed

- `app/formatting/responses.py` — added `format_corridor`; refactored distance/duration formatting into shared helpers; tightened `format_route` (no more "by car")
- `app/flows/orchestrator.py` — corridor lookup at destination resolution; `_try_corridor_reply` at origin resolution; SQLAlchemyError handling; new `Event.ROUTE_SUCCESS` payload includes `source` (`corridor` / `locationiq`)
- `data/corridors/abuja/lugbe-to-banex.yaml` — final segment fixed: taxi (not keke) from Berger to Banex; kekes don't operate at Berger
- `tests/integration/conftest.py` — autouse fixture truncates corridor tables before every integration test and resets the orchestrator engine singleton; keeps tests isolated from dev-seed data and from each other
- `tests/unit/test_orchestrator.py` — autouse fixture mocks `_lookup_corridor` and `_try_corridor_reply` so the existing unit tests continue to exercise the LocationIQ fallback path without needing a database

## State checks

- 114 tests pass (78 unit + 36 integration)
- `ruff check` clean
- `mypy` clean
- Local Postgres re-seeded with the corrected `lugbe-to-banex.yaml`

## Worth knowing

- **Two LocationIQ calls in the corridor path:** one for the geocode-ish lookup happens only as a fallback (corridor miss); the route call always runs once for the distance/ETA footer when a corridor matches. We could skip the route call if we want a leaner reply.
- **Anchor lat/lon precision matters.** The seed YAMLs have anchors to 3 decimal places (~100 m). If two anchors are very close, the nearest-anchor calc could pick the wrong one. Bumping to 5 decimals (~1 m) is straightforward when we get real GPS-captured anchors from contributors.
- **Single-corridor pick.** When multiple corridors match a destination, we currently pick the first. Time-window-aware ranking is the obvious next step — the schema already supports `applicability_windows`, but the logic isn't built.

## What's left in Phase 1

- **Wider seed set.** Three corridors covers the demo flows but not much more. Adding 5–10 more would surface failure modes earlier.
- **Reply quality pass with the team.** The numbered-step format is functional; copy review is a separate task.

## What unlocks now

The product loop works end-to-end for known corridors. Logical next phases:

- **Phase 2** — contributor portal: web form for guided corridor submission, admin review queue, basic auth (probably WhatsApp-OTP-based, since that's the user's existing identifier). This is where the real-world data starts coming in.
- **Phase 3** — credit/reward ledger and payouts.
- **Phase 4** — LLM-assisted dedup in the admin review path.
