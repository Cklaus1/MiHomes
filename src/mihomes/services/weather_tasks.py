"""Weather-based AI task suggestions — generate property-specific tasks from forecasts."""

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class WeatherTaskSuggestion:
    title: str
    description: str
    priority: str          # urgent / high / medium / low
    category: str          # TaskCategory value
    due_days: int          # days from today until due
    weather_trigger: str   # human-readable reason (e.g. "2.1\" rain forecast Thu")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the MiHomes Maintenance Advisor and Grounds Manager. \
Your job is to review an upcoming weather forecast for a specific property \
and suggest concrete, actionable maintenance tasks that should be done \
in response to the weather.

Rules:
- Only suggest tasks that are directly triggered by the forecast (rain, frost, wind, snow, heat).
- Consider the property's existing open issues and tasks — do not duplicate them, \
  but do suggest escalating priority on relevant existing work.
- Be specific about WHY the weather makes this task necessary.
- Prioritize tasks using the SPACE framework: Safety first, then Asset Protection, then Economy.
- Only suggest tasks worth doing — if the weather is mild, output nothing.
- For each task, choose the most relevant category from: \
  plumbing, hvac, electrical, exterior, landscaping, interior, appliances, operations, general.

Output format — output ONLY task blocks, nothing else. \
Each task block must follow this exact format:
TASK: <title>
PRIORITY: <urgent|high|medium|low>
CATEGORY: <category>
DUE_DAYS: <integer, days from today>
DESCRIPTION: <one or two sentences explaining what to do and why>
END

If no weather-triggered tasks are needed, output exactly: NO_TASKS
"""


# ---------------------------------------------------------------------------
# Core suggestion function
# ---------------------------------------------------------------------------

def suggest_tasks_for_weather(
    session: Session,
    prop,
    forecast,
) -> list[WeatherTaskSuggestion]:
    """
    Ask the AI to suggest tasks for a property given its forecast.
    Returns a list of WeatherTaskSuggestion (may be empty if weather is benign).
    """
    from mihomes.services.ai.ai_config import get_ai_api_key, get_ai_model, get_ai_provider_name
    from mihomes.services.ai.context import assemble_context
    from mihomes.services.ai.provider import get_provider
    from mihomes.services.ai.roles import ROLES
    from mihomes.services.weather import forecast_summary

    provider_name = get_ai_provider_name(session)
    api_key = get_ai_api_key(session, provider_name)
    model = get_ai_model(session, provider_name)
    # **Deliberately unmetered — SPEC-004 D11/N10.** This runs from the nightly automation, not
    # from a user action, and `PRICING` §5.2 exempts system-initiated calls: *"a limit that trips
    # a scheduled job is a bug — the user cannot upgrade their way out of something they did not
    # do."* Passing an `entry_point` here would count a household's quota against work it never
    # asked for, and the visible symptom would be a background job failing rather than an upgrade
    # prompt (N9's reasoning, applied to the meter instead of a gate).
    #
    # A15 asserts this stays true; `test_all_entry_points_metered` lists this module as a
    # declared exemption rather than an omission, so removing the exemption fails the suite.
    provider = get_provider(provider_name, api_key, model=model)

    # Build context: property + tasks + issues (maintenance role categories)
    role = ROLES["maintenance"]
    context = assemble_context(
        session, [role], "",
        property_slug=prop.slug,
    )

    # Append the weather forecast
    weather_text = forecast_summary(forecast)
    full_context = context + "\n\n" + weather_text

    # Notable weather summary for the query
    notable = _notable_weather_summary(forecast)
    if not notable:
        return []  # nothing significant — skip AI call

    query = (
        f"The property '{prop.name}' has the following notable weather coming up: {notable}. "
        f"What maintenance tasks should be done to prepare or respond? "
        f"Review the existing open tasks and issues before suggesting anything new."
    )

    response_text = provider.complete(_SYSTEM_PROMPT, query, context_data=full_context)
    return _parse_suggestions(response_text, notable)


def _notable_weather_summary(forecast) -> str:
    """Return a short human summary of notable weather, or '' if nothing significant."""
    items = []
    for day in forecast.daily:
        if day.temp_low <= 32:
            items.append(f"frost/freeze ({day.temp_low:.0f}°F low) on {day.date}")
        if day.precipitation >= 1.0:
            items.append(f"{day.precipitation:.1f}\" rain on {day.date}")
        if day.wind_gusts >= 45:
            items.append(f"{day.wind_gusts:.0f} mph wind gusts on {day.date}")
        if day.weather_code in (75, 96, 99):
            items.append(f"{day.description.lower()} on {day.date}")
    return "; ".join(items)


def _parse_suggestions(text: str, weather_trigger: str) -> list[WeatherTaskSuggestion]:
    """Parse the AI's structured task block output."""
    text = text.strip()
    if "NO_TASKS" in text:
        return []

    suggestions = []
    blocks = text.split("END")

    for block in blocks:
        block = block.strip()
        if not block or "TASK:" not in block:
            continue

        fields = {}
        for line in block.splitlines():
            line = line.strip()
            for key in ("TASK", "PRIORITY", "CATEGORY", "DUE_DAYS", "DESCRIPTION"):
                if line.startswith(f"{key}:"):
                    fields[key] = line[len(key) + 1:].strip()
                    break

        if "TASK" not in fields:
            continue

        try:
            due_days = int(fields.get("DUE_DAYS", "1"))
        except ValueError:
            due_days = 1

        priority = fields.get("PRIORITY", "medium").lower()
        if priority not in ("urgent", "high", "medium", "low"):
            priority = "medium"

        category = fields.get("CATEGORY", "general").lower().replace(" ", "-")

        suggestions.append(WeatherTaskSuggestion(
            title=fields["TASK"],
            description=fields.get("DESCRIPTION", ""),
            priority=priority,
            category=category,
            due_days=due_days,
            weather_trigger=weather_trigger,
        ))

    return suggestions


