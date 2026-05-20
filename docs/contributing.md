# Contributing

## Local Setup

```bash
uv sync
cp .env.example .env                  # fill in secrets from 1Password
docker compose up -d                  # Postgres + Redis
uv run alembic upgrade head           # apply migrations
uv run python -m app.corridors.seed   # load seed corridors
uv run uvicorn app.main:app --reload
```

If you only want one service: `docker compose up -d postgres` or `docker compose up -d redis`.

## Seed corridors

Seed YAMLs live under [data/corridors/<city>/](../data/corridors/). Each file is one corridor — its destination, ordered segments, and the anchors it touches. The loader upserts anchors by `(name, city)` and inserts a corridor keyed on `(destination, contributor)` — so re-running the loader is **idempotent**: anchors merge their aliases, and corridors with a matching key are skipped.

To add a new seed corridor: copy an existing `.yaml`, edit anchors / segments / destination, and re-run the loader. Every named stop on the route should be its own anchor (with lat/lon) — don't bury intermediate landmarks inside instruction text, or the lookup can't reason over them.

## Test database

Tests run against a **separate database** — `wna_test` — not the dev database `wna`. [tests/conftest.py](../tests/conftest.py) sets `DATABASE_URL` to point at `wna_test` before any app code is imported, then auto-creates the database (if it doesn't exist) and applies migrations.

This means `uv run pytest` **never touches your live / dev data**. The autouse truncate fixture in [tests/integration/conftest.py](../tests/integration/conftest.py) only wipes `wna_test`. Your seeded corridors, your submitted routes, and your admin-approved data in `wna` are preserved across test runs.

Quick checks:

```bash
uv run python -m app.corridors.seed --status      # what's in the dev DB right now
```

If you want to reset the test DB by hand (rare):

```bash
docker exec wna-postgres-1 psql -U wna -d postgres -c "DROP DATABASE wna_test;"
# Next pytest run will recreate it.
```

## Ownership Map

| You | You own |
|---|---|
| BE-1 | `app/channel/`, `app/intent/`, `app/flows/`, `app/formatting/` |
| BE-2 | `app/resolver/`, `app/routing/`, `app/session/`, `app/abuse/` |
| Shared | `app/analytics/`, `app/config.py`, `app/errors.py`, `tests/` |
| Ops | `Dockerfile`, `render.yaml`, `.github/`, deploys, secrets |
| PM | `data/aliases/`, `data/qa_routes/`, GitHub issues |

Cross-module PRs need approval from both owners.

## Branching

- `main` is protected. Never push directly.
- Feature branches: `feat/<short-name>`, `fix/<short-name>`, `chore/<short-name>`.
- Open a PR when ready for review.

## Commits

Conventional Commits:

- `feat: add nearby search flow`
- `fix: handle empty geocode response`
- `chore: bump locationiq client version`
- `docs: clarify session TTL rationale`

## PR Rules

- CI must pass (ruff, mypy, pytest).
- At least one reviewer approval.
- Squash-merge to `main`.
- Link the GitHub issue or PRD section in the description.

## Testing Webhooks Locally

Twilio needs a public URL to POST to. Use ngrok:

```bash
# terminal 1
uv run uvicorn app.main:app --reload

# terminal 2
ngrok http 8000
```

Copy the ngrok HTTPS URL (e.g. `https://abc123.ngrok.app`) and set it as the WhatsApp sandbox webhook in the Twilio console: `<ngrok-url>/webhook/twilio`.

Join the sandbox from your phone (code in Twilio console), then message the sandbox number. You should see `{"status": "accepted"}` logs in terminal 1 and receive an echo reply.

## Tests

- Unit tests go in `tests/unit/` — no network, no Redis, no external calls.
- Integration tests go in `tests/integration/` — may use a local Redis and mocked LocationIQ.

## Decisions

Any choice that affects more than one module gets a short ADR in `docs/decisions/`. Copy the template from `0001-record-architecture-decisions.md`.

## Style

- Ruff handles lint + format. Don't bikeshed style in PRs.
- Follow existing module patterns before introducing new ones.
- No comments that restate the code. Comments explain *why*, never *what*.
