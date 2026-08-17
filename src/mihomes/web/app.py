"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mihomes.config import ensure_dirs
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError
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
    playbooks_route,
    properties,
    recurring,
    search,
    staff,
    tasks,
    templates_route,
    vendors,
    work_orders,
)
from mihomes.web.routes import calendar as calendar_route
from mihomes.web.routes import inventory as inventory_route
from mihomes.web.routes import library as library_route
from mihomes.web.routes import weather as weather_route
from mihomes.web.security import HostAndOriginGuardMiddleware

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    app = FastAPI(title="MiHomes", docs_url=None, redoc_url=None)

    # Reject cross-site state-changing requests and non-loopback Host headers
    # (H30): defends the localhost app against browser CSRF and DNS rebinding.
    app.add_middleware(HostAndOriginGuardMiddleware)

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

    return app


app = create_app()
