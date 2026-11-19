"""`/legal/terms` and `/legal/privacy` — public, and 404 until published (2026-11-09).

Unauthenticated on purpose (`PERMANENT_ALLOWLIST`): terms and a privacy policy must be readable
before anyone signs up. They answer 404 until their `effective_date` is set (`mihomes.legal`),
which keeps the footer links behaving exactly as before until the founder and counsel sign off.

Rendered through the landing app's Jinja environment, like the signed-out marketing page at `/`,
so the pages look like the public site rather than the signed-in app.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from mihomes import legal

router = APIRouter()


def legal_page(slug: str) -> HTMLResponse:
    from mihomes.landing.templates_env import render_page

    doc = legal.published(slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="Not found")
    return HTMLResponse(render_page("legal.html", {"doc": doc}))


@router.get("/legal/terms", response_class=HTMLResponse)
def terms() -> HTMLResponse:
    return legal_page("terms")


@router.get("/legal/privacy", response_class=HTMLResponse)
def privacy() -> HTMLResponse:
    return legal_page("privacy")
