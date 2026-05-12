"""MiHomes Web — FastAPI app factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from mihomes.web.routes import dashboard, properties, tasks, issues, staff, vendors, budget

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

    return app


app = create_app()
