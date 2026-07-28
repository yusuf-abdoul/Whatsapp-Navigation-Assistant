# WNA — WhatsApp Navigation Assistant

Chat-based commuter directions for Nigerian cities, delivered through WhatsApp.

> "Google Maps, over simple chat."

Google Maps assumes a private car, a data plan, and a landmark database that
knows your neighbourhood. In Abuja most trips are a chain of shared taxis and
`keke`s between named landmarks — "Police Signpost", "Berger", "Federal
Housing Bridge" — with fares that change block-by-block. WNA answers "how do
I get to X" the way another commuter would: which vehicle, from where, how
much, where to switch.

**Status:** early beta. Launch city: Abuja. Twilio WhatsApp sandbox for dev,
Meta WhatsApp Business Cloud API for production.

## How it works

- **Ask a question over WhatsApp.** The bot resolves your origin (from a
  shared location or a named landmark), matches your destination against
  known corridors, and replies with the ordered legs — mode, fare, and where
  to change vehicles.
- **Contribute a route by walking it.** Say `start trip`, name the
  destination, share your live location at each landmark, tell the bot when
  you change vehicles. It records the whole trip as a corridor pending admin
  review.
- **Contribute via the web form.** Sign in with your WhatsApp number, fill in
  anchors and segments, submit.
- **Admin review.** Approved corridors become part of the answer set for
  every future commuter asking about the same destination.

## Tech stack

- **Backend:** Python 3.12 + FastAPI, SQLAlchemy 2 (async), Pydantic v2.
- **Storage:** Postgres (corridors + anchors), Redis (session + rate limits).
- **Channels:** Meta WhatsApp Business Cloud API (production), Twilio
  WhatsApp sandbox (development).
- **Geocoding:** LocationIQ.
- **Deployment:** Fly.io.
- **Frontend:** Server-rendered Jinja templates + HTMX (no SPA build step).

## Quick start

```bash
# Prerequisites: Python 3.12, uv (https://github.com/astral-sh/uv), Docker.
git clone https://github.com/<your-fork>/WNA.git
cd WNA
uv sync
cp .env.example .env                    # fill in secrets (see below)
docker compose up -d postgres redis
uv run alembic upgrade head
uv run python -m app.corridors.seed     # load a few example corridors
uv run uvicorn app.main:app --reload
```

Open http://localhost:8000 for the web form and admin dashboard.
Health check: http://localhost:8000/health

**Environment variables you'll need for a minimal dev loop:**
- `SESSION_SECRET` — 32+ random bytes.
- `LOCATIONIQ_KEY` — free tier is enough for local testing.
- `DATABASE_URL`, `REDIS_URL` — the docker-compose defaults work.
- WhatsApp channel keys (Meta or Twilio) — only needed to test end-to-end
  over WhatsApp. See [docs/architecture.md](docs/architecture.md) for the
  channel adapter split.

The [`.env.example`](.env.example) file lists every variable.

## Repository layout

```
app/
  channel/        WhatsApp adapters (Meta + Twilio)
  intent/         Route the incoming message to a flow
  flows/          Recording, query, admin flows
  corridors/      Anchors, corridors, segments — model + repository + seed
  resolver/       Origin + destination resolution (landmark, address, GPS)
  routing/        Corridor lookup + response building
  session/        Redis-backed conversation state
  abuse/          Rate limits + query cooldowns
  web/            Server-rendered pages (submission form, admin console)
  auth/           WhatsApp-OTP web sign-in
data/
  corridors/      YAML seed corridors, per city
docs/             Architecture, decisions, runbooks, progress log
tests/
  unit/           Pure logic, no I/O
  integration/    DB + Redis + HTTP end-to-end
```

## Contributing

Please read [CONTRIBUTING.md](CONTRIBUTING.md) for how to file bugs, propose
features, and open pull requests. First-time contributors: issues tagged
[`good first issue`](https://github.com/<your-fork>/WNA/labels/good%20first%20issue)
are a good place to start.

We ship changes in small, reviewable slices. Every PR must pass `ruff`,
`mypy`, and `pytest`.

The community follows the [Contributor Covenant Code of
Conduct](CODE_OF_CONDUCT.md).

## Deeper reading

- [Architecture](docs/architecture.md) — module map + request lifecycle.
- [Contributing (internal ops)](docs/contributing.md) — team-facing setup,
  ownership map, deploy notes.
- [Decision records](docs/decisions/) — why we chose each stack piece.
- [Progress log](docs/progress/) — running notes on each shipped slice.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
