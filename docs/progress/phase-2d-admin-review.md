# Phase 2d — Admin Review Console

**Date:** 2026-05-07
**Status:** Done. About to be committed.

## What we built

A gated `/admin` surface for reviewing pending corridors. Admins can:

- See every pending corridor newest-first
- Drill into a corridor's anchors, ordered steps, and submitter
- Approve (status → approved, `approved_at` stamped) or reject
- Correct an anchor's lat/lon — the sole path for changing established
  coordinates, since the contribution flow intentionally won't overwrite them

## Why this came when it did

Phase 2c made anchor coordinates immutable from the contribution flow. That
rule only makes sense if there's a path to correct mistakes when they happen.
Phase 2d provides that path, and the broader editorial layer for everything
contributors submit.

## What's in place

### Auth gate ([app/auth/session.py](../../app/auth/session.py))

- `current_admin(request)` returns the logged-in user iff `user.is_admin`,
  else `None`. Every admin route checks this and 303s to `/login` on miss.
- The user's `is_admin` flag is mirrored into the session cookie on login so
  the nav can show/hide the Admin link without a DB lookup per request.
  Stale if the flag toggles mid-session — the user signs in again to pick it
  up.

### Admin operations ([app/corridors/admin.py](../../app/corridors/admin.py))

Pure data-layer functions, no auth concerns of their own:

- `list_pending(db)` — pending corridors newest-first, with destination and
  segments eagerly loaded.
- `get_detail(db, id)` — single corridor with destination + segments +
  segment endpoints joined.
- `get_submitter(db, corridor)` — looks up the contributor by id; tolerates
  the legacy "seed" string we used on the seed-loaded corridors.
- `approve(db, id)` — sets status="approved" and stamps `approved_at`.
  Idempotent on already-approved rows.
- `reject(db, id)` — sets status="rejected". Idempotent.
- `update_anchor_coords(db, anchor_id, lat, lon)` — admin-only path to fix
  a pin. The contribution flow's lock makes this the only writer of lat/lon
  after first-create.

### Routes ([app/web/routes.py](../../app/web/routes.py))

- `GET /admin` — queue
- `GET /admin/corridors/{id}` — detail
- `POST /admin/corridors/{id}/approve` and `/reject` — decide a corridor;
  303 back to the queue
- `POST /admin/anchors/{id}` — `lat` + `lon` form fields, range-checked
  (-90..90, -180..180); 303 to `return_to` (the detail view by default)

All admin routes redirect anonymous and non-admin users to `/login` rather
than 403'ing. Same UX as everything else in the portal.

### Templates

- `admin/queue.html` — table of pending corridors with destination, step
  count, submission time, and a link to the detail view.
- `admin/detail.html` — header (destination, step count, submitter, status
  badge), Approve / Reject buttons, the ordered step list, and one form per
  anchor for editing its coordinates.
- `base.html` — nav shows an "Admin" link when the session carries
  `is_admin=true`.

## Promotion (how to make someone an admin)

No admin tool yet for managing admins. For dev, in a `psql` shell:

```sql
UPDATE users SET is_admin = true WHERE wa_number = '+234...';
```

The user signs in again to refresh the session. The Admin link then appears
in the nav.

## Files added

- `app/corridors/admin.py`
- `app/web/templates/admin/queue.html`
- `app/web/templates/admin/detail.html`
- `tests/integration/test_admin_flow.py` — 9 tests (gate, queue, detail,
  approve, reject, anchor edit, range check, 404, idempotency)
- `docs/progress/phase-2d-admin-review.md` (this file)

## Files changed

- `app/auth/session.py` — `current_admin` helper; `login()` mirrors
  `is_admin` into the session
- `app/web/routes.py` — admin routes + imports
- `app/web/templates/base.html` — Admin link in the nav when applicable

## State checks

- 197 tests pass (was 188 before this slice; +9)
- `ruff` clean
- `mypy` clean

## What's NOT in here (deferred follow-ups)

- **Merging two pending corridors into one** — when two contributors submit
  the same destination, both land as pending. Admin can approve both today;
  a "merge corridor A into corridor B" action would deduplicate. Bigger
  scope; defer until the corpus shows the pain.
- **Editing segment instructions / modes / costs in-place** — read-only on
  the detail page right now. Easy to add once we see real reviewer needs.
- **Renaming anchors** — same.
- **Activity log / audit trail** — who approved what, when, and why. Worth
  adding before payouts go live.
- **Bulk approve / reject** — single-row actions only.

## Open questions

- **Approval auto-credits the contributor (eventually).** We've recorded
  `contributor_id` on the corridor but haven't built the credits / payouts
  ledger. Phase 3 territory.
- **Sandbox-only auth means there's a chicken-and-egg with admin promotion**:
  the admin's WhatsApp number has to be joined to the Twilio sandbox to
  receive the login OTP. Document this in the runbook before we hand it off.
