"""Web (HTML) routes — landing page, auth, contributor portal.

Templates live in ``app/web/templates``; rendered via Jinja2. HTMX + Tailwind
loaded via CDN in the base layout, so there's no build step.

Phase 2a is structure only: landing copy + stub pages for /login, /signup,
/submit. Auth (OTP) and the submission form land in 2b and 2c.
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(include_in_schema=False)

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


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
    return templates.TemplateResponse(request, "submit.html", {"page": "submit"})
