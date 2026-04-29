"""Tests for weather_tasks service."""

from dataclasses import dataclass
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskCategory
from mihomes.services.weather_tasks import (
    WeatherTaskSuggestion,
    _notable_weather_summary,
    _parse_suggestions,
    create_tasks_from_suggestions,
)
from mihomes.services.weather import DailyForecast


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_day(
    dt=None, temp_high=75.0, temp_low=60.0,
    precipitation=0.0, precip_probability=10,
    wind_gusts=15.0, weather_code=0,
):
    return DailyForecast(
        date=dt or date.today(),
        temp_high=temp_high,
        temp_low=temp_low,
        precipitation=precipitation,
        precip_probability=precip_probability,
        wind_gusts=wind_gusts,
        weather_code=weather_code,
        description="Clear sky",
    )


def _make_forecast(days=None):
    from mihomes.services.weather import WeatherForecast, CurrentWeather
    current = CurrentWeather(
        temperature=72.0, feels_like=70.0, humidity=55,
        precipitation=0.0, wind_speed=8.0, wind_gusts=12.0,
        weather_code=0, description="Clear sky",
    )
    if days is None:
        days = [_make_day()]
    return WeatherForecast(
        property_name="Test Property",
        latitude=33.7, longitude=-84.4,
        timezone="America/New_York",
        current=current,
        daily=days,
    )


@pytest.fixture
def prop(session):
    p = Property(name="Test House", slug="test-house",
                 property_type=PropertyType.PRIMARY)
    session.add(p)
    session.flush()
    return p


# ── _notable_weather_summary ──────────────────────────────────────────────────

class TestNotableWeatherSummary:
    def test_returns_empty_for_mild_weather(self):
        forecast = _make_forecast([_make_day(temp_low=60, precipitation=0, wind_gusts=10)])
        result = _notable_weather_summary(forecast)
        assert result == ""

    def test_frost_detected(self):
        forecast = _make_forecast([_make_day(temp_low=28)])
        result = _notable_weather_summary(forecast)
        assert "frost/freeze" in result

    def test_heavy_rain_detected(self):
        forecast = _make_forecast([_make_day(precipitation=1.5)])
        result = _notable_weather_summary(forecast)
        assert "rain" in result

    def test_high_wind_detected(self):
        forecast = _make_forecast([_make_day(wind_gusts=50)])
        result = _notable_weather_summary(forecast)
        assert "wind gusts" in result

    def test_hail_weather_code_detected(self):
        forecast = _make_forecast([_make_day(weather_code=96)])
        result = _notable_weather_summary(forecast)
        assert result != ""

    def test_multiple_events_joined(self):
        days = [
            _make_day(temp_low=28, dt=date.today()),
            _make_day(precipitation=2.0, dt=date.today() + timedelta(days=1)),
        ]
        forecast = _make_forecast(days)
        result = _notable_weather_summary(forecast)
        assert ";" in result

    def test_weather_code_99_detected(self):
        forecast = _make_forecast([_make_day(weather_code=99)])
        result = _notable_weather_summary(forecast)
        assert result != ""

    def test_exact_32_degree_is_frost(self):
        forecast = _make_forecast([_make_day(temp_low=32)])
        result = _notable_weather_summary(forecast)
        assert "frost" in result

    def test_exactly_1_inch_rain(self):
        forecast = _make_forecast([_make_day(precipitation=1.0)])
        result = _notable_weather_summary(forecast)
        assert "rain" in result

    def test_exactly_45_mph_gusts(self):
        forecast = _make_forecast([_make_day(wind_gusts=45)])
        result = _notable_weather_summary(forecast)
        assert "wind gusts" in result


# ── _parse_suggestions ────────────────────────────────────────────────────────

