"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mihomes.web.routes import alerts, assets, contracts, dashboard, insurance, properties, recurring, tasks, issues, staff, vendors, budget, work_orders

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
    app.include_router(insurance.router, prefix="/insurance")
    app.include_router(recurring.router, prefix="/recurring")

    return app


app = create_app()
