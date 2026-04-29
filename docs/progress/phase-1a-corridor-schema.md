# Phase 1a — Corridor Schema

**Date:** 2026-04-27
**Status:** Done. Migration applied to local Postgres. Not yet committed.

## What we built

Three database tables that will hold the routes the bot serves from. They model the corridor concept the team agreed on:

- **`anchors`** — named places along a route. Bus stops, junctions, landmarks. Each anchor has a name (e.g. "Police Signpost"), aliases for local variants, lat/lon for distance math, and a city.
- **`corridors`** — a directed route ending at one anchor. A corridor can be reused for many starting points, because users joining mid-corridor get a clipped version of the same instructions.
- **`segments`** — the ordered steps inside a corridor. Each step says: take *this transport mode* from *this anchor* to *this anchor*, with a human-readable instruction and optional cost / duration / time-window.

## Why this came first

The product's value is the curated human commute instructions — not a map. Those instructions live in this schema. We needed the data shape locked before writing any code that reads or writes it; otherwise we'd churn in two layers at once. Schema-first as its own slice means we can poke holes in the model now, while it's cheap.

## Rules the database enforces

These are guaranteed by Postgres, so no app bug can write bad data:

- An anchor's name is unique within a city. ("Berger" can exist in Abuja and Lagos.)
- A corridor's `status` must be `pending`, `approved`, or `rejected`.
- A segment's `mode` must be one of: `taxi`, `keke`, `bike`, `walk`, `bus`, `car`, `mixed`.
- A segment can't loop back on itself (`from_anchor` ≠ `to_anchor`).
- Step numbers (`sequence`) are unique within a corridor — no two "step 1"s.
- Deleting a corridor deletes its segments automatically.
- An anchor can't be deleted while corridors still reference it.

## Files added

- `docker-compose.yml` — runs Postgres + Redis locally
- `app/corridors/models.py` — the three table definitions
- `app/corridors/db.py` — shared async DB engine and session factory
- `alembic/` + first migration `alembic/versions/03d1eab73d5e_anchors_corridors_segments.py`
- `tests/integration/test_corridors_schema.py` — 11 tests covering every rule above
- `tests/integration/conftest.py` — DB fixture that rolls back per test, so tests stay isolated

## Files changed

- `pyproject.toml` — added `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `psycopg[binary]`, `pytest-postgresql`
- `app/config.py` — added `database_url` setting (default: local docker compose)
- `.env.example` — added `DATABASE_URL`
- `alembic.ini` — URL is now read from `app.config` at runtime

## How to run it locally

```bash
docker compose up -d postgres
uv sync
uv run alembic upgrade head
uv run pytest tests/integration/test_corridors_schema.py
```

## State checks

- 89 tests pass total (78 existing unit + 11 new schema integration)
- `ruff check` clean
- `mypy` clean
- Local Postgres has the three tables created

## What's left in Phase 1

- **1b — Repository layer.** Functions to read from the schema: `find_corridors_by_destination(name)`, `nearest_anchor_to(lat, lon)`. Pure data access, no app logic.
- **1c — Seed corridors.** Hand-author 2–3 Abuja corridors (e.g. Lugbe → Banex) as a YAML or JSON seed and a loader script. This is what we test the model with end-to-end.
- **1d — Orchestrator integration.** When a user asks for directions, look up the corridor first; only fall back to LocationIQ if there's no match.
- New reply renderer that formats a corridor hit as numbered steps with mode + landmark + optional cost.

## Open questions for next phase

- Where do contributor user IDs live? Right now `corridors.contributor_id` is a free-text string — fine until the contributor portal lands, but worth flagging.
- Geographic search for nearest anchor: brute-force (read all city anchors, compute haversine) is fine while the table is small (< a few thousand rows). PostGIS is the upgrade path when we outgrow that.
