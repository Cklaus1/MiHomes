"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mihomes.config import ensure_dirs
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError
from mihomes.web.deps import enforce_declared_action
from mihomes.web.errors import RequestIdMiddleware, register_error_handlers
from mihomes.web.routes import ai as ai_route
from mihomes.web.routes import (
    alerts,
    assets,
    books,
    budget,
    contracts,
    dashboard,
    documents,
    documents_download,  # noqa: E402
    issues,
    onboarding,
    playbooks_route,
    properties,
    recurring,
    search,
    settings,
    staff,
    tasks,
    team,
    templates_route,
    vendors,
    work_orders,
)
from mihomes.web.routes import auth as auth_route
from mihomes.web.routes import billing as billing_route
from mihomes.web.routes import calendar as calendar_route
from mihomes.web.routes import gateways as gateways_route
from mihomes.web.routes import inventory as inventory_route
from mihomes.web.routes import library as library_route
from mihomes.web.routes import password as password_route
from mihomes.web.routes import privacy as privacy_route
from mihomes.web.routes import unsubscribe as unsubscribe_route
from mihomes.web.routes import weather as weather_route
from mihomes.web.routes import webhooks as webhooks_route
from mihomes.web.security import HostAndOriginGuardMiddleware

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    # `enforce_declared_action` is where SPEC-003's 142 route declarations become enforcement
    # (§6 Step 7). Applied app-wide rather than per-route so there is exactly one place to
    # forget — and Step 4's harness guarantees every route carries a declaration for it to read.
    # Together they close N1: the declarations are verified, and the verification is consulted.
    #
    # Routes with no declaration are passed through untouched. That is safe only because the
    # temporary allowlist is empty (CEILING = 0), so `auth` is the sole undeclared module — and
    # it must stay reachable to callers who are not yet authenticated.
    app = FastAPI(
        title="MiHomes",
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(enforce_declared_action)],
    )

    # Reject cross-site state-changing requests and non-loopback Host headers
    # (H30): defends the localhost app against browser CSRF and DNS rebinding.
    app.add_middleware(HostAndOriginGuardMiddleware)

    # SPEC-005 Step 15 — a request id on every request, echoed in the response header, bound for
    # anything that logs beneath it (A31). Added *after* the guard, so it is the **outermost**
    # middleware: Starlette applies them in reverse registration order, and an id assigned
    # outside the guard means even a rejected request carries one. A 400 nobody can correlate to
    # a log line is the support ticket this exists to prevent.
    app.add_middleware(RequestIdMiddleware)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # User-uploaded documents live outside the package (survive pip upgrade, included in
    # backups — spec H34).
    #
    # **The static mount that used to be here was a cross-tenant hole (G11 · A14).**
    # `app.mount(UPLOADS_URL_PREFIX, SecureStaticFiles(directory=UPLOADS_DIR))` served the whole
    # uploads directory with no authentication and no tenant check, so any request that could reach
    # the app could fetch any tenant's document. Unguessable upload filenames were the only thing
    # in the way, and generated reports were named from their titles, so not even that held.
    #
    # Documents are now served by `documents_download.router`, which authorises against the
    # account prefix in the storage key before reading a byte. The mount is not merely narrowed —
    # it is gone, because a static mount has nowhere to put an authorisation check.
    ensure_dirs()

    app.include_router(dashboard.router)
    app.include_router(properties.router, prefix="/properties")
    app.include_router(tasks.router, prefix="/tasks")
    app.include_router(issues.router, prefix="/issues")
    app.include_router(staff.router, prefix="/staff")
    app.include_router(vendors.router, prefix="/vendors")
    app.include_router(budget.router, prefix="/budget")
    app.include_router(alerts.router, prefix="/alerts")
    app.include_router(assets.router, prefix="/assets")
    app.include_router(work_orders.router, prefix="/work-orders")
    app.include_router(contracts.router, prefix="/contracts")
    app.include_router(recurring.router, prefix="/recurring")
    app.include_router(search.router, prefix="/search")
    app.include_router(templates_route.router, prefix="/templates")
    app.include_router(documents.router, prefix="/documents")
    # Tenant-checked object download (G11 · A14). Registered without a prefix because it owns
    # its full path; it replaces the unauthenticated /uploads static mount.
    app.include_router(documents_download.router)

    # SPEC-004 Step 4 — `POST /webhooks/stripe`. Mounted with **no prefix**: the path is
    # registered in the Stripe dashboard, so it is external identity rather than an internal
    # routing choice, and `web/security.py` reads its prefix constant to exempt it from the Host
    # and Origin guards. Declares no matrix action; it is in `PERMANENT_ALLOWLIST`, authorised by
    # a signature over the raw body rather than by a session (N3).
    app.include_router(webhooks_route.router)

    # SPEC-006 Step 5 — `POST /webhooks/telegram`. Mounted at the root for the same reason and
    # under the same `/webhooks/` prefix, so `web/security.py`'s existing exemption covers it
    # without a second entry — the prefix scoping that module's comment describes as being "so a
    # second provider's endpoint inherits it". Also unauthenticated by session and in
    # `PERMANENT_ALLOWLIST`: Telegram is not a user, and the secret token echoed in
    # `X-Telegram-Bot-Api-Secret-Token` is what authorises the request (D7).
    app.include_router(gateways_route.router)

    # SPEC-004 Step 6 — checkout, portal, plan page. Owner-only via `billing.manage` (row 15),
    # enforced app-wide by `enforce_declared_action` rather than by a check in the handlers.
    app.include_router(billing_route.router)
    # SPEC-005 Step 7 — data export. Owner-only via `account.delete` (row 16): downloading
    # every row an account holds is the same authority as ending the account, and reusing the
    # row avoids a 21st matrix key for a distinction without a security difference.
    app.include_router(privacy_route.router)
    # SPEC-005 Step 9 — RFC 8058 one-click unsubscribe. Allowlisted: a mail client is not
    # a user, so there is no cookie, principal or account for the matrix to consult. It is
    # authenticated by an HMAC token over the address (N9) — see the module docstring.
    app.include_router(unsubscribe_route.router)
    # Sign-in / sign-out (G12). No prefix: these paths are fixed by the OAuth redirect URI
    # registered with Google, so they cannot move without reconfiguring the provider.
    app.include_router(auth_route.router)
    # SPEC-010 Step 3 — `/signup` and `/login`. Also no prefix, and for a plainer reason than
    # the OAuth one above: these are the paths a person types. Registered next to `auth` because
    # they are the same front door, reached by a different key.
    app.include_router(password_route.router)
    # SPEC-003 Steps 11-13. No prefix on `team`: it owns `/invite/{token}` and
    # `/accounts/switch` alongside `/team`, because those are the same feature seen from either
    # side of account membership.
    app.include_router(onboarding.router, prefix="/onboarding")
    app.include_router(team.router)
    # Step 15's config UI. No prefix: it owns `/settings` outright.
    app.include_router(settings.router)
    app.include_router(books.router, prefix="/books")
    app.include_router(ai_route.router, prefix="/ai")
    app.include_router(weather_route.router, prefix="/weather")
    app.include_router(library_route.router, prefix="/library")
    app.include_router(playbooks_route.router, prefix="/playbooks")
    app.include_router(inventory_route.router, prefix="/inventory")
    app.include_router(calendar_route.router, prefix="/calendar")

    # Centralized identifier-resolution errors (H31/R3): an unknown id/slug is a
    # 404, an ambiguous prefix is a 400. Routes no longer need per-handler
    # `if not x` guards — the service getters raise, these handlers translate.
    @app.exception_handler(EntityNotFoundError)
    async def _entity_not_found(request: Request, exc: EntityNotFoundError):
        return PlainTextResponse(str(exc), status_code=404)

    @app.exception_handler(AmbiguousIdentifierError)
    async def _ambiguous_identifier(request: Request, exc: AmbiguousIdentifierError):
        return PlainTextResponse(str(exc), status_code=400)

    # SPEC-005 Step 15 — the catch-all handler and `/healthz` (A31).
    #
    # **Registered after the two above, and that is not an ordering dependency.** FastAPI
    # dispatches an exception to the handler for its most specific matching type, so
    # `EntityNotFoundError` keeps reaching `_entity_not_found` regardless of position; the
    # generic handler catches what nothing else claimed. Written down because "registered last
    # so it does not shadow" is the plausible-but-wrong reading.
    register_error_handlers(app, templates=templates)

    @app.exception_handler(401)
    async def _unauthenticated_to_login(request: Request, exc):
        """Send an unauthenticated **browser** to `/login`; answer anything else with the 401.

        Before this, every route answered a signed-out visitor with a bare
        `{"detail":"Not authenticated"}` — the app read as broken rather than locked, and there
        was nowhere to click.

        **Content negotiation, not a blanket redirect.** An HTMX request or an API client asking
        for JSON must keep receiving the status code: turning a 401 into a 303 for HTMX would
        swap a login page into whatever fragment the request targeted, and a client checking for
        401 would silently follow a redirect and parse HTML as data.

        `/login` itself is excluded, or a signed-out visitor loops between it and this handler.
        """
        from fastapi.responses import JSONResponse

        wants_html = "text/html" in request.headers.get("accept", "")
        is_htmx = request.headers.get("hx-request") == "true"
        already_there = request.url.path == "/login"

        if wants_html and not is_htmx and not already_there:
            return RedirectResponse("/login", status_code=303)

        return JSONResponse(
            {"detail": getattr(exc, "detail", "Not authenticated")}, status_code=401
        )

    return app


app = create_app()
