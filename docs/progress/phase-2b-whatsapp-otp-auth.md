# Phase 2b — WhatsApp OTP Auth

**Date:** 2026-05-07
**Status:** Done. About to be committed.

## What we built

Sign-up and sign-in for the contributor portal, gated by a one-time code sent to the user's WhatsApp number. No passwords. Once verified, the user gets a signed cookie that keeps them logged in across page loads.

The same Twilio adapter that runs the bot also delivers the OTP message — one channel, one config.

## Why this came when it did

Phase 2a put up the public landing page and stub forms. Phase 2c (the route submission form) needs to know who's submitting so we can credit them later. Auth is the bridge.

## What's in place

### `users` table ([app/users/models.py](../../app/users/models.py))
- UUID PK
- `wa_number` (E.164, unique, indexed)
- `name` (optional)
- `is_admin` (bool, default false — flip in SQL until the admin tool lands)
- `created_at` / `updated_at` timestamps
- Migration: `alembic/versions/024050b6b283_add_users_table.py`

### Phone normalization ([app/auth/phone.py](../../app/auth/phone.py))
Accepts the most common ways Nigerians type a number and returns an E.164 string (or `None` for "we can't safely guess"):
- `+2348123456789` → as-is
- `08123456789` → `+2348123456789`
- `2348123456789` → `+2348123456789`
- `+1 415 523 8886` → `+14155238886` (other countries pass through if E.164)

Anything else returns `None` — better than guessing across countries.

### OTP store ([app/auth/otp.py](../../app/auth/otp.py))
Redis-backed with three keyspaces per number+kind:
- `otp:{kind}:{wa_number}` — the 6-digit code, 5-minute TTL
- `otp_attempts:{kind}:{wa_number}` — INCR counter; 3 wrong tries clears the slot
- `otp_cooldown:{kind}:{wa_number}` — 30-second resend cooldown

`kind` is `"signup"` or `"login"`. Codes for the two flows are independent (separate keys) so a pending login OTP doesn't interfere with a fresh signup. Verification is single-use and constant-time (`secrets.compare_digest`).

### OTP delivery ([app/auth/sender.py](../../app/auth/sender.py))
Reuses the existing channel adapter (`get_channel().send_text(...)`) so the dev sandbox just works. Last-mile failures are logged but don't raise — the code is in Redis already and the user's next move (entering a code that didn't arrive) surfaces the problem.

### Web routes ([app/web/routes.py](../../app/web/routes.py))
- `POST /signup` — normalize phone, issue OTP, send, swap form to verify partial
- `POST /login` — normalize phone, look up user, reject unknown numbers, issue OTP, send, swap to verify partial
- `POST /verify` — verify code, create user (signup) or look up (login), set session cookie, return `204` with `HX-Redirect: /submit`
- `POST /logout` — clear session, redirect to `/`

All form posts are HTMX (`hx-post`, `hx-target="#auth-form"`, `hx-swap="outerHTML"`) so error states swap in place — no full-page reload.

### Session cookie
Starlette's `SessionMiddleware` (signed via `itsdangerous`):
- Cookie: `wna_session`
- 14-day max-age (configurable via `WEB_SESSION_MAX_AGE_SECONDS`)
- `HttpOnly`, `SameSite=Lax`, `Secure` only in production
- Carries one key: `user_id` (UUID string)

`current_user(request)` reads it, looks up the User in Postgres, returns `None` if missing or invalid.

### Templates
- `_signup_form.html` / `_login_form.html` / `_verify_form.html` — HTMX partials. Each defaults `values` and `error` so they render fine inside full pages too.
- `signup.html` / `login.html` updated to include the partials.
- `submit.html` greets the logged-in user by name.
- `base.html` nav swaps "Sign in / Get started" for "Sign out" when a session cookie is present.

## Files added

- `alembic/versions/024050b6b283_add_users_table.py`
- `app/users/__init__.py`, `app/users/models.py`, `app/users/repository.py`
- `app/auth/__init__.py`, `app/auth/phone.py`, `app/auth/otp.py`, `app/auth/sender.py`, `app/auth/session.py`
- `app/web/templates/_signup_form.html`, `_login_form.html`, `_verify_form.html`
- `tests/unit/test_auth_phone.py` (16 tests)
- `tests/unit/test_auth_otp.py` (8 tests)
- `tests/integration/test_users_repository.py` (4 tests)
- `tests/integration/test_auth_flow.py` (6 end-to-end tests)

## Files changed

- `app/main.py` — `SessionMiddleware`
- `app/config.py` — `session_secret`, `web_session_max_age_seconds`
- `app/web/routes.py` — POST handlers + HTMX flow
- `app/web/templates/base.html` — auth-aware nav
- `app/web/templates/signup.html` / `login.html` / `submit.html`
- `tests/integration/conftest.py` — also truncate `users` on each test
- `alembic/env.py` — import the User model so `Base.metadata` knows about it
- `.env.example` — `SESSION_SECRET`, `WEB_SESSION_MAX_AGE_SECONDS`
- `pyproject.toml` — `itsdangerous`, `python-multipart` (form parsing)

## State checks

- 167 tests pass (was 135 before this slice)
- `ruff` clean
- `mypy` clean

## What's left in Phase 2

- **2c — Route submission form.** Authenticated users fill out city, anchors (with lat/lon), and ordered segments. Submissions land as `corridors.status='pending'` with `contributor_id` = the user's id.
- **2d — Admin review.** `/admin` gated by `is_admin`, lists pending corridors, approve/reject buttons.

## Open questions / known limitations

- **Twilio sandbox restriction.** Only WhatsApp numbers that have joined the sandbox can receive messages. For real users, that means the user has to "join" the sandbox once before signup works in dev. Production via Meta Direct (planned) will need a verified WhatsApp business template for OTP delivery.
- **No CSRF on form posts.** SameSite=Lax + an HTMX-only verify endpoint covers most of it for the MVP. Adding a token before launch is reasonable.
- **`is_admin` flip is manual.** No admin tooling yet. We just `UPDATE users SET is_admin = true WHERE wa_number = ...` for now. The admin console (Phase 2d) wires this up.
