# Foundation — Webhook + Echo

**Date:** 2026-04-21 → 2026-04-22
**Status:** Done. Committed as `5cd6804` (scaffold) + `b4bd71b` (webhook + echo).

## What we built

The bare-minimum WhatsApp loop. A user sends a WhatsApp message, our server receives it through Twilio, verifies it's actually from Twilio (not spoofed), parses out the sender / text / location, and replies back. No business logic yet — just the plumbing.

## Why this came first

We wanted to prove the channel works end-to-end before writing any product code. If signature verification or webhook parsing was wrong, every later feature would be blocked. Getting an echo reply from a real phone validates: Twilio config, ngrok tunnel, FastAPI route, signature math, and Twilio's reply API — all in one go.

## What's in place

- **FastAPI app** (`app/main.py`) with `/health` and `/webhook/twilio` endpoints. The webhook responds within milliseconds and queues the actual reply work to a background task — Twilio retries if we're slow.
- **Channel adapter pattern** (`app/channel/base.py`) — an abstract interface with two implementations:
  - `TwilioAdapter` (`app/channel/twilio.py`) — fully working: HMAC-SHA1 signature verification, form-body parsing, REST-API send.
  - `MetaAdapter` (`app/channel/meta.py`) — stub, raises `NotImplementedError`. Will be filled in once Meta business verification clears.
- **Provider switching** via the `CHANNEL_PROVIDER` env var, factory at `app/channel/__init__.py`. Same calling code regardless of provider.
- **Config layer** (`app/config.py`) using pydantic-settings. All secrets read from `.env`.
- **Analytics events stub** (`app/analytics/events.py`) — `Event` enum from PRD §9.2, `emit()` writes JSON via structlog.
- **Error taxonomy** (`app/errors.py`) — `ErrorKind` enum + `WNAError` exception. Every user-facing failure maps to a specific kind.
- **Echo orchestrator** (`app/flows/orchestrator.py` at the time) — text → "Echo: …", location → "Got your location: …", anything else → help nudge.
- **Tooling** — uv, ruff, mypy strict, pytest with respx + pytest-asyncio.
- **Dockerfile** with `--proxy-headers` so Twilio signature verification works behind a reverse proxy.

## Files added

- `app/main.py`, `app/config.py`, `app/errors.py`
- `app/channel/{__init__,base,twilio,meta}.py`
- `app/analytics/events.py`
- `app/flows/orchestrator.py` (initial echo version)
- `app/intent/types.py` (Intent enum, no detector logic yet)
- `app/session/state.py` (Place + SessionState pydantic models)
- `app/resolver/aliases.py` + `data/aliases/abuja.yaml` (25 seed aliases)
- `pyproject.toml`, `Dockerfile`, `.dockerignore`, `.env.example`, `render.yaml`
- `tests/unit/test_health.py`, `tests/unit/test_aliases.py`, `tests/unit/test_twilio_adapter.py`
- `docs/architecture.md`, `docs/contributing.md`, `docs/decisions/` (3 ADRs: ADR template, Meta-direct-with-Twilio-fallback, LocationIQ choice)

## Validated end-to-end

Real WhatsApp → ngrok → FastAPI → signature verify → background task → Twilio REST → reply back to phone. One config gotcha caught: `TWILIO_WHATSAPP_FROM` needs the literal `whatsapp:` prefix.

## What was NOT built here

- No intent detection — "How do I get to X" got the same echo reply as "asdfgh".
- No geocoding, routing, or sessions.
- Many module stubs (`app/intent/detector.py`, `app/resolver/locationiq.py`, `app/routing/locationiq.py`, `app/session/store.py`, `app/formatting/responses.py`, `app/abuse/limits.py`, `app/flows/{directions,nearby,help}.py`) raise `NotImplementedError`.

That's fine — they're known stubs filled in by later phases.
