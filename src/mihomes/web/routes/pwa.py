"""The installable-app files, served from the site root (SPEC-014 D12).

**Undeclared on purpose** (permanent allowlist in `test_route_declarations.py`): a service worker's
scope is capped at its script's directory, so `/static/sw.js` could never control `/chat/`; and a
manifest fetch carries no cookies by default, so one behind sign-in breaks install. Both are fixed
files — no input, no tenant data — the same argument as `/healthz`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()

PWA_DIR = Path(__file__).resolve().parent.parent / "pwa"


@router.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(PWA_DIR / "sw.js", media_type="text/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})


@router.get("/manifest.webmanifest", include_in_schema=False)
def manifest() -> FileResponse:
    return FileResponse(PWA_DIR / "manifest.webmanifest", media_type="application/manifest+json",
                        headers={"Cache-Control": "public, max-age=3600"})
