"""Landing app factory — a stripped instance, mounting only Phase 0's routes.

**§7-N1 is the load-bearing constraint here.** `mihomes.web.app` calls
`include_router` 22 times with no authentication of any kind, over live estate
data — properties, staff, financials, documents. `web/server.py` binds to
127.0.0.1 by default for exactly that reason. This app is public, so it mounts
*none* of it: nothing in this module imports `mihomes.web`.

`GTM:247`: the Phase 0 deploy "must be a *stripped instance* mounting only the
landing, waitlist, and OAuth-stub routes."
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from mihomes.landing.ratelimit import TokenBucket
from mihomes.landing.routes import router as landing_router

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

# Public, unauthenticated, writes rows and sends email (D10).
RATE_LIMITED_PATHS = ("/waitlist", "/auth/google/callback")


def _client_ip(request: Request) -> str:
    """Client IP, honouring Fly's proxy header.

    Behind Fly every connection appears to come from the proxy, so keying the
    bucket on `request.client.host` would make one shared bucket for the whole
    internet — the global-counter failure the per-IP design exists to avoid.
    """
    forwarded = request.headers.get("fly-client-ip") or request.headers.get(
        "x-forwarded-for"
    )
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def create_landing_app() -> FastAPI:
    """Build the Phase 0 app. Does not touch the database at import time (N4)."""
    app = FastAPI(
        title="MiHomes — waitlist",
        docs_url=None,       # no interactive docs on a public marketing host
        redoc_url=None,
        openapi_url=None,
    )

    bucket = TokenBucket()

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.url.path in RATE_LIMITED_PATHS and not bucket.allow(
            _client_ip(request)
        ):
            logger.warning("rate limited: path=%s", request.url.path)
            return JSONResponse(
                {"detail": "Too many requests. Please try again shortly."},
                status_code=429,
            )
        return await call_next(request)

    app.include_router(landing_router)

    # One SVG plus inlined critical CSS — no bundler, no CDN, no web fonts (N5).
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    return app


def main() -> None:
    """`mihomes-landing` entry point."""
    import uvicorn

    uvicorn.run(
        create_landing_app(),
        # 0.0.0.0 is correct *here* and wrong for mihomes.web: this app is a
        # public marketing page with no estate data behind it.
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8080")),
    )
