"""Recurrence engine — calculate next due dates for recurring tasks."""

from calendar import monthrange
from datetime import date, timedelta

SEASON_CALENDAR: dict[str, dict[str, tuple[int, int]]] = {
    "northeast": {"spring": (3, 1), "summer": (6, 1), "fall": (9, 1), "winter": (12, 1)},
    "southeast": {"spring": (2, 15), "summer": (5, 15), "fall": (9, 15), "winter": (11, 15)},
    "mountain": {"spring": (4, 1), "summer": (6, 15), "fall": (9, 15), "winter": (11, 1)},
    "tropical": {"dry": (11, 1), "wet": (5, 1)},
    "default": {"spring": (3, 1), "summer": (6, 1), "fall": (9, 1), "winter": (12, 1)},
}


def add_months(d: date, months: int) -> date:
    """Add months to a date, clamping to end of month if needed."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, monthrange(year, month)[1])
    return date(year, month, day)


def calculate_next_due(
    frequency: str,
    current_due: date,
    climate_zone: str | None = None,
    season_spec: str | None = None,
) -> date | None:
    """Calculate the next due date based on frequency.

    Returns None for non-recurring (once) tasks.
    """
    match frequency:
        case "once":
            return None
        case "daily":
            return current_due + timedelta(days=1)
        case "weekly":
            return current_due + timedelta(weeks=1)
        case "biweekly":
            return current_due + timedelta(weeks=2)
        case "monthly":
            return add_months(current_due, 1)
        case "quarterly":
            return add_months(current_due, 3)
        case "annual":
            return add_months(current_due, 12)
        case "seasonal":
            return _next_seasonal_due(current_due, climate_zone, season_spec)
        case _:
            return None


def _next_seasonal_due(
    current_due: date,
    climate_zone: str | None,
    season_spec: str | None,
) -> date | None:
    """Find the next seasonal due date after current_due."""
    if not season_spec:
        return None

    zone = (climate_zone or "default").lower()
    calendar = SEASON_CALENDAR.get(zone, SEASON_CALENDAR["default"])
    seasons = [s.strip().lower() for s in season_spec.split(",")]

    candidates = []
    for season_name in seasons:
        if season_name not in calendar:
            continue
        month, day = calendar[season_name]
        for year_offset in range(3):  # check this year, next year, year after
            year = current_due.year + year_offset
            candidate = date(year, month, day)
            if candidate > current_due:
                candidates.append(candidate)

    return min(candidates) if candidates else None
