"""FastAPI dependencies shared across all routes."""

from pathlib import Path
from typing import Generator

from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from mihomes.db import get_session

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _time_label(t) -> str:
    """Format a time/datetime as '1pm', '1:30pm', etc. for calendar badges."""
    if t is None:
        return ""
    from datetime import datetime as _dt
    if isinstance(t, _dt):
        t = t.time()
    hour = int(t.strftime("%I"))  # 12-hour, strip leading zero via int()
    ampm = t.strftime("%p").lower()
    if t.minute == 0:
        return f"{hour}{ampm}"
    return f"{hour}:{t.minute:02d}{ampm}"


templates.env.filters["time_label"] = _time_label


def get_db() -> Generator[Session, None, None]:
    with get_session() as session:
        yield session
