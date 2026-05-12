# Phase 2e — Passthrough Anchors on Steps

**Date:** 2026-05-12
**Status:** Done. About to be committed.

## What we built

A step can now declare anchors it physically **passes through** without
breaking the leg into more sub-steps. A rider near a passthrough anchor
matches the corridor and gets the same boarding instruction as someone
boarding at the leg's `from` — because they're catching the same vehicle on
the way to the same destination.

## Why this came when it did

Contributors asked the natural question: "do I have to list every named
place along the route, even ones I don't change vehicles at?" Without
passthroughs, the answer was "yes if you want users near those places to
match." With passthroughs, the answer is "no — name them on the leg they
sit on, that's it."

## What's in place

### Schema

- `segments.passthrough_anchor_ids` — Postgres `ARRAY(UUID)` column,
  default empty list, server default `'{}'`.
- Migration `alembic/versions/21c02c28147c_add_segment_passthrough_anchor_ids.py`.
- Purely additive: existing corridors get `[]` and behave exactly as before.

### Repository ([app/corridors/repository.py](../../app/corridors/repository.py))

- `_corridor_anchors` — now returns endpoints PLUS all distinct passthrough
  anchors, deduped. One bulk SELECT keeps it cheap.
- `clip_segments_from_anchor` — first segment whose `from_anchor_id` OR
  passthrough list matches wins. Clipping starts from that segment.

### Submission ([app/corridors/submission.py](../../app/corridors/submission.py))

- `SegmentInput.passthroughs` — list of anchor names, accepts either a list
  or a comma-separated string.
- `cross_validate` enforces two rules:
  - Every passthrough name must appear in the submission's anchor list.
  - A passthrough cannot also be the step's `from` or `to`.
- `create_pending` resolves names to anchor ids and stores them on the
  segment row.

### Seed loader ([app/corridors/seed.py](../../app/corridors/seed.py))

- YAML segments accept an optional `passes_through:` list of anchor names.
- Same validation as the submission path (must be in the corridor's
  `anchors:` block).

### Web form

- New optional textarea per step row: **Passes through** — comma-separated
  anchor names. Hint text explains they must also appear in the Anchors
  list and reminds contributors what passthroughs are for.
- `_collect_submission_form` extracts `seg_passthroughs[]` and hands it to
  `SegmentInput` as-is (Pydantic does the comma-split).
- `_form_state_from_raw` re-populates the field after a validation error so
  the contributor doesn't have to retype.

## How it behaves at query time

User at **Police Signpost** asking for Jabi Park:
> To Jabi Park:
> 1. Take a taxi to Berger junction. (₦400, ~20 min)
> 2. Take another taxi from Berger to Jabi Park. (₦300, ~10 min)

User at **Federal Housing** (a passthrough on step 1) asking the same:
> To Jabi Park:
> 1. Take a taxi to Berger junction. (₦400, ~20 min)
> 2. Take another taxi from Berger to Jabi Park. (₦300, ~10 min)

Identical reply — because the contributor authored the instruction as a
boarding directive that names the leg's destination, not its origin.

## Files added

- `alembic/versions/21c02c28147c_add_segment_passthrough_anchor_ids.py`
- `tests/integration/test_passthroughs.py` — 9 tests covering repository
  lookup, clipping, submission validation, persistence, seed loader,
  and the full web flow

## Files changed

- `app/corridors/models.py` — `Segment.passthrough_anchor_ids`
- `app/corridors/repository.py` — `_corridor_anchors`, `clip_segments_from_anchor`
- `app/corridors/submission.py` — `SegmentInput.passthroughs`, cross-validation, persist
- `app/corridors/seed.py` — `passes_through` YAML key
- `app/web/routes.py` — form parsing + state restoration
- `app/web/templates/_segment_row.html` — new "Passes through" field

## State checks

- 215 tests pass (was 206 before this slice; +9)
- `ruff` clean
- `mypy` clean

## Authoring rule for contributors

> If your vehicle physically passes through a named place where another
> rider could realistically board it on the way, list that place as a
> passthrough on the step. The place must also appear in your Anchors list.

## Not in this slice

- **Reverse-direction matching** (Banex → Lugbe corridor matching a user
  at Berger). Corridors are still directional; you'd submit a separate
  corridor for the return trip.
- **Map preview of a corridor with its passthroughs** in the admin view.
  Worth doing once the corpus is bigger and admins want to spot wrong
  pins visually.
