"""FastAPI dependencies shared across all routes."""

from pathlib import Path
from typing import Generator

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mihomes.db import get_session

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session
