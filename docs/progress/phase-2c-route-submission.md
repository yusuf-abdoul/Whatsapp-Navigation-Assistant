# Phase 2c — Route Submission Form

**Date:** 2026-05-07
**Status:** Done. About to be committed.

## What we built

Authenticated contributors can now submit a route through the web. The form
captures the city, destination, anchors (named places along the way) with
lat/lon and aliases, and ordered steps with mode, instruction, and optional
cost / duration. Valid submissions land as a pending corridor that an admin
will review.

## Why this came when it did

Phase 2a put the landing page up. Phase 2b gated the contributor portal
behind a verified WhatsApp number. Phase 2c is the actual contribution
surface — the thing the rest of the portal exists for.

## What's in place

### Submission schema ([app/corridors/submission.py](../../app/corridors/submission.py))

Three Pydantic models with strict field-level rules:

- ``AnchorInput`` — name (1-120 chars), lat (-90..90), lon (-180..180), aliases
  (accepts a comma-separated string or a list; trimmed, lowercased).
- ``SegmentInput`` — from/to anchor names, mode (from ``SEGMENT_MODES``),
  instruction (1-500 chars), transfer flag, optional cost / duration.
- ``CorridorSubmission`` — city, destination, applicability notes, anchors,
  segments. Plus a ``cross_validate()`` method that catches the multi-field
  rules Pydantic can't see on its own:
  - Destination must be in the anchors list.
  - Every segment's from/to must be in the anchors list.
  - A segment's from/to must differ.
  - Anchor names must be unique within one submission.

### Persistence ([app/corridors/submission.py](../../app/corridors/submission.py))

``create_pending(db, payload, contributor_id)`` runs ``cross_validate``,
upserts anchors, inserts the corridor with ``status='pending'`` and the
contributor's id, then inserts segments in submission order (sequence 1..N).
It refreshes the corridor so callers can iterate ``segments`` without
tripping SQLAlchemy's async lazy-load guard.

### Anchor upsert rule (changed in this slice)

When a submission references an anchor that already exists by ``(name, city)``:
- **Coordinates are NOT overwritten.** The first contributor sets them; only
  the admin review tool (Phase 2d) may correct them. Without this, a single
  bad pending submission could silently move a pin used by every corridor.
- **Aliases ARE merged** — additive, low-risk, and the more local names we
  collect for the same place, the better the lookup works.

### Web routes ([app/web/routes.py](../../app/web/routes.py))

- ``GET /submit`` — when signed in, shows the form (with the user's submitted
  values restored on validation error); when signed out, shows the sign-up CTA
  instead of the form.
- ``POST /submit`` — auth-gated (redirects to /login when anonymous); parses
  the parallel-array form fields, validates via Pydantic + cross-validate,
  inserts the corridor, renders a success page with the new id.
- ``GET /submit/anchor-row`` and ``GET /submit/segment-row`` — return blank
  row partials for the HTMX "+ Add anchor" / "+ Add step" buttons.

### Templates

- ``submit.html`` rewritten as a three-section guided form: basics, anchors,
  steps. Each row is a card with stacked labelled fields (replaced the dense
  12-column grid for readability).
- ``_anchor_row.html`` — one anchor card (name, lat, lon, aliases).
- ``_segment_row.html`` — one step card (from, to, mode, instruction,
  vehicle-change flag, optional cost / duration).
- ``_submit_success.html`` — "thanks, pending review" page with the corridor's id.

The "+ Add" buttons use HTMX (``hx-get`` + ``hx-swap="beforeend"``). The
"remove" button on each row is plain JS (one-liner) — no round-trip needed for
client-only DOM removal.

## Files added

- ``app/corridors/submission.py``
- ``app/web/templates/_anchor_row.html``
- ``app/web/templates/_segment_row.html``
- ``app/web/templates/_submit_success.html``
- ``tests/integration/test_corridors_submission.py`` — 12 tests (schema
  validation, cross-validation, persistence, coord-preservation, segment order)
- ``tests/integration/test_submit_flow.py`` — 9 tests (auth gate, HTMX row
  partials, happy and sad submission paths)

## Files changed

- ``app/web/routes.py`` — submit GET/POST/row handlers + form helpers
- ``app/web/templates/submit.html`` — full rewrite for the form
- ``app/corridors/submission.py`` — coord-preservation rule on existing anchors

## State checks

- 188 tests pass (was 167 before this slice; +21 new)
- ``ruff`` clean
- ``mypy`` clean

## What's left in Phase 2

- **2d — Admin review.** Gated ``/admin`` for ``is_admin`` users. List pending
  corridors, view their anchors + segments + submitter, approve / reject, edit
  anchor coordinates when they're wrong. This is the only path to mutate
  established anchor lat/lon — the rule we just enforced at the contribution
  layer.

## Known limitations to address in Phase 2d

- **No editorial merging yet.** Two submissions for the same destination land
  as two separate pending corridors. Admins can approve both (runtime picks
  nearest-anchor at query time) or reject duplicates by hand.
- **Anchors are global per (name, city).** If two contributors name the same
  physical place differently ("Federal Housing" vs "Fed Housing Bridge"),
  they become two anchor rows. The admin tool needs an "alias-merge" action.
- **Contributor portal can't see prior submissions.** A contributor doesn't
  know what anchors already exist before they type a name — so naming drift
  is possible. Listing existing anchors in the form (as suggestions) is a
  follow-up improvement.
