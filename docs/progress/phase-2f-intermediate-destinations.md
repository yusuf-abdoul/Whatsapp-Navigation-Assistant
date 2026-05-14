# Phase 2f — Intermediate-anchor destinations

**Date:** 2026-05-14
**Status:** Done. About to be committed.

## What we built

Any anchor on a corridor — endpoint, mid-route stop, or passthrough — can
now be a query destination. Previously the bot could only answer "How do I
get to X?" when X was the canonical end of a corridor; anything mid-route
fell through to LocationIQ even when we had the structured data to answer
perfectly.

## Why this came when it did

The user just live-tested and asked for "Berger" — which is on the
Lugbe→Banex corridor but isn't its end. The corridor lookup returned
nothing and we fell to LocationIQ, which returned wrong matches. The data
existed; the lookup just wasn't smart enough.

## What's in place

### Repository

- `find_corridors_containing_anchor(anchor_id, *, city, only_approved)` —
  returns every corridor where the anchor appears as the corridor's
  destination, OR as any segment's from/to anchor, OR as an entry in any
  segment's passthrough list. One SQL statement (subselect for the
  segment-side hits).
- `clip_segments_between(segments, origin_id, destination_id)` — returns
  the slice of segments from origin to destination. Both can match either
  a segment endpoint or a passthrough. Returns `[]` for the wrong
  direction or when either end isn't found — corridors are one-way.

### Renderer

`format_corridor` now accepts an optional `end_anchor` parameter. When
set, both the header line ("To X:") and the last step's "to" use the
anchor's name instead of the corridor's canonical destination. This lets
the same corridor render two different destinations correctly for two
different users.

### Orchestrator

`_resolve_destination` (called when a user names a destination) now has a
three-tier resolution:

1. Try `find_corridors_by_destination(query)` — corridors that END at the
   destination. Existing path.
2. NEW: Try `find_anchor_by_name(query)` — any known anchor by name or
   alias. If found, we trust it as the destination even though no corridor
   ends there. The corridor-matching at reply time will handle the rest.
3. Else fall through to LocationIQ geocoding.

`_try_corridor_reply` (called when origin is captured) now iterates:

1. Try `find_corridors_by_destination(dest.query)`. If empty:
2. Resolve the destination to an anchor row, then call
   `find_corridors_containing_anchor(anchor_id)`. Each candidate is tested
   with `clip_segments_between(nearest_id, dest_anchor_id)`. The first
   candidate with a non-empty clip wins.
3. Render with `end_anchor` set to the user's destination — so the reply
   header says "To Berger" not "To Banex Plaza," and the last step's "to"
   is Berger, not Banex Plaza.

When a corridor's canonical destination matches (case 1), `end_anchor`
stays None and the renderer falls back to the corridor's destination —
existing behavior, no surprises.

### Error tolerance

`_lookup_corridor`, `_lookup_anchor`, and the new `_try_corridor_reply`
fallback path all catch a broader exception class than before. A
transient DB pool issue (which surfaced in unit tests with cross-loop
asyncpg) no longer breaks the conversation — the orchestrator silently
falls through to LocationIQ as if the corridor lookup returned nothing.

## How it behaves now

User: **"How do I get to Berger"** + shares location near Car Wash.

Before this slice:
> (No matching corridor → LocationIQ ambiguity → wrong answer)

After this slice:
> To Berger:
> 1. Take a taxi from Car Wash to Berger. (₦200, ~15 min)
>
> About 8 km · ~17 min.

The corridor (Lugbe→Banex) was matched via the new containing-anchor
lookup. `clip_segments_between` returned the slice from Car Wash (a
passthrough on segment 1) to Berger (segment 1's to-anchor). The renderer
used `end_anchor=Berger` to set both the header and the last step's
destination correctly. Cost/duration are approximations from the
segment-level data.

## Files added

- `tests/integration/test_intermediate_destinations.py` — 13 tests
  covering both repository functions and the renderer override.

## Files changed

- `app/corridors/repository.py` — `find_corridors_containing_anchor`,
  `clip_segments_between`
- `app/formatting/responses.py` — `end_anchor` parameter on
  `format_corridor`
- `app/flows/orchestrator.py` — three-tier destination resolution,
  candidate iteration for containing-match, broader DB error tolerance

## State checks

- 235 tests pass (was 222 before this slice; +13)
- `ruff` clean
- `mypy` clean

## What this enables next

With Gap 1 (synthesized instructions) and Gap 2 (intermediate
destinations) both shipped, the bot can answer any query where:
- Both the origin and destination are known anchors (or live-shared
  coordinates near a known anchor), AND
- They both appear on the same corridor in the right order

That's a meaningful step up from "corridor-end destinations only."

## Known limitations

- **Multiple candidate corridors with no ranking.** If Berger appears on
  three different corridors, we take the first one with a valid clip.
  Ranking by total distance / step count is a follow-up.
- **Cost/duration overshoot at passthrough destinations.** When the
  destination is a passthrough on the final clipped segment, we sum the
  full segment's cost/duration — the rider gets off before the segment's
  actual end. Slight overestimate; live with it.
- **Bug 2 from the live test still open.** Ambiguity prompts have no
  follow-up state — the user can't pick "1" or repeat the option label.
  Separate fix; not part of this slice.
