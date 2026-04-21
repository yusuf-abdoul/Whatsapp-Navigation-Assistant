# Contributing

## Local Setup

```bash
uv sync
cp .env.example .env   # fill in secrets from 1Password
uv run uvicorn app.main:app --reload
```

Run Redis locally via Docker if you don't have it:
```bash
docker run -d -p 6379:6379 redis:7-alpine
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
