"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def create_app() -> FastAPI:
    app = FastAPI(title="MiHomes", docs_url=None, redoc_url=None)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

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

    return app


app = create_app()
