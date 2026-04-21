# Architecture

Read this first. The PRD is the source of truth for *what*; this doc covers *how*.

## System Shape

```
WhatsApp user
     │
     ▼
┌─────────────┐  webhook   ┌──────────────┐
│ Meta Cloud  │ ─────────▶ │ FastAPI app  │
│  (Twilio    │ ◀───────── │  (Render)    │
│   in dev)   │            └──────┬───────┘
└─────────────┘                   │
                                  ├──▶ LocationIQ (geocode / route / POI)
                                  ├──▶ Redis (session, rate limits)
                                  └──▶ structlog → stdout → Render logs → Metabase
```

## Module Boundaries

| Module | Owns | Depends on |
|---|---|---|
| `channel/` | WhatsApp I/O; adapter per provider | nothing |
| `intent/` | Rule-based classification of inbound text | nothing |
| `flows/` | Conversation orchestration per intent | session, resolver, routing, formatting |
| `resolver/` | Geocoding + alias expansion + ambiguity | LocationIQ, YAML aliases |
| `routing/` | Route generation (steps, distance, ETA) | LocationIQ |
| `session/` | Per-user short-lived state | Redis |
| `formatting/` | WhatsApp-safe response builders | nothing |
| `analytics/` | Event emission per PRD §9.2 | structlog |
| `abuse/` | Rate limits, cooldowns, blocklist | Redis |

Each module is a seam. To swap providers (Meta → Twilio, LocationIQ → Mapbox), change only that module.

## Request Flow

1. Provider POSTs webhook → `channel/` verifies signature and parses `InboundMessage`.
2. `abuse/` checks rate limit and cooldown; rejects if breached.
3. `flows/orchestrator.handle()` loads session from `session/`, classifies via `intent/`, dispatches.
4. Flow calls `resolver/` (geocode with alias expansion) and `routing/` as needed.
5. `formatting/` builds chat-ready text; `channel/` sends it back.
6. `analytics/` emits events at every meaningful step.

## Non-Negotiables

- **Never guess silently** — ambiguity always returns ranked options.
- **Fail fast** — external calls timeout at 2s; on timeout, honest fallback, no fabrication.
- **TTL everything** — session TTL 600s, rate counters windowed; no unbounded state.
- **No PII in logs** — hash user IDs if logged long-term; precise coordinates only in-flight.

## Provider Strategy

- **Dev:** Twilio WhatsApp sandbox (no verification lead time).
- **Prod:** Meta Cloud API Direct (≈10× cheaper at scale).
- Both implement `ChannelAdapter`. `CHANNEL_PROVIDER` env var selects at boot.
- Meta business verification runs in parallel with Sprint 1 dev so it's not on the critical path.

## Deployment

- Render (Frankfurt region — closest to Nigeria).
- `render.yaml` declares web service + managed key-value store.
- Secrets set in Render dashboard, never committed.
- Staging and production are separate Render environments pointing at separate Redis instances.

## Observability

- Structured JSON logs via `structlog`; Render ingests stdout automatically.
- Metabase queries Render logs via their log-drain integration (Sprint 4).
- Error tracking: Sentry free tier (Sprint 3).
