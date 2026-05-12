"""Web (HTML) routes — landing page, auth, contributor portal.

Templates live in ``app/web/templates``; rendered via Jinja2. HTMX + Tailwind
loaded via CDN in the base layout, so there's no build step.

Auth flow (Phase 2b):
- POST /signup — creates user-pending state, sends OTP, swaps form to verify
- POST /login  — same as signup but rejects unknown numbers
- POST /verify — checks OTP, creates user (signup-intent), sets session cookie,
  HX-Redirects to /submit
- POST /logout — clears session, returns to /

Submission flow (Phase 2c):
- GET /submit — form (auth-gated; signed-out users see a CTA)
- POST /submit — parses parallel-list form fields, validates via Pydantic,
  inserts a pending corridor with the user as contributor
- GET /submit/anchor-row, /submit/segment-row — return blank rows for HTMX
  "+ Add" buttons

Admin review (Phase 2d):
- GET /admin — pending queue, gated by users.is_admin
- GET /admin/corridors/{id} — one corridor's anchors + segments + submitter
- POST /admin/corridors/{id}/approve, /reject — decide a pending corridor
- POST /admin/anchors/{id} — fix an anchor's lat/lon
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.exc import NoResultFound
from starlette.datastructures import FormData

from app.auth import otp, sender
from app.auth import session as auth_session
from app.auth.phone import normalize as normalize_phone
from app.corridors import admin as admin_ops
from app.corridors.db import session_factory
from app.corridors.models import SEGMENT_MODES
from app.corridors.submission import (
    AnchorInput,
    CorridorSubmission,
    SegmentInput,
    SubmissionError,
    create_pending,
)
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
    return templates.TemplateResponse(
        request,
        "submit.html",
        {
            "page": "submit",
            "user": user,
            "modes": SEGMENT_MODES,
            "form": _empty_form_state(),
        },
    )


@router.get("/submit/anchor-row", response_class=HTMLResponse)
async def submit_anchor_row(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "_anchor_row.html", {"row": _empty_anchor_row()})


@router.get("/submit/segment-row", response_class=HTMLResponse)
async def submit_segment_row(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_segment_row.html", {"row": _empty_segment_row(), "modes": SEGMENT_MODES}
    )


@router.get("/submit/anchor-search", response_class=HTMLResponse)
async def submit_anchor_search(
    request: Request, q: str = "", city: str = "abuja"
) -> HTMLResponse:
    """Live-search existing anchors as the contributor types a name. Returns
    a small HTMX-swappable list — clicking a result fills the row's coords.

    Returns an empty body for queries under 2 chars so HTMX clears any stale
    suggestions without firing a full SQL query on every keystroke.
    """
    from app.corridors.repository import search_anchors  # local import to avoid cycle

    if len(q.strip()) < 2:
        return HTMLResponse("")

    factory = session_factory()
    async with factory() as db:
        results = await search_anchors(db, q, city=city, limit=5)

    return templates.TemplateResponse(
        request, "_anchor_suggestions.html", {"suggestions": list(results)}
    )


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


@router.post("/submit", response_class=HTMLResponse)
async def submit_post(request: Request) -> Response:
    user = await auth_session.current_user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)

    form = await request.form()
    raw = _collect_submission_form(form)

    # Build typed payload. Pydantic catches per-field errors; cross_validate
    # catches inter-field rules (destination must be in anchors, etc.).
    try:
        payload = CorridorSubmission(
            city=raw["city"],
            destination=raw["destination"],
            applicability_notes=raw.get("applicability_notes") or None,
            anchors=[AnchorInput(**a) for a in raw["anchors"]],
            segments=[SegmentInput(**s) for s in raw["segments"]],
        )
    except ValidationError as e:
        return _submit_error(request, user, raw, _summarize_pydantic(e))

    try:
        factory = session_factory()
        async with factory() as db:
            corridor = await create_pending(db, payload=payload, contributor_id=str(user.id))
            await db.commit()
    except SubmissionError as e:
        return _submit_error(request, user, raw, str(e))

    return templates.TemplateResponse(
        request,
        "_submit_success.html",
        {
            "corridor_id": str(corridor.id),
            "destination": payload.destination,
            "segment_count": len(payload.segments),
        },
    )


# --- admin (Phase 2d) ---------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
async def admin_queue(request: Request) -> Response:
    admin = await auth_session.current_admin(request)
    if admin is None:
        return RedirectResponse("/login", status_code=303)

    factory = session_factory()
    async with factory() as db:
        pending = await admin_ops.list_pending(db)
    return templates.TemplateResponse(
        request,
        "admin/queue.html",
        {"page": "admin", "user": admin, "pending": list(pending)},
    )


@router.get("/admin/corridors/{corridor_id}", response_class=HTMLResponse)
async def admin_corridor_detail(request: Request, corridor_id: uuid.UUID) -> Response:
    admin = await auth_session.current_admin(request)
    if admin is None:
        return RedirectResponse("/login", status_code=303)

    factory = session_factory()
    async with factory() as db:
        try:
            corridor = await admin_ops.get_detail(db, corridor_id)
        except NoResultFound:
            raise HTTPException(status_code=404, detail="corridor not found") from None
        submitter = await admin_ops.get_submitter(db, corridor)
        # Deduped list of anchors that appear on this corridor.
        anchors: dict[uuid.UUID, Any] = {}
        for s in corridor.segments:
            anchors[s.from_anchor.id] = s.from_anchor
            anchors[s.to_anchor.id] = s.to_anchor
        anchors[corridor.destination.id] = corridor.destination

    return templates.TemplateResponse(
        request,
        "admin/detail.html",
        {
            "page": "admin",
            "user": admin,
            "corridor": corridor,
            "submitter": submitter,
            "anchors": list(anchors.values()),
        },
    )


@router.post("/admin/corridors/{corridor_id}/approve")
async def admin_approve(request: Request, corridor_id: uuid.UUID) -> Response:
    admin = await auth_session.current_admin(request)
    if admin is None:
        return RedirectResponse("/login", status_code=303)
    factory = session_factory()
    async with factory() as db:
        try:
            await admin_ops.approve(db, corridor_id)
            await db.commit()
        except NoResultFound:
            raise HTTPException(status_code=404, detail="corridor not found") from None
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/corridors/{corridor_id}/reject")
async def admin_reject(request: Request, corridor_id: uuid.UUID) -> Response:
    admin = await auth_session.current_admin(request)
    if admin is None:
        return RedirectResponse("/login", status_code=303)
    factory = session_factory()
    async with factory() as db:
        try:
            await admin_ops.reject(db, corridor_id)
            await db.commit()
        except NoResultFound:
            raise HTTPException(status_code=404, detail="corridor not found") from None
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/anchors/{anchor_id}")
async def admin_update_anchor(
    request: Request,
    anchor_id: uuid.UUID,
    lat: float = Form(...),
    lon: float = Form(...),
    return_to: str = Form("/admin"),
) -> Response:
    admin = await auth_session.current_admin(request)
    if admin is None:
        return RedirectResponse("/login", status_code=303)
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="lat/lon out of range")
    factory = session_factory()
    async with factory() as db:
        try:
            await admin_ops.update_anchor_coords(db, anchor_id, lat=lat, lon=lon)
            await db.commit()
        except NoResultFound:
            raise HTTPException(status_code=404, detail="anchor not found") from None
    return RedirectResponse(return_to, status_code=303)


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


# --- submission form helpers --------------------------------------------


def _empty_anchor_row() -> dict[str, str]:
    return {"name": "", "lat": "", "lon": "", "aliases": ""}


def _empty_segment_row() -> dict[str, str]:
    return {
        "from_anchor": "",
        "to_anchor": "",
        "mode": "taxi",
        "instruction": "",
        "transfer": "false",
        "cost_ngn": "",
        "duration_min": "",
    }


def _empty_form_state() -> dict[str, object]:
    return {
        "city": "abuja",
        "destination": "",
        "applicability_notes": "",
        "anchors": [_empty_anchor_row(), _empty_anchor_row()],
        "segments": [_empty_segment_row()],
    }


def _collect_submission_form(form: FormData) -> dict[str, Any]:
    """Turn parallel form arrays into the nested shape the schema expects.

    Empty rows (anchor with no name; segment with no instruction) are dropped
    silently so contributors can leave blank "+ Add" rows behind.
    """
    anchor_names = form.getlist("anchor_name")
    anchor_lats = form.getlist("anchor_lat")
    anchor_lons = form.getlist("anchor_lon")
    anchor_aliases = form.getlist("anchor_aliases")

    anchors: list[dict[str, str]] = []
    for i, name in enumerate(anchor_names):
        if not str(name).strip():
            continue
        anchors.append(
            {
                "name": str(name),
                "lat": str(anchor_lats[i]) if i < len(anchor_lats) else "",
                "lon": str(anchor_lons[i]) if i < len(anchor_lons) else "",
                "aliases": str(anchor_aliases[i]) if i < len(anchor_aliases) else "",
            }
        )

    seg_from = form.getlist("seg_from")
    seg_to = form.getlist("seg_to")
    seg_mode = form.getlist("seg_mode")
    seg_instruction = form.getlist("seg_instruction")
    seg_transfer = form.getlist("seg_transfer")
    seg_cost = form.getlist("seg_cost_ngn")
    seg_duration = form.getlist("seg_duration_min")

    segments: list[dict[str, object]] = []
    for i, instr in enumerate(seg_instruction):
        if not str(instr).strip():
            continue
        cost = str(seg_cost[i] if i < len(seg_cost) else "").strip()
        duration = str(seg_duration[i] if i < len(seg_duration) else "").strip()
        segments.append(
            {
                "from_anchor": str(seg_from[i]) if i < len(seg_from) else "",
                "to_anchor": str(seg_to[i]) if i < len(seg_to) else "",
                "mode": str(seg_mode[i]) if i < len(seg_mode) else "",
                "instruction": str(instr),
                "transfer": (str(seg_transfer[i]) if i < len(seg_transfer) else "false") == "true",
                "cost_ngn": int(cost) if cost else None,
                "duration_min": int(duration) if duration else None,
            }
        )

    return {
        "city": str(form.get("city") or "").strip(),
        "destination": str(form.get("destination") or "").strip(),
        "applicability_notes": str(form.get("applicability_notes") or "").strip(),
        "anchors": anchors,
        "segments": segments,
    }


def _submit_error(request: Request, user: Any, raw: dict[str, Any], message: str) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "submit.html",
        {
            "page": "submit",
            "user": user,
            "modes": SEGMENT_MODES,
            "form": _form_state_from_raw(raw),
            "error": message,
        },
    )


def _form_state_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Re-populate the form with the user's submitted values after a validation error."""
    anchors = list(raw.get("anchors") or [])
    if not anchors:
        anchors = [_empty_anchor_row(), _empty_anchor_row()]
    segments_in = list(raw.get("segments") or [])
    segments: list[dict[str, str]] = []
    for s in segments_in:
        segments.append(
            {
                "from_anchor": str(s.get("from_anchor") or ""),
                "to_anchor": str(s.get("to_anchor") or ""),
                "mode": str(s.get("mode") or "taxi"),
                "instruction": str(s.get("instruction") or ""),
                "transfer": "true" if s.get("transfer") else "false",
                "cost_ngn": "" if s.get("cost_ngn") is None else str(s.get("cost_ngn")),
                "duration_min": "" if s.get("duration_min") is None else str(s.get("duration_min")),
            }
        )
    if not segments:
        segments = [_empty_segment_row()]
    return {
        "city": str(raw.get("city") or "abuja"),
        "destination": str(raw.get("destination") or ""),
        "applicability_notes": str(raw.get("applicability_notes") or ""),
        "anchors": anchors,
        "segments": segments,
    }


def _summarize_pydantic(err: ValidationError) -> str:
    lines: list[str] = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        lines.append(f"{loc}: {e['msg']}")
    return "\n".join(lines)
