# Phase 2g — Test Database Isolation

**Date:** 2026-05-18
**Status:** Done. About to be committed.

## What we built

Tests now run against a dedicated `wna_test` database, not the dev database
`wna`. The autouse truncate fixture only touches `wna_test`, so a `uv run
pytest` no longer wipes live submissions, approved corridors, or any data
you've added through the WhatsApp or web flows.

## Why this came when it did

A bug — and a real one. Mid-session I ran the test suite and the autouse
truncate destroyed a contributor's submitted-and-approved Jabi Park route.
Recovery wasn't possible; the truncate was committed. The fix had to be
structural: tests must not share the dev DB.

## What's in place

### [tests/conftest.py](../../tests/conftest.py)

- Sets `DATABASE_URL` env var to point at `wna_test` **before any `app.*`
  import**. `app.config.get_settings()` is `lru_cache`d, so the value at
  first read is what every subsequent call sees — by setting the env var
  first, the entire test session is routed to the test DB.
- Session-scoped autouse fixture `_bootstrap_test_db` does two things:
  - Connects to the `postgres` maintenance DB (sync via `psycopg`) and
    `CREATE DATABASE wna_test` if it doesn't already exist.
  - Runs `alembic upgrade head` against the test DB so schema is current.
- Idempotent: existing test DB is left in place between runs.

### Existing fixtures unchanged

`tests/integration/conftest.py` reads `get_settings().database_url` to
truncate — that URL is now the test one, so the truncate hits `wna_test`,
never `wna`. No code change needed there.

### docs/contributing.md

- Replaced the "tests truncate the dev DB, remember to re-seed" warning
  with a "tests run against `wna_test`; your dev data is safe" note.
- Added a one-liner for resetting the test DB by hand if needed.

## Verified

- `uv run pytest -q` — 248 passed.
- Post-test check:
  - `wna_test`: 0 anchors, 0 corridors (truncated as designed).
  - `wna`: 12 anchors, 3 corridors (untouched).

## Files added

- `tests/conftest.py`
- `docs/progress/phase-2g-test-db-isolation.md` (this file)

## Files changed

- `docs/contributing.md` — section rewritten

## What this doesn't fix

- **Already-lost submissions stay lost.** This guards future runs only.
- **Redis isolation** — tests use `fakeredis`, so live Redis data
  (session state for active conversations) is already safe. Not affected
  by this slice.

## Authoring rule going forward

- New integration tests that touch the DB don't need any special setup —
  the existing `db` fixture already does the right thing via the routed
  URL. Just inherit it.
- If you find yourself wanting to "truncate before testing," the autouse
  fixture already does that — for `wna_test`, never the dev DB.