# ---------------------------------------------------------------------------
# Bulk runner — all properties
# ---------------------------------------------------------------------------

def generate_suggestions_all_properties(
    session: Session,
) -> dict[str, list[WeatherTaskSuggestion]]:
    """
    Run weather task suggestions for every property that has notable weather.
    Returns dict of property_slug → suggestions list.
    """
    from mihomes.models.property import Property
    from mihomes.services.weather import get_forecast_for_property

    results = {}
    props = session.query(Property).all()

    for prop in props:
        forecast = get_forecast_for_property(session, prop)
        if forecast is None:
            continue
        if not _notable_weather_summary(forecast):
            continue
        suggestions = suggest_tasks_for_weather(session, prop, forecast)
        if suggestions:
            results[prop.slug] = suggestions

    return results


# ---------------------------------------------------------------------------
# Task creation from accepted suggestions
# ---------------------------------------------------------------------------

def create_tasks_from_suggestions(
    session: Session,
    property_slug: str,
    suggestions: list[WeatherTaskSuggestion],
    indices: list[int] | None = None,
) -> list:
    """
    Create Task records from accepted suggestions.
    indices: 1-based list of suggestions to accept. None = accept all.
    Returns list of created Task objects.
    """
    from mihomes.models.task import TaskCategory, TaskPriority
    from mihomes.services.task import create_task

    to_create = [
        s for i, s in enumerate(suggestions, 1)
        if indices is None or i in indices
    ]

    created = []
    for s in to_create:
        due = date.today() + timedelta(days=s.due_days)

        try:
            priority = TaskPriority(s.priority)
        except ValueError:
            priority = TaskPriority.MEDIUM

        try:
            category = TaskCategory(s.category)
        except ValueError:
            category = TaskCategory.GENERAL

        desc = s.description
        if s.weather_trigger:
            desc = f"{desc}\n\nWeather trigger: {s.weather_trigger}".strip()

        task = create_task(
            session,
            s.title,
            property_slug,
            description=desc,
            priority=priority,
            due_date=due,
            category=category,
        )
        created.append(task)

    return created
