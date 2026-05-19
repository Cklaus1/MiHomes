"""Weather routes — widget and AI-powered alert/task generation."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from mihomes.web.deps import get_db, templates

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


def _icon(code: int) -> str:
    return _WMO_ICON.get(code, "🌡️")


@router.get("", response_class=HTMLResponse)
def weather_widget(request: Request, db: Session = Depends(get_db)):
    """Return the weather widget partial — loaded lazily by the dashboard."""
    from mihomes.models.property import Property
    from mihomes.services.weather import get_forecast_for_property

    properties = db.query(Property).all()
    forecasts = []
    for prop in properties:
        try:
            forecast = get_forecast_for_property(db, prop)
        except Exception:
            forecast = None
        forecasts.append({
            "prop": prop,
            "forecast": forecast,
            "icon": _icon(forecast.current.weather_code) if forecast else "❓",
        })
    db.commit()  # persist any cached lat/lon

    return templates.TemplateResponse(request, "partials/weather_widget.html", {
        "forecasts": forecasts,
        "result": None,
    })


@router.post("/analyze", response_class=HTMLResponse)
def weather_analyze(request: Request, db: Session = Depends(get_db)):
    """Run weather alert generation + AI task suggestions for all properties."""
    from mihomes.models.property import Property
    from mihomes.services.weather import get_forecast_for_property, generate_weather_alerts
    from mihomes.services.weather_tasks import suggest_tasks_for_weather, create_tasks_from_suggestions

    properties = db.query(Property).all()
    forecasts = []
    total_alerts = 0
    total_tasks = 0
    property_summaries = []
    ai_error = None

    # 1. Weather alerts (no AI needed)
    try:
        total_alerts = generate_weather_alerts(db)
    except Exception as e:
        pass

    # 2. AI task suggestions per property
    for prop in properties:
        try:
            forecast = get_forecast_for_property(db, prop)
        except Exception:
            forecast = None

        forecasts.append({
            "prop": prop,
            "forecast": forecast,
            "icon": _icon(forecast.current.weather_code) if forecast else "❓",
        })

        if forecast is None:
            continue

        try:
            suggestions = suggest_tasks_for_weather(db, prop, forecast)
            if suggestions:
                created = create_tasks_from_suggestions(db, prop.slug, suggestions)
                total_tasks += len(created)
                property_summaries.append({
                    "prop_name": prop.name,
                    "tasks": [t.title for t in created],
                })
        except Exception as e:
            err = str(e)
            if "api_key" in err.lower() or "apikey" in err.lower() or "unauthorized" in err.lower() or "authentication" in err.lower():
                ai_error = "AI API key not configured. Set it via CLI: mihomes config set anthropic.api_key <key>"
            else:
                ai_error = f"AI analysis failed: {err}"
            break

    db.commit()

    result = {
        "alerts_created": total_alerts,
        "tasks_created": total_tasks,
        "property_summaries": property_summaries,
        "ai_error": ai_error,
    }

    return templates.TemplateResponse(request, "partials/weather_widget.html", {
        "forecasts": forecasts,
        "result": result,
    })
