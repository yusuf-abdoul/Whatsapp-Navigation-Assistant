# Phase 1c — Seed Corridors

**Date:** 2026-04-27
**Status:** Done. Loaded into local Postgres. About to be committed.

## What we built

Three real Abuja corridors, hand-authored as YAML, plus a small CLI loader that turns those files into rows in the database. This is the first time the schema has actual data in it — until now everything was scaffolding.

## Why this came when it did

Phase 1a built the tables. Phase 1b built the queries. Without data, neither can be tested end-to-end. Seeding before orchestrator integration (Phase 1d) means we can validate the lookup with real names ("banex", "jabi mall") before wiring it into the conversation flow.

## What's in place

### Three seed corridors

All under [data/corridors/abuja/](../../data/corridors/abuja/):

1. **Lugbe → Banex Plaza** (4 segments) — taxi from Police Signpost via Car Wash → Federal Housing Bridge → Berger, then keke to Banex.
2. **Lugbe (Newsite) → Area 1 Shopping Complex** (4 segments) — bike from estate gate to Police Signpost, walk over the pedestrian bridge, car to Area 1 Bridge, walk down. This is the example from our PRD discussion.
3. **Wuse 2 → Jabi Lake Mall** (2 segments) — keke to Aminu Kano Crescent, taxi to the mall.

Total: 12 distinct anchors, 3 corridors, 10 segments.

### Seed YAML format

```yaml
city: abuja
status: approved              # pending | approved | rejected
contributor: seed             # free-text for now (real contributor IDs come with the portal)
applicability_notes: All day. Fewer kekes after 8pm.
applicability_windows: []     # JSON list — empty means always

anchors:
  - name: Wuse 2 Market
    lat: 9.077
    lon: 7.483
    aliases: [wuse 2, wuse market, wuse two]
  # … more anchors

destination: Jabi Lake Mall   # must match one of the anchor names above

segments:
  - sequence: 1
    from: Wuse 2 Market       # anchor name
    to: Aminu Kano Crescent Junction
    mode: keke                # taxi | keke | bike | walk | bus | car | mixed
    instruction: Take a keke heading Aminu Kano Crescent.
    cost_ngn: 200             # optional
    duration_min: 5           # optional
  # … more segments
```

### Loader ([app/corridors/seed.py](../../app/corridors/seed.py))

Two functions, plus a small CLI:

- `load_file(db, path)` — parse one YAML, upsert anchors by `(name, city)`, insert one corridor and its segments. Returns counts.
- `load_directory(db, root)` — walks `*.yaml` recursively under `root`, calls `load_file` for each.
- `python -m app.corridors.seed [--city abuja]` — CLI wrapper that opens a session, runs the loader, commits.

**Upsert semantics:** running a seed twice doesn't duplicate anchors — they're merged by `(name, city)` and aliases are unioned. Corridors and segments, by contrast, are inserted fresh each run. Truncate before re-seeding if you want a clean slate.

### Validation

- `destination` must be one of the declared anchor names (otherwise `SeedError`).
- Every segment's `from` and `to` must be declared anchors (otherwise `SeedError`).
- `city` and `destination` are required at the top level.
- The DB-level rules from Phase 1a (mode in enum, status in enum, no self-loops, sequence unique per corridor) still apply — they catch shape errors the loader doesn't pre-check.

## Files added

- `data/corridors/abuja/lugbe-to-banex.yaml`
- `data/corridors/abuja/lugbe-to-area1.yaml`
- `data/corridors/abuja/wuse2-to-jabi-lake-mall.yaml`
- `app/corridors/seed.py`
- `tests/integration/test_corridors_seed.py` — 7 tests: file load, directory load, anchor upsert, bad-destination/bad-segment-anchor/missing-key error paths, smoke-load of the real Abuja seed files.

## Files changed

- `docs/contributing.md` — local setup now includes `docker compose up -d`, `alembic upgrade head`, and the seed CLI; added a short section on adding a corridor.

## How to use

```bash
# Fresh start
docker compose up -d postgres
uv run alembic upgrade head
uv run python -m app.corridors.seed             # all cities under data/corridors/
uv run python -m app.corridors.seed --city abuja  # one city

# Sanity check
PGPASSWORD=wna_dev docker exec wna-postgres-1 \
  psql -U wna -d wna -c "\
    SELECT a.name AS dest, COUNT(s.id) AS segs \
    FROM corridors c \
    JOIN anchors a ON a.id = c.destination_anchor_id \
    JOIN segments s ON s.corridor_id = c.id \
    GROUP BY a.name ORDER BY a.name;"
```

## State checks

- 111 tests pass (78 unit + 11 schema + 15 repository + 7 seed)
- ruff + mypy clean
- All three real seed files load cleanly end-to-end
- `find_corridors_by_destination("banex")` returns the Lugbe→Banex corridor as expected

## What's left in Phase 1

- **1d — Orchestrator integration.** When a user asks for directions:
  1. Call `find_corridors_by_destination(message)` first.
  2. If a match exists, geocode (or take live-location for) the user's origin, find the nearest anchor on the corridor, clip the segment list, render as numbered steps.
  3. If no match, fall back to the existing LocationIQ-only path.
- **`format_corridor`** in `app/formatting/responses.py` to render corridor hits — numbered bullets with mode + instruction + optional cost/duration, plus distance/ETA from LocationIQ at the bottom.

## Known minor warts

- The seed loader's printed summary (`"X anchor refs"`) counts declarations, not unique rows. The actual row count is usually lower because of upserts. Cosmetic; reading the log lines or querying the DB gives the truthful number.
- No integrity check yet that segments form a connected path (e.g. segment N's `to_anchor` should generally be segment N+1's `from_anchor`). The schema doesn't require it, and corridors with branches/forks aren't yet supported but might need to be.
