# Phase 2a — Landing Page + Web Structure

**Date:** 2026-05-06
**Status:** Done. About to be committed.

## What we built

The first web surface: a public landing page plus stub pages for `/login`, `/signup`, and `/submit`. All Jinja-rendered inside the existing FastAPI app — no separate frontend, no build pipeline. HTMX + Tailwind load via CDN.

This sets up the structure the contributor portal needs. Auth (OTP), real submission form, and admin review come in the following phases.

## Why this came when it did

The product has two surfaces: WhatsApp (where users get directions) and the web (where contributors sign up + submit corridors). The chat side works. The web side has been zero. Even an "empty" landing page is now better than a stub `{"service": "wna"}` JSON response — it gives the team something to point at, and unblocks auth + submission work that depends on the page structure.

## What's in place

### Routes ([app/web/routes.py](../../app/web/routes.py))
Four GET routes, all server-rendered Jinja, none in the OpenAPI schema:
- `GET /` — landing page (replaces the old JSON root)
- `GET /login` — stub form (collects WA number, posts to `/login`)
- `GET /signup` — stub form (name + WA number)
- `GET /submit` — explainer page with "coming soon" notice

The forms use HTMX (`hx-post`, `hx-swap="outerHTML"`, `hx-disabled-elt="find button"`) so the submission flow is ready to hand off to the auth phase.

### Templates ([app/web/templates/](../../app/web/templates/))
- `base.html` — shared layout: nav (Home / Contribute / Sign in / Get started), footer, Inter font, custom Tailwind palette with a brand emerald accent.
- `landing.html` — hero, three-step "How it works", contributor CTA with a sample bot reply.
- `signup.html`, `login.html`, `submit.html` — minimal, consistent with the brand.

### Wired into [app/main.py](../../app/main.py)
- `app.include_router(web_router)` at startup
- The old `GET /` JSON handler removed; `/health` stays as the JSON health probe

### Dependency
- Added `jinja2>=3.1` to `pyproject.toml` (FastAPI's templating extension).

## Files added

- `app/web/__init__.py`
- `app/web/routes.py`
- `app/web/templates/base.html`
- `app/web/templates/landing.html`
- `app/web/templates/signup.html`
- `app/web/templates/login.html`
- `app/web/templates/submit.html`
- `tests/unit/test_web_routes.py` — 6 smoke tests

## Files changed

- `app/main.py` — mount the web router, drop the JSON `/` handler
- `pyproject.toml` — `jinja2` dep

## State checks

- 135 tests pass (was 129 before this slice)
- `ruff check` clean
- `mypy` clean
- Local server renders `/` correctly (page title, hero copy, CTA links)

## What's left in Phase 2

- **2b — Auth (WhatsApp OTP).** `users` table, sign-up POST that issues a 6-digit code via Twilio, verification page, signed session cookie.
- **2c — Route submission form.** Guided form mapping to the corridor schema (city, anchors with lat/lon, ordered segments). Submissions land as `corridors.status='pending'` with the contributor's `users.id`.
- **2d — Admin review.** Gated `/admin`, list pending corridors, approve/reject with status + `approved_at` updates.

## Open questions for the next phase

- **OTP delivery.** Twilio is set up for the bot; the same client can send OTP messages, but Meta Direct (production target) needs a verified WhatsApp business template for OTP. Worth flagging now.
- **Session cookie vs JWT.** I'd default to a signed cookie (HMAC) keyed off the user's id and a short TTL — simpler than JWT for an in-app flow. Open to override.
- **Marketing copy.** The hero/feature copy is decent placeholder, but should be reviewed by the PM before launch.
