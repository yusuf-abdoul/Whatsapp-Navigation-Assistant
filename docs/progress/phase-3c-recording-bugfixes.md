# Phase 3c — Recording bugfixes + same-place anchor merging

**Date:** 2026-07-02
**Status:** Ready to commit.

## What triggered this

Live dogfooding on the WhatsApp bot surfaced three problems on the first
real recording attempt (Police Signpost → Shoprite Lugbe):

1. **Destination never captured.** Saying `end` after describing the final
   leg silently dropped the pending leg and jumped straight to a summary
   that missed the destination anchor. Submission then failed with
   `Destination 'Shoprite Lugbe' is not in the anchor list.`
2. **Fare re-prompted at every anchor.** The bot asked for a mode+fare
   after every `same vehicle` answer, treating each landmark as a segment
   endpoint. Reality: one fare per vehicle-segment; intermediate
   landmarks are passthroughs on that segment.
3. **Same physical place, different name → duplicate anchor rows.** The
   contributor's `Shoprite Lugbe Bridge` should resolve to the existing
   `Shoprite Lugbe` anchor when they sit at the same coordinates.

All three are fixed. The corridor schema hasn't changed — passthroughs
have been part of the model since Phase 2e; the recording flow just
wasn't using them.

## Changes

### 1. Destination location capture on `end`

New session state: `destination_location` awaiting cursor.

`end_recording` now decides:

- **No trip started** (no anchors OR still in intro questions OR no legs
  described yet) → discard with "Not enough recorded yet."
- **Mid-anchor location share pending** (`awaiting=next_anchor_location`,
  contributor said "now at X" but never shared) → gently nudge to
  finish or `cancel`.
- **`changed` with no leg described yet** → nudge for the new
  segment's mode+fare.
- **Destination not captured** (pending_leg set OR last anchor's name ≠
  destination name) → transition to `destination_location`, prompt for
  live location share.
- **Everything captured** → build summary directly.

New handler `_handle_destination_location` receives the location share,
appends the final anchor named after `destination_name`, promotes any
pending leg to the segment list, and shows the summary.

### 2. Passthrough model on `same`

`_handle_transfer_decision` no longer routes every answer to `leg_info`.

- `same` → mark the just-recorded anchor as a passthrough on the current
  segment (`is_passthrough=True`), transition to `next_anchor_name` with
  no fare re-prompt. Message: "OK, same taxi continues. Tell me when you
  reach the next stop, or 'end' if you've arrived."
- `changed` → set `state.next_leg_is_transfer=True`, transition to
  `leg_info` (existing behavior for new segment).

`_handle_leg_info` reads `next_leg_is_transfer` to set the new leg's
`transfer` flag, then resets it. The old "seed pending_leg with taxi
default" hack is gone.

### 3. Segment builder + submission

`_build_segments` groups anchors into segments:

- Endpoints = anchors with `is_passthrough=False`.
- The i-th (endpoint, next-endpoint) pair defines segment i.
- Anchors strictly between them = passthroughs on that segment.
- Duration = wall-clock delta between the two endpoint timestamps.
- Each segment consumes one entry from `legs` in order.

`_submit` now iterates segments (not raw legs) and constructs
`SegmentInput(..., passthroughs=[...])` — the corridor schema already
supports passthroughs, we now feed them.

### 4. Destination-name shortcut

If the contributor types the destination's name as an intermediate
anchor (`now at Shoprite Lugbe`), `_handle_next_anchor_location` skips
the transfer-decision prompt and goes straight to the summary. Saves
one turn at the end of every trip.

### 5. Alias / same-place anchor merging on submission

`app/corridors/submission.py` — `_upsert_anchors` now runs a
`_find_same_place` check before creating a new anchor row. Match rules
(all require `_ANCHOR_MERGE_RADIUS_M = 800m` proximity):

- **Alias match** — incoming name is already in an existing anchor's
  `aliases`.
- **Token-subsequence match** — one name's tokens are a contiguous run
  inside the other's. `Shoprite Lugbe` ⊂ `Shoprite Lugbe Bridge` →
  match. `Federal Housing` ⊂ `Federal Housing Bridge` → match.
- **Synonym-normalised subsequence match** — small dictionary of
  Nigerian landmark synonyms (`signpost ⇔ signboard`, `bridge ⇔
  overhead`, `junction ⇔ roundabout`, `estate ⇔ housing`) applied
  once per token. Catches `Police Signpost` ↔ `Police Signboard`
  when they're at the same spot.

Coordinates are still never overwritten by a pending submission — the
admin console remains the only path to move a pin. Incoming names get
added as aliases so future lookups find the merged anchor directly.

## Files

**Modified:**
- `app/session/state.py` — `is_passthrough` on `RecordedAnchor`,
  `next_leg_is_transfer` on `RecordingState`, `destination_location`
  awaiting value.
- `app/flows/recording.py` — passthrough-model handlers, destination
  location capture, `_build_segments`, `_looks_like_destination`,
  `_show_summary_and_confirm`, rewritten `_submit`.
- `app/corridors/submission.py` — `_find_same_place`,
  `_is_token_subsequence`, `_tokens`, `_synonymise`, `_haversine_m`,
  `_LANDMARK_SYNONYMS`. `_upsert_anchors` now consults them.
- `tests/integration/test_recording_flow.py` — updated happy path (no
  spurious `same` at destination), added 4 tests for the two bugs.

**Added:**
- `tests/integration/test_anchor_merge.py` — 14 tests covering
  tokenisation, subsequence rules, proximity guarding, alias reuse,
  synonym merging, city scoping.

## State checks

- 294 tests pass (was 276, +18)
- `ruff` clean, `mypy` clean

## Open / deliberately deferred

- **Per-anchor cost prompts.** Contributor noted "not critical, but
  useful for long distances where a mid-route landmark is significantly
  cheaper to the destination." Deferred until we see whether the
  full-segment fare feels too coarse in real traffic.
- **Admin-facing alias editor.** Fuzzier cases (`signpost/signboard` for
  a landmark whose synonym isn't in the dictionary) will need manual
  admin curation. Ship when the corpus grows past ~30 anchors.
- **Cross-submission anchor merging inside one payload.** The current
  merge runs anchor-by-anchor against the DB. If a single submission
  lists both `Federal Housing` and `Federal Housing Bridge` for the
  same trip, they'll now collapse into one DB row. If the submission
  then has a segment `Federal Housing → Federal Housing Bridge`, that
  becomes a self-loop. In practice contributors don't do this, but the
  validator could reject it explicitly. Deferred.
