"""Request-scoped error handling and the request id (SPEC-005 Step 15, A31).

Three things that only work together:

1. **A request id**, attached to every request and returned in the response header, so the page
   a customer is looking at and the line in the log can be connected by someone reading a
   support ticket.
2. **A generic exception handler**, so an unhandled error renders a page rather than a stack
   trace — and emits exactly one structured record naming that id.
3. **`/healthz`**, which the product app does not have. C2 corrected the spec here: Step 15 says
   *"`/healthz` confirmed live from SPEC-001"*, but SPEC-001's lives on the **landing** app
   (`landing/routes.py:41`). The product app has none, so this adds it rather than confirming it.

## Why the handler does not swallow

`app.add_exception_handler(Exception, ...)` catches what nothing else did. The temptation is to
return 500 and move on; N15 is the rule against it, and the reason is that a GA service that
discards its own errors cannot learn it is broken — *the customer finds out first*. So the
handler logs with `exc_info` before responding, and the response carries the id that names that
log line.

**It extends rather than replaces.** `web/app.py` already registers two handlers
(`EntityNotFoundError`→404, `AmbiguousIdentifierError`→400) from the hardening run; FastAPI
dispatches most-specific-first, so those keep answering and this catches the rest. C4 corrected
the spec on that too — it claimed no handler was registered anywhere.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

__all__ = [
    "REQUEST_ID_HEADER",
    "RequestIdMiddleware",
    "current_request_id",
    "register_error_handlers",
]

REQUEST_ID_HEADER = "X-Request-ID"

#: The id of the request being served, for log records emitted anywhere beneath it.
#:
#: A ContextVar rather than a parameter threaded through the call stack: the services that log
#: are many layers below the request and none of them should have to know a request exists.
#: Same shape as `tenancy.current_account`, and it fails the same way — reading it outside a
#: request returns the default rather than raising, because a CLI log line with no request id is
#: correct rather than exceptional.
current_request_id: ContextVar[str] = ContextVar("current_request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Assign every request an id, bind it, and echo it back.

    **An inbound `X-Request-ID` is honoured**, so a reverse proxy or a load balancer that already
    stamps one keeps its correlation across the hop. It is truncated to 64 characters and used as
    an opaque token — never parsed, never trusted for anything but correlation, because it is
    caller-controlled input and a log field is exactly where an injected value would want to be.
    """

    async def dispatch(self, request: Request, call_next):
        inbound = (request.headers.get(REQUEST_ID_HEADER) or "").strip()
        request_id = inbound[:64] if inbound else uuid.uuid4().hex

        token = current_request_id.set(request_id)
        request.state.request_id = request_id
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)

        response.headers[REQUEST_ID_HEADER] = request_id
        return response


def check_database() -> None:
    """Raise if the product database is unreachable.

    A named function rather than inline in the route, matching `landing/routes.py:34` — the
    landing healthcheck's own tests patch its equivalent to simulate an outage, and a
    healthcheck whose failure path cannot be exercised is a healthcheck nobody has tested.

    `get_engine()` rather than a module-level `engine`: the engine is created lazily and
    `dispose_engine()` can replace it, so binding one at import time would leave this probing a
    connection pool the rest of the app has stopped using.
    """
    from sqlalchemy import text

    from mihomes.db import get_engine

    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def _wants_json(request: Request) -> bool:
    """Is this an API-ish caller?

    An HTMX fragment or a fetch() gets JSON; a browser navigation gets the error page. Deciding
    by `Accept` rather than by path keeps the two surfaces from needing separate handlers.
    """
    accept = request.headers.get("accept", "")
    return "application/json" in accept or request.headers.get("HX-Request") == "true"


def register_error_handlers(app: FastAPI, templates=None) -> None:
    """Register the catch-all handler and `/healthz` on `app`.

    `templates` is the Jinja environment the app already builds; passed in rather than imported
    so this module does not reach back into `web.app` and create the import cycle that would
    follow.
    """

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        """A31 — render the error page, emit **one** structured record carrying the request id.

        One record, not two: logging here *and* re-raising would double every incident in the
        aggregator, and the count is what an operator alerts on.
        """
        request_id = getattr(request.state, "request_id", current_request_id.get())

        logger.exception(
            "unhandled exception: %s %s",
            request.method,
            request.url.path,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )

        if _wants_json(request):
            return JSONResponse(
                status_code=500,
                content={"error": "internal_error", "request_id": request_id},
                headers={REQUEST_ID_HEADER: request_id},
            )

        if templates is not None:
            try:
                response = templates.TemplateResponse(
                    request,
                    "error.html",
                    {"request_id": request_id},
                    status_code=500,
                )
                # **Stamped here, not left to the middleware.** Measured: an unhandled exception
                # unwinds past `RequestIdMiddleware`, so its `response.headers[...] = ...` never
                # runs and the 500 came back with no `X-Request-ID` — on the one response where
                # a support ticket most needs it. The page body carries the id too; the header is
                # what a fetch() or a proxy log can read without parsing HTML.
                response.headers[REQUEST_ID_HEADER] = request_id
                return response
            except Exception:
                # The error page itself failed. Logged rather than swallowed (N15) — a broken
                # error template is invisible precisely when it matters — and the plain-text
                # fallback below still names the request id.
                logger.exception("error template failed to render")

        return PlainTextResponse(
            f"Something went wrong. Reference: {request_id}",
            status_code=500,
            headers={REQUEST_ID_HEADER: request_id},
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> JSONResponse:
        """Liveness. Checks the database. No auth, no PII.

        **Must genuinely fail when the database is gone** — the same contract as the landing
        app's (`landing/routes.py:41`). A healthcheck that always returns 200 keeps a broken
        deploy in rotation, which is worse than having none: it converts an outage into a silent
        one.

        The body carries a status and nothing else. No DSN, no version, no counts — this is the
        one route with no authentication, so everything it says is public.
        """
        try:
            check_database()
        except Exception:
            logger.exception("healthz: database unreachable")
            return JSONResponse({"status": "unhealthy"}, status_code=503)
        return JSONResponse({"status": "ok"})

    # `/healthz` is unauthenticated by design, so it must be exempt from the app-wide
    # declaration gate the same way the webhook and unsubscribe routes are. Declared here rather
    # than in the allowlist module because the route is defined here — keeping the two together
    # is what stops one from being moved without the other.
    healthz.__mihomes_undeclared_ok__ = True

    return None


def error_page_html(request_id: str) -> str:
    """The error page as a string, for the fallback path and for tests.

    Deliberately not the template: this is what renders when the *template* is the thing that
    broke, so it cannot depend on Jinja.
    """
    return (
        "<!doctype html><title>Something went wrong</title>"
        f"<h1>Something went wrong</h1><p>Reference: {request_id}</p>"
    )
