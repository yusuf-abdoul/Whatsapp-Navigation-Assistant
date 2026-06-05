# Phase 3b — Live Trip Recording over WhatsApp

**Date:** 2026-06-05
**Status:** Done. About to be committed.

## What we built

A contributor can now capture a route in real time over WhatsApp:

```
> start trip
< Recording a new route. What's the destination?
> Banex Plaza
< What's the name of where you are now?
> AMAC Market
< Share your live location to pin AMAC Market.
> [shares location]
< ✓ Anchor 1: AMAC Market.
  What's the next leg — transport mode and fare? e.g. 'bike 200' or 'taxi 400'
> bike 200
< Got it. Tell me when you reach the next stop.
> now at Police Signpost
< Share your live location to pin Police Signpost.
> [shares location]
< ✓ Anchor 2: Police Signpost. Did you stay on the same vehicle, or change here?
> changed
< OK, vehicle changed. What's the next leg's mode and fare?
> taxi 400
... (repeats) ...
> end
< Recorded:
  1. AMAC Market → Police Signpost (bike, ₦200, ~5 min)
  2. Police Signpost → Berger (taxi, ₦400, ~20 min)
  3. Berger → Banex Plaza (taxi, ₦300, ~10 min, changed)
  Reply 'confirm' to submit for review, or 'cancel' to discard.
> confirm
< Thanks — submission pending review.
```

That submission lands in the same `corridors` table as a form submission,
with `status='pending'` and the contributor's WhatsApp number as
`contributor_id`. Admin review queue is unified.

## Why this came when it did

The web form was solving the wrong problem for our audience. Commuters
who actually take routes aren't going to sit at a laptop and fill in
lat/lon and segment-by-segment fares from memory after the trip — they
get home, they're tired, the data is stale. Live capture during the
journey gives us:

- Cost they just paid (no recall errors)
- GPS coordinates of every named stop, free (live location share)
- Durations from the bot's clock (better than guessing)
- Friction near zero (phone is already in their hand for WhatsApp anyway)

## What's in place

### State machine

`SessionState.recording: RecordingState | None` — when set, the
orchestrator routes every inbound message to the recording handler
instead of normal intent dispatch. Cursor stored in
`RecordingState.awaiting`:

```
destination → origin_name → origin_location → leg_info →
next_anchor_name → next_anchor_location → transfer_decision →
(loop back to leg_info) → confirmation → submit
```

### Intent additions

- `START_ROUTE` — matches `start trip`, `start route`, `record trip`, etc.
- `END_ROUTE` — matches `end`, `done`, `arrived`, `finished`.

Both checked **before** the existing `HELP` regex to avoid the older
`help|menu|start|...` pattern stealing `start`.

### Leg parser

`_parse_leg("bike 200")` → `("bike", 200)`. Accepts mode + integer fare
in either order. Mode list is enforced by regex against
`SEGMENT_MODES` so anything that parses also passes downstream schema
validation.

### Duration deduction

Each `RecordedAnchor` stamps `ts = datetime.utcnow()` at receipt of the
live-location share. Leg duration = `round((t_next - t_prev) / 60)`,
floor-clamped to 1 minute. Contributor never has to type "how long was
that".

### Persist path — identical to the form

`_submit()` builds a `CorridorSubmission` with `AnchorInput` /
`SegmentInput`, calls `create_pending(db, payload, contributor_id)` —
exactly what the web form does. The admin review queue can't tell which
entry point produced which submission.

Instructions are synthesised at render time (`"Take a {mode} to {to_anchor}."`)
so the contributor never types prose.

### Renderer-friendly stored data

The submission's `instruction` field gets a short synthesised fallback
like `"Take a taxi to Berger junction."`. The corridor renderer
(`format_corridor`) regenerates from structure at query time, so this
is mostly an audit trail.

### Cancel + recovery paths

- `cancel` at any point → session deleted, no submission.
- `end` with fewer than 2 anchors + 1 leg → rejected with a helpful
  message, recording discarded.
- Garbage leg text → bot reprompts, doesn't advance.
- Text where the bot expected a location share → bot reprompts.
- `end` when no recording is active → no-op with a hint.

## Files added

- `app/flows/recording.py` — the state machine, leg parser, submission
  builder.
- `tests/integration/test_recording_flow.py` — 18 tests covering the
  happy path, parser edge cases, cancellation, bad inputs, and the
  "end" prematurely / "end" never-started edge cases.

## Files changed

- `app/intent/types.py` — `START_ROUTE`, `END_ROUTE` added.
- `app/intent/detector.py` — new regexes, dispatched before `HELP`.
- `app/session/state.py` — `RecordedAnchor`, `RecordedLeg`,
  `RecordingState`, new `SessionState.recording` field.
- `app/flows/orchestrator.py` — routes through `recording.handle` /
  `recording.start` / `recording.end_recording` at the right points;
  preserves the existing query-mode behaviour when no recording is
  active.

## State checks

- 276 tests pass (was 258 before this slice; +18)
- `ruff` clean
- `mypy` clean

## Open / known limitations

- **Same-vehicle / changed prompt fires after every anchor**, not just
  when potentially same-mode. Worth tightening: only ask when the
  next leg's mode might match the previous. For now we ask universally
  — clearer prompt at the cost of one extra question per stop.
- **No anchor-against-existing-DB matching during recording.** When
  the contributor says "now at Berger" we trust the name as-is; the
  upsert at submission time handles dedup by `(name, city)`. But we
  could autocomplete from known anchors in the bot itself for typo
  resistance. Defer.
- **One destination per recording.** No multi-stop / detour support.
  Matches the corridor schema (linear) so this is correct.
- **The form lives on.** Both entry points stay alive; admin doesn't
  see a difference.

## What this unlocks for launch

- Contributor onboarding becomes "send 'start trip' to our WhatsApp" —
  zero install, zero docs.
- Coverage growth shifts from "PM writes YAMLs" to "commuters tap a few
  messages while riding."
- Soft-launch story for outreach: "want to map your daily commute and
  help others? Reply START to this number."
