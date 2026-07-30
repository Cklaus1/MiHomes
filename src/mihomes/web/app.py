"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mihomes.config import UPLOADS_DIR, UPLOADS_URL_PREFIX, ensure_dirs
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
from mihomes.web.secure_static import SecureStaticFiles
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

    # User-uploaded documents live outside the package (survive pip upgrade,
    # included in backups — spec H34) and are served with nosniff + forced
    # attachment for non-inline types (spec D6).
    ensure_dirs()
    app.mount(
        UPLOADS_URL_PREFIX,
        SecureStaticFiles(directory=str(UPLOADS_DIR)),
        name="uploads",
    )

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
