"""Web (HTML) routes — landing page, auth, contributor portal.

Templates live in ``app/web/templates``; rendered via Jinja2. HTMX + Tailwind
loaded via CDN in the base layout, so there's no build step.

Auth flow (Phase 2b):
- POST /signup — creates user-pending state, sends OTP, swaps form to verify
- POST /login  — same as signup but rejects unknown numbers
- POST /verify — checks OTP, creates user (signup-intent), sets session cookie,
  HX-Redirects to /submit
- POST /logout — clears session, returns to /
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.auth import otp, sender
from app.auth import session as auth_session
from app.auth.phone import normalize as normalize_phone
from app.corridors.db import session_factory
from app.users import repository as users_repo

router = APIRouter(include_in_schema=False)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


# --- GET pages -----------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def landing(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "landing.html", {"page": "home"})


@router.get("/login", response_class=HTMLResponse)
async def login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"page": "login"})


@router.get("/signup", response_class=HTMLResponse)
async def signup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "signup.html", {"page": "signup"})


@router.get("/submit", response_class=HTMLResponse)
async def submit(request: Request) -> HTMLResponse:
    user = await auth_session.current_user(request)
    return templates.TemplateResponse(request, "submit.html", {"page": "submit", "user": user})


# --- POST handlers (HTMX form submissions) ------------------------------


@router.post("/signup", response_class=HTMLResponse)
async def signup_post(
    request: Request, name: str = Form(...), wa_number: str = Form(...)
) -> HTMLResponse:
    canonical = normalize_phone(wa_number)
    if canonical is None:
        return _form_error(
            request,
            "_signup_form.html",
            "Please enter a valid phone number with country code (e.g. +234...).",
        )

    try:
        code = await otp.issue(canonical, kind="signup")
    except otp.OTPRateLimited as e:
        return _form_error(
            request, "_signup_form.html", str(e), values={"name": name, "wa_number": wa_number}
        )

    await sender.send_otp(canonical, code)
    return templates.TemplateResponse(
        request,
        "_verify_form.html",
        {"kind": "signup", "wa_number": canonical, "name": name},
    )


@router.post("/login", response_class=HTMLResponse)
async def login_post(request: Request, wa_number: str = Form(...)) -> HTMLResponse:
    canonical = normalize_phone(wa_number)
    if canonical is None:
        return _form_error(
            request,
            "_login_form.html",
            "Please enter a valid phone number with country code (e.g. +234...).",
        )

    factory = session_factory()
    async with factory() as db:
        existing = await users_repo.get_by_wa_number(db, canonical)
    if existing is None:
        return _form_error(
            request,
            "_login_form.html",
            "We don't have an account for that number. Sign up first.",
            values={"wa_number": wa_number},
        )

    try:
        code = await otp.issue(canonical, kind="login")
    except otp.OTPRateLimited as e:
        return _form_error(request, "_login_form.html", str(e), values={"wa_number": wa_number})

    await sender.send_otp(canonical, code)
    return templates.TemplateResponse(
        request,
        "_verify_form.html",
        {"kind": "login", "wa_number": canonical, "name": None},
    )


@router.post("/verify", response_class=HTMLResponse)
async def verify_post(
    request: Request,
    kind: str = Form(...),
    wa_number: str = Form(...),
    code: str = Form(...),
    name: str | None = Form(None),
) -> Response:
    if kind not in {"signup", "login"}:
        return _verify_error(request, kind, wa_number, name, "Invalid verification request.")

    ok = await otp.verify(wa_number, kind=kind, submitted=code)
    if not ok:
        return _verify_error(
            request,
            kind,
            wa_number,
            name,
            "That code didn't match (or it's expired). Try again, or request a new code.",
        )

    factory = session_factory()
    async with factory() as db:
        user = await users_repo.get_by_wa_number(db, wa_number)
        if user is None:
            if kind != "signup":
                return _verify_error(
                    request,
                    kind,
                    wa_number,
                    name,
                    "Account not found. Please sign up.",
                )
            user = await users_repo.create(db, wa_number=wa_number, name=name)
        await db.commit()

    auth_session.login(request, user)
    # HTMX honors HX-Redirect to do a client-side full navigation.
    response = HTMLResponse("", status_code=204)
    response.headers["HX-Redirect"] = "/submit"
    return response


@router.post("/logout")
async def logout_post(request: Request) -> RedirectResponse:
    auth_session.logout(request)
    return RedirectResponse("/", status_code=303)


# --- helpers -------------------------------------------------------------


def _form_error(
    request: Request,
    template: str,
    message: str,
    *,
    values: dict[str, str] | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(request, template, {"error": message, "values": values or {}})


def _verify_error(
    request: Request, kind: str, wa_number: str, name: str | None, message: str
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_verify_form.html",
        {"kind": kind, "wa_number": wa_number, "name": name, "error": message},
    )
