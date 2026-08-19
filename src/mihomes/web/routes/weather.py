"""Weather routes — widget and AI-powered alert/task generation."""

import logging
import re
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.authz.actions import Access
from mihomes.authz.declare import declares
from mihomes.web.deps import get_db, templates

logger = logging.getLogger(__name__)

router = APIRouter()

_WMO_ICON = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️",
    56: "🌧️", 57: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    66: "🌧️", 67: "🌧️",
    71: "❄️", 73: "❄️", 75: "❄️", 77: "❄️",
    80: "🌧️", 81: "🌧️", 82: "🌧️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}

_ZIP_RE = re.compile(r'\b(\d{5})(?:-\d{4})?\b')


def _icon(code: int) -> str:
    return _WMO_ICON.get(code, "🌡️")


def _extract_zip(address: str | None) -> str:
    if address:
        m = _ZIP_RE.search(address)
        if m:
            return m.group(1)
    return "—"


def _group_by_zip(properties) -> list[dict]:
    """Return list of {zip, props} dicts, preserving insertion order."""
    buckets: dict[str, list] = defaultdict(list)
    for prop in properties:
        buckets[_extract_zip(prop.address)].append(prop)
    return [{"zip": z, "props": ps} for z, ps in buckets.items()]


@router.get("", response_class=HTMLResponse)
@declares("property.view", Access.COLLECTION)
def weather_widget(request: Request, db: Session = Depends(get_db)):
    """Return the weather widget partial — loaded lazily by the dashboard."""
    from mihomes.models.property import Property
    from mihomes.services.weather import get_forecast_for_property

    properties = db.query(Property).all()
    groups = []
    for group in _group_by_zip(properties):
        # Fetch forecast from the first property that resolves successfully
        forecast = None
        for prop in group["props"]:
            try:
                forecast = get_forecast_for_property(db, prop)
            except Exception:
                forecast = None
            if forecast:
                break
        groups.append({
            "zip": group["zip"],
            "props": group["props"],
            "forecast": forecast,
            "icon": _icon(forecast.current.weather_code) if forecast else "❓",
        })
    db.commit()  # persist any cached lat/lon

    return templates.TemplateResponse(request, "partials/weather_widget.html", {
        "groups": groups,
        "result": None,
    })


@router.post("/analyze", response_class=HTMLResponse)
@declares("property.view", Access.COLLECTION)
def weather_analyze(request: Request, db: Session = Depends(get_db)):
    """Run weather alert generation + AI task suggestions for all properties."""
    from mihomes.models.property import Property
    from mihomes.services.weather import generate_weather_alerts, get_forecast_for_property
    from mihomes.services.weather_tasks import create_tasks_from_suggestions, suggest_tasks_for_weather

    properties = db.query(Property).all()
    groups = []
    total_alerts = 0
    total_tasks = 0
    property_summaries = []
    ai_error = None

    # 1. Weather alerts (no AI needed)
    try:
        total_alerts = generate_weather_alerts(db)
    except Exception:
        logger.exception("weather_analyze: suppressed exception")

    # 2. Build groups + AI task suggestions per property
    for group in _group_by_zip(properties):
        forecast = None
        for prop in group["props"]:
            try:
                forecast = get_forecast_for_property(db, prop)
            except Exception:
                forecast = None
            if forecast:
                break
        groups.append({
            "zip": group["zip"],
            "props": group["props"],
            "forecast": forecast,
            "icon": _icon(forecast.current.weather_code) if forecast else "❓",
        })

        if forecast is None or ai_error:
            continue

        # Only generate AI tasks for the primary property in each zip group
        primary = group["props"][0]
        try:
            suggestions = suggest_tasks_for_weather(db, primary, forecast)
            if suggestions:
                created = create_tasks_from_suggestions(db, primary.slug, suggestions)
                total_tasks += len(created)
                property_summaries.append({
                    "prop_name": primary.name,
                    "tasks": [t.title for t in created],
                })
        except Exception as e:
            err = str(e)
            if "api_key" in err.lower() or "apikey" in err.lower() or "unauthorized" in err.lower() or "authentication" in err.lower():
                ai_error = "AI API key not configured. Set it via CLI: mihomes config set anthropic.api_key <key>"
            else:
                ai_error = f"AI analysis failed: {err}"

    db.commit()

    result = {
        "alerts_created": total_alerts,
        "tasks_created": total_tasks,
        "property_summaries": property_summaries,
        "ai_error": ai_error,
    }

    return templates.TemplateResponse(request, "partials/weather_widget.html", {
        "groups": groups,
        "result": result,
    })
