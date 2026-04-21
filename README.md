# WhatsApp Navigation Assistant (WNA)

Chat-based urban navigation for Nigeria, delivered through WhatsApp.

> "Google Maps, accessible through simple chat."

## Quick Start

```bash
uv sync
cp .env.example .env    # fill in secrets
uv run uvicorn app.main:app --reload
```

Health check: http://localhost:8000/health

## Stack

- **Channel:** WhatsApp Business Cloud API (Meta Direct), Twilio sandbox for dev
- **Backend:** Python 3.12 + FastAPI
- **Geocoding / Routing / Nearby:** LocationIQ
- **Session:** Redis (TTL-based)
- **Deploy:** Render (Frankfurt)

## Documentation

- [Architecture](docs/architecture.md)
- [Contributing](docs/contributing.md)
- [Decision records](docs/decisions/)
- [Runbooks](docs/runbooks/)

## Scope

**In:** commuter directions, nearby search, ambiguity handling, session context, map fallback, analytics, abuse controls.
**Out (MVP):** saved locations, voice I/O, Hausa/Yoruba, multi-stop, business listings.

See the PRD for full context.