class TestParseSuggestions:
    def test_no_tasks_returns_empty_list(self):
        result = _parse_suggestions("NO_TASKS", "mild weather")
        assert result == []

    def test_no_tasks_in_mixed_text(self):
        result = _parse_suggestions("All is well. NO_TASKS", "mild")
        assert result == []

    def test_parses_single_task(self):
        text = """TASK: Clear Gutters
PRIORITY: high
CATEGORY: exterior
DUE_DAYS: 2
DESCRIPTION: Heavy rain expected — clear gutters to prevent backup.
END"""
        results = _parse_suggestions(text, "2.0\" rain tomorrow")
        assert len(results) == 1
        r = results[0]
        assert r.title == "Clear Gutters"
        assert r.priority == "high"
        assert r.category == "exterior"
        assert r.due_days == 2
        assert "Heavy rain" in r.description

    def test_parses_multiple_tasks(self):
        text = """TASK: Clear Gutters
PRIORITY: high
CATEGORY: exterior
DUE_DAYS: 2
DESCRIPTION: Rain coming.
END
TASK: Protect Plants
PRIORITY: medium
CATEGORY: landscaping
DUE_DAYS: 1
DESCRIPTION: Frost tonight.
END"""
        results = _parse_suggestions(text, "frost and rain")
        assert len(results) == 2

    def test_defaults_for_missing_fields(self):
        text = """TASK: Inspect Drains
END"""
        results = _parse_suggestions(text, "some weather")
        assert len(results) == 1
        r = results[0]
        assert r.priority == "medium"
        assert r.due_days == 1
        assert r.category == "general"

    def test_invalid_due_days_defaults_to_1(self):
        text = """TASK: Fix something
PRIORITY: low
CATEGORY: general
DUE_DAYS: notanumber
DESCRIPTION: Do it.
END"""
        results = _parse_suggestions(text, "weather")
        assert results[0].due_days == 1

    def test_invalid_priority_defaults_to_medium(self):
        text = """TASK: Fix something
PRIORITY: extreme
CATEGORY: general
DUE_DAYS: 1
DESCRIPTION: Do it.
END"""
        results = _parse_suggestions(text, "weather")
        assert results[0].priority == "medium"

    def test_weather_trigger_attached(self):
        text = """TASK: Fix something
PRIORITY: high
CATEGORY: plumbing
DUE_DAYS: 1
DESCRIPTION: Rain coming.
END"""
        results = _parse_suggestions(text, "2.5\" rain on Tuesday")
        assert results[0].weather_trigger == "2.5\" rain on Tuesday"

    def test_skips_blocks_without_task_keyword(self):
        text = """Some intro text
END
TASK: Valid Task
PRIORITY: medium
CATEGORY: general
DUE_DAYS: 1
DESCRIPTION: Do it.
END"""
        results = _parse_suggestions(text, "weather")
        assert len(results) == 1
        assert results[0].title == "Valid Task"


# ── create_tasks_from_suggestions ─────────────────────────────────────────────

class TestCreateTasksFromSuggestions:
    def _sample_suggestions(self):
        return [
            WeatherTaskSuggestion(
                title="Clear Gutters",
                description="Rain expected",
                priority="high",
                category="exterior",
                due_days=2,
                weather_trigger="2\" rain Thursday",
            ),
            WeatherTaskSuggestion(
                title="Protect Pipes",
                description="Frost warning",
                priority="urgent",
                category="plumbing",
                due_days=1,
                weather_trigger="freeze 28°F Friday",
            ),
        ]

    def test_creates_all_tasks_when_no_indices(self, session, prop):
        suggestions = self._sample_suggestions()
        created = create_tasks_from_suggestions(session, prop.slug, suggestions)
        assert len(created) == 2

    def test_creates_subset_with_indices(self, session, prop):
        suggestions = self._sample_suggestions()
        created = create_tasks_from_suggestions(session, prop.slug, suggestions, indices=[1])
        assert len(created) == 1
        assert created[0].title == "Clear Gutters"

    def test_task_due_date_offset_correct(self, session, prop):
        suggestions = [
            WeatherTaskSuggestion(
                title="Check Drains",
                description="Prep",
                priority="medium",
                category="general",
                due_days=3,
                weather_trigger="rain",
            )
        ]
        created = create_tasks_from_suggestions(session, prop.slug, suggestions)
        expected_due = date.today() + timedelta(days=3)
        assert created[0].due_date == expected_due

    def test_weather_trigger_appended_to_description(self, session, prop):
        suggestions = [
            WeatherTaskSuggestion(
                title="Secure Furniture",
                description="Winds coming",
                priority="high",
                category="exterior",
                due_days=1,
                weather_trigger="50 mph gusts Saturday",
            )
        ]
        created = create_tasks_from_suggestions(session, prop.slug, suggestions)
        assert "50 mph gusts Saturday" in created[0].description

    def test_invalid_priority_falls_back_to_medium(self, session, prop):
        suggestions = [
            WeatherTaskSuggestion(
                title="Bad Priority Task",
                description="",
                priority="super-urgent",  # invalid
                category="general",
                due_days=1,
                weather_trigger="",
            )
        ]
        created = create_tasks_from_suggestions(session, prop.slug, suggestions)
        assert created[0].priority == TaskPriority.MEDIUM

    def test_invalid_category_falls_back_to_general(self, session, prop):
        suggestions = [
            WeatherTaskSuggestion(
                title="Bad Category Task",
                description="",
                priority="medium",
                category="made-up-category",  # invalid
                due_days=1,
                weather_trigger="",
            )
        ]
        created = create_tasks_from_suggestions(session, prop.slug, suggestions)
        assert created[0].category == TaskCategory.GENERAL

    def test_empty_suggestions_returns_empty(self, session, prop):
        created = create_tasks_from_suggestions(session, prop.slug, [])
        assert created == []
