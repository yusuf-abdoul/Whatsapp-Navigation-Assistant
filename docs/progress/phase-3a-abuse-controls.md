# Phase 3a — Abuse Controls

**Date:** 2026-05-30
**Status:** Done. About to be committed.

## What we built

Per-WhatsApp-number rate limits + identical-query cooldown, wired into the
orchestrator's entrypoint so they fire before any database or LocationIQ
work. Backed by Redis (the same instance we use for sessions). This is the
last blocker between us and a public-internet deploy — without it, a
malicious user could spam the bot and burn through our LocationIQ quota.

## Why this came when it did

User asked for a launch readiness review. The config already exposed
`rate_limit_per_hour`, `rate_limit_per_day`, and
`identical_query_cooldown_seconds`, but the `app/abuse/limits.py` module
was a `NotImplementedError` stub. Shipping this clears the way to point
the bot at a Render deploy without exposing it to obvious abuse vectors.

## What's in place

### Two layered checks ([app/abuse/limits.py](../../app/abuse/limits.py))

- **`check_and_record(wa_number) -> bool`** — increments the per-hour and
  per-day counters, returns `False` if either limit is exceeded.
  - Hourly: tumbling 1-hour window, default 30 requests
  - Daily: tumbling 24-hour window, default 100 requests
  - One `INCR` + conditional `EXPIRE` per window. Two Redis ops per call.
- **`is_duplicate(wa_number, text) -> bool`** — `SET NX EX` on a key derived
  from `(number, sha256(text)[:16])`. First send wins; repeats inside the
  5-second window are flagged duplicates and silently dropped.
  - Live-location shares aren't deduped (each share is a fresh GPS reading).

### Fail-open policy

Any Redis error logs a warning and returns "allowed." A transient Redis
hiccup shouldn't reject real users. Sustained abuse will still hit the
limit on the next successful op.

### Wiring ([app/flows/orchestrator.py](../../app/flows/orchestrator.py))

In `handle()`, before any session load:

1. `check_and_record(message.user_id)` — over limit → reply with
   `format_error(ErrorKind.RATE_LIMITED)` and exit.
2. `is_duplicate(message.user_id, message.text)` — duplicate within
   cooldown → log + silently drop.

Order matters: rate limit first (cheaper), then dedup.

### Tests

- `tests/unit/test_abuse_limits.py` — 10 tests covering both checks, the
  fail-open behaviour, per-number isolation, and the cooldown-disabled
  edge case. Uses fakeredis.
- `tests/unit/test_orchestrator.py` — autouse fixture now also patches
  `abuse_limits.check_and_record` / `is_duplicate` to default-allow, so
  the existing orchestrator tests don't need to know about Redis state.
- `tests/integration/conftest.py` — same autouse patch for integration
  tests. Without it, the cooldown between consecutive integration tests
  caused the second test's first message to be silently dropped as a
  "duplicate."

## Files added

- `tests/unit/test_abuse_limits.py`

## Files changed

- `app/abuse/limits.py` (was a stub, now ~120 lines)
- `app/flows/orchestrator.py` — two-line guard at the top of `handle()`
- `tests/unit/test_orchestrator.py` — autouse fixture extended
- `tests/integration/conftest.py` — autouse fixture added

## State checks

- 258 tests pass (was 248 before this slice; +10)
- `ruff` clean
- `mypy` clean

## Operational note

These limits are tuned conservatively. In a launch incident we'd want to:

- Lower `rate_limit_per_hour` (env var, no redeploy needed) if a number
  starts hammering us.
- Add a hard `is_blocked(wa_number)` check (the stub is still in
  `app/abuse/limits.py` — implement with a Redis set when needed).

## What's left before live deploy

1. **Render web service + Neon Postgres + Upstash Redis** — provision
   and connect. `render.yaml` exists, just needs touch-up for Neon /
   Upstash URLs.
2. **GitHub Actions CI** — ruff / mypy / pytest on PR, optional
   auto-deploy on `main`.
3. **Meta Direct verification** kickoff (long lead time, can run in
   parallel with Render bring-up).
