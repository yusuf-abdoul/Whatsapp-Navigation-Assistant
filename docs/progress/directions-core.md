# Directions Core — Intent, Geocoding, Session, Routing

**Date:** 2026-04-23
**Status:** Done. **Not yet committed** — all changes are in working tree.

## What we built

Turned the echo bot into a working directions assistant. A user asks "How do I get to Jabi Lake Mall?" → we figure out it's a directions request, find the place, ask for the user's starting point (text or live location), compute the route, and reply with distance + duration + a Google Maps link.

This is the slice that proves the orchestrator → resolver → router → formatter pipeline.

## Why this came when it did

Once the WhatsApp loop worked (foundation phase), the next visible jump is "the bot actually answers a directions question." Everything else (nearby search, abuse controls, contributor flow) is built on top of this core path.

## What's in place

### Intent detection ([app/intent/detector.py](app/intent/detector.py))
Rule-based regex classifier — no LLM, no training. Returns one of:
- `DIRECTION` + extracted destination text — matches phrasings like "how do I get to X", "directions to X", "where is X", "take me to X"
- `NEARBY` + category — "nearest pharmacy", "ATMs near me", "restaurants nearby"
- `HELP` — "help", "menu", "hi", "hello", "?"
- `CANCEL` — "cancel", "stop", "reset", "start over", "nvm", "never mind", "quit"
- `UNKNOWN` — anything else; falls through to a help nudge

### Geocoder ([app/resolver/locationiq.py](app/resolver/locationiq.py))
Forward geocoding via LocationIQ, biased to the Abuja FCT viewbox + Nigeria country filter. Applies the alias dictionary first ("banex" → "Banex Plaza, Wuse 2, Abuja") so common nicknames don't waste a network call.

Field-tested refinements after first real-traffic problems:
- **Sort by `importance`** descending instead of hard-filtering — we lost valid landmarks like Jabi Lake Mall to a too-aggressive cutoff.
- **Unbounded retry** — if the bounded-to-Abuja query returns empty, retry without `bounded=1`. Catches places whose OSM coordinates tip slightly outside the box.
- **Dedupe near-duplicates** by ~100 m grid (round lat/lon to 3 decimals).
- **8 s timeout** instead of 5 — free-tier LocationIQ from Nigerian networks needs the headroom.
- **Debug logging** of result count + top match + top importance — when ranking misbehaves, we can see what LocationIQ actually returned.

### Routing ([app/routing/locationiq.py](app/routing/locationiq.py))
LocationIQ driving directions. Returns a `Route` dataclass with `distance_m`, `duration_s`, and a `deep_link` (Google Maps URL) — that's all the orchestrator needs. Step-by-step breakdown is intentionally not requested; the corridor layer (Phase 1) will own that for commuter-style instructions.

### Response formatter ([app/formatting/responses.py](app/formatting/responses.py))
- `short_name(display_name)` — strips bureaucratic noise from LocationIQ display names: "Jabi Lake Mall, Jabi, Abuja Municipal Area Council, Federal Capital Territory, 900108, Nigeria" → "Jabi Lake Mall, Jabi, Abuja". Skips postal codes, country, and AMAC/FCT tails.
- `format_ambiguity(query, candidates)` — when geocoding returns multiple results, render a numbered prompt the user can pick from.
- `format_route(destination, route)` — combines short destination name + distance (1-decimal under 10 km, integer above) + duration + deep link.
- `format_error(kind)` — one canned message per `ErrorKind`.

### Session store ([app/session/store.py](app/session/store.py))
Redis-backed CRUD: `get`, `put`, `delete`. Every write refreshes the TTL (default 600 s = 10 min). If Redis is down, calls log a warning and degrade gracefully (treat as no session) — they don't raise.

### Orchestrator state machine ([app/flows/orchestrator.py](app/flows/orchestrator.py))
The conversation logic, derived from session contents (no explicit state flag):

```
IDLE → DIRECTION(X) → geocode → destination set → AWAITING_ORIGIN
AWAITING_ORIGIN + live location → route → reply → IDLE
AWAITING_ORIGIN + text(Y)       → geocode(Y) as origin → route → reply → IDLE
any state + new DIRECTION       → overwrite destination, re-enter AWAITING_ORIGIN
any state + CANCEL              → clear session
```

Live-location messages are always interpreted as origin — they can't be anything else. Text while awaiting origin is geocoded as the starting point (with a small prefix-stripper for "I'm at X / from X / near X" phrasings) so users don't have to phrase responses as commands.

## Validated end-to-end

Live tests on real WhatsApp via ngrok during this work surfaced (and fixed) several problems:
- Missing `whatsapp:` prefix in `TWILIO_WHATSAPP_FROM` → fixed.
- Wrong webhook URL in Twilio console → fixed via console.
- 5 s LocationIQ timeout → bumped to 8 s.
- "I couldn't find 'jabi lake mall'" after a previous query worked → caused by the hard importance filter, fixed by sorting only.
- "I'm currently at Surgicare Hospital" returned UNKNOWN before session state landed → fixed by adding the awaiting-origin branch.

## Files added

- `tests/unit/test_intent_detector.py` (24 tests across DIRECTION / NEARBY / HELP / CANCEL / UNKNOWN)
- `tests/unit/test_locationiq_geocoder.py` (10 tests; mocked with respx)
- `tests/unit/test_locationiq_router.py`
- `tests/unit/test_formatting_responses.py` (8 tests)
- `tests/unit/test_session_store.py`
- `tests/unit/test_orchestrator.py`

## Files filled in (were stubs)

- `app/intent/detector.py`
- `app/resolver/locationiq.py`
- `app/routing/locationiq.py`
- `app/formatting/responses.py`
- `app/session/store.py`

## Files changed

- `app/intent/types.py` — added `CANCEL` intent
- `app/flows/orchestrator.py` — replaced echo logic with the state machine above
- `app/config.py` — minor tweaks
- `pyproject.toml` — added `respx`, `fakeredis` (dev deps)

## What's NOT in here yet

- Nearby search — only the intent is detected; the actual lookup just sends "I can help you find X nearby. Share your live location to continue." (acknowledgment, no real implementation)
- Reply format is still LocationIQ-style (distance + ETA + map link). The bulleted commuter-narrative format is what Phase 1 (corridor schema + repository + integration) is building toward.
- Abuse controls (`app/abuse/limits.py` is still a stub).
- Contributor flow / portal / DB-backed routes — that's the corridor work that started in Phase 1a.

## State at end of phase

- 78 unit tests pass
- ruff + mypy clean
- Working tree dirty — needs a commit before merging the corridor work on top
