"""Tests for weather service."""

import json
from datetime import date
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from mihomes.services.weather import (
    CurrentWeather,
    DailyForecast,
    WeatherForecast,
    _assess_day,
    _describe_code,
    _get_json,
    fetch_forecast,
    forecast_summary,
    geocode_address,
    generate_weather_alerts,
    get_forecast_for_property,
)
from mihomes.models.property import Property, PropertyType
from mihomes.models.alert import Alert, AlertSeverity, AlertStatus


# ── _describe_code ────────────────────────────────────────────────────────────

class TestDescribeCode:
    def test_clear_sky(self):
        assert _describe_code(0) == "Clear sky"

    def test_mainly_clear(self):
        assert _describe_code(1) == "Mainly clear"

    def test_partly_cloudy(self):
        assert _describe_code(2) == "Partly cloudy"

    def test_overcast(self):
        assert _describe_code(3) == "Overcast"

    def test_foggy(self):
        assert _describe_code(45) == "Foggy"
        assert _describe_code(48) == "Foggy"

    def test_drizzle(self):
        assert _describe_code(51) == "Drizzle"

    def test_freezing_drizzle(self):
        assert _describe_code(56) == "Freezing drizzle"

    def test_light_rain(self):
        assert _describe_code(61) == "Light rain"

    def test_rain(self):
        assert _describe_code(63) == "Rain"

    def test_heavy_rain(self):
        assert _describe_code(65) == "Heavy rain"

    def test_freezing_rain(self):
        assert _describe_code(66) == "Freezing rain"

    def test_light_snow(self):
        assert _describe_code(71) == "Light snow"

    def test_snow(self):
        assert _describe_code(73) == "Snow"

    def test_heavy_snow(self):
        assert _describe_code(75) == "Heavy snow"

    def test_snow_grains(self):
        assert _describe_code(77) == "Snow grains"

    def test_light_showers(self):
        assert _describe_code(80) == "Light showers"

    def test_rain_showers(self):
        assert _describe_code(81) == "Rain showers"

    def test_heavy_showers(self):
        assert _describe_code(82) == "Heavy showers"

    def test_snow_showers(self):
        assert _describe_code(85) == "Snow showers"

    def test_thunderstorm(self):
        assert _describe_code(95) == "Thunderstorm"

    def test_thunderstorm_with_hail(self):
        assert _describe_code(96) == "Thunderstorm with hail"
        assert _describe_code(99) == "Thunderstorm with hail"

    def test_unknown_code(self):
        assert _describe_code(999) == "Unknown"


# ── _assess_day ───────────────────────────────────────────────────────────────

def _make_day(temp_high=75, temp_low=60, precip=0.0, wind_gusts=10, code=0):
    return DailyForecast(
        date=date.today(),
        temp_high=temp_high,
        temp_low=temp_low,
        precipitation=precip,
        precip_probability=10,
        wind_gusts=wind_gusts,
        weather_code=code,
        description=_describe_code(code),
    )


class TestAssessDay:
    def test_extreme_cold_below_20(self):
        day = _make_day(temp_low=15)
        results = _assess_day(day, "Test Prop")
        severities = [r[0] for r in results]
        assert AlertSeverity.CRITICAL in severities

    def test_frost_between_20_and_32(self):
        day = _make_day(temp_low=28)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "frost" in keys

    def test_no_cold_alert_above_32(self):
        day = _make_day(temp_low=40)
        results = _assess_day(day, "Test Prop")
        assert not any(r[2] in ("frost", "extreme_cold") for r in results)

    def test_heavy_rain_at_2_inches(self):
        day = _make_day(precip=2.0)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "heavy_rain" in keys

    def test_significant_rain_at_1_inch(self):
        day = _make_day(precip=1.0)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "rain" in keys

    def test_no_rain_alert_under_1_inch(self):
        day = _make_day(precip=0.5)
        results = _assess_day(day, "Test Prop")
        assert not any(r[2] in ("rain", "heavy_rain") for r in results)

    def test_extreme_wind_at_60mph(self):
        day = _make_day(wind_gusts=60)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "extreme_wind" in keys

    def test_high_wind_at_45mph(self):
        day = _make_day(wind_gusts=45)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "high_wind" in keys

    def test_hail_code_96(self):
        day = _make_day(code=96)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "hail" in keys

    def test_hail_code_99(self):
        day = _make_day(code=99)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "hail" in keys

    def test_heavy_snow_code_75_with_enough_precip(self):
        day = _make_day(code=75, precip=3.5)
        results = _assess_day(day, "Test Prop")
        keys = [r[2] for r in results]
        assert "heavy_snow" in keys

    def test_mild_day_returns_empty(self):
        day = _make_day(temp_low=55, precip=0.0, wind_gusts=15, code=0)
        results = _assess_day(day, "Test Prop")
        assert results == []


# ── geocode_address ───────────────────────────────────────────────────────────

class TestGeocodeAddress:
    def _make_response(self, lat="33.7490", lon="-84.3880"):
        data = [{"lat": lat, "lon": lon}]
        response_mock = MagicMock()
        response_mock.read.return_value = json.dumps(data).encode()
        response_mock.__enter__ = lambda s: s
        response_mock.__exit__ = MagicMock(return_value=False)
        return response_mock

    def test_returns_coordinates_on_success(self):
        with patch("urllib.request.urlopen", return_value=self._make_response("33.7490", "-84.3880")):
            result = geocode_address("Atlanta, GA")
        assert result is not None
        assert abs(result[0] - 33.749) < 0.01
        assert abs(result[1] - (-84.388)) < 0.01

    def test_returns_none_on_empty_results(self):
        response_mock = MagicMock()
        response_mock.read.return_value = b"[]"
        response_mock.__enter__ = lambda s: s
        response_mock.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=response_mock):
            result = geocode_address("Nowhere Land 99999")
        assert result is None

    def test_returns_none_on_url_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("Network error")):
            result = geocode_address("anywhere")
        assert result is None


# ── fetch_forecast ────────────────────────────────────────────────────────────

class TestFetchForecast:
    def _make_forecast_response(self):
        return {
            "current": {
                "temperature_2m": 72.5,
                "apparent_temperature": 70.0,
                "relative_humidity_2m": 55,
                "precipitation": 0.0,
                "wind_speed_10m": 8.0,
                "wind_gusts_10m": 12.0,
                "weather_code": 0,
            },
            "daily": {
                "time": ["2026-04-10", "2026-04-11", "2026-04-12"],
                "weather_code": [0, 61, 80],
                "temperature_2m_max": [78.0, 65.0, 70.0],
                "temperature_2m_min": [58.0, 55.0, 60.0],
                "precipitation_sum": [0.0, 0.8, 0.2],
                "precipitation_probability_max": [5, 60, 30],
                "wind_gusts_10m_max": [12.0, 25.0, 18.0],
            },
            "timezone": "America/New_York",
        }

    def _mock_urlopen(self, data):
        response_mock = MagicMock()
        response_mock.read.return_value = json.dumps(data).encode()
        response_mock.__enter__ = lambda s: s
        response_mock.__exit__ = MagicMock(return_value=False)
        return response_mock

    def test_returns_weather_forecast_object(self):
        with patch("urllib.request.urlopen",
                   return_value=self._mock_urlopen(self._make_forecast_response())):
            result = fetch_forecast(33.7, -84.4, property_name="Atlanta")
        assert isinstance(result, WeatherForecast)
        assert result.property_name == "Atlanta"

    def test_current_weather_populated(self):
        with patch("urllib.request.urlopen",
                   return_value=self._mock_urlopen(self._make_forecast_response())):
            result = fetch_forecast(33.7, -84.4)
        assert result.current.temperature == 72.5
        assert result.current.humidity == 55

    def test_daily_forecast_list_length(self):
        with patch("urllib.request.urlopen",
                   return_value=self._mock_urlopen(self._make_forecast_response())):
            result = fetch_forecast(33.7, -84.4)
        assert len(result.daily) == 3

    def test_daily_forecast_dates_parsed(self):
        with patch("urllib.request.urlopen",
                   return_value=self._mock_urlopen(self._make_forecast_response())):
            result = fetch_forecast(33.7, -84.4)
        assert result.daily[0].date == date(2026, 4, 10)

    def test_null_precipitation_defaults_to_zero(self):
        data = self._make_forecast_response()
        data["daily"]["precipitation_sum"] = [None, None, None]
        with patch("urllib.request.urlopen",
                   return_value=self._mock_urlopen(data)):
            result = fetch_forecast(33.7, -84.4)
        assert result.daily[0].precipitation == 0.0


# ── forecast_summary ──────────────────────────────────────────────────────────

class TestForecastSummary:
    def _make_forecast(self):
        current = CurrentWeather(
            temperature=72.0, feels_like=70.0, humidity=55,
            precipitation=0.0, wind_speed=8.0, wind_gusts=12.0,
            weather_code=0, description="Clear sky",
        )
        daily = [
            DailyForecast(
                date=date(2026, 4, 10), temp_high=78.0, temp_low=58.0,
                precipitation=0.0, precip_probability=5, wind_gusts=12.0,
                weather_code=0, description="Clear sky",
            ),
            DailyForecast(
                date=date(2026, 4, 11), temp_high=65.0, temp_low=28.0,
                precipitation=0.0, precip_probability=10, wind_gusts=12.0,
                weather_code=0, description="Clear sky",
            ),
            DailyForecast(
                date=date(2026, 4, 12), temp_high=70.0, temp_low=60.0,
                precipitation=1.5, precip_probability=60, wind_gusts=12.0,
                weather_code=61, description="Rain",
            ),
            DailyForecast(
                date=date(2026, 4, 13), temp_high=68.0, temp_low=55.0,
                precipitation=0.0, precip_probability=10, wind_gusts=50.0,
                weather_code=0, description="Clear sky",
            ),
        ]
        return WeatherForecast(
            property_name="Test Property",
            latitude=33.7, longitude=-84.4,
            timezone="America/New_York",
            current=current,
            daily=daily,
        )

    def test_summary_contains_property_name(self):
        result = forecast_summary(self._make_forecast())
        assert "Test Property" in result

    def test_summary_contains_current_conditions(self):
        result = forecast_summary(self._make_forecast())
        assert "72" in result  # current temp

    def test_frost_warning_flagged(self):
        result = forecast_summary(self._make_forecast())
        assert "[FROST WARNING]" in result

    def test_heavy_rain_flagged(self):
        result = forecast_summary(self._make_forecast())
        assert "[HEAVY RAIN]" in result

    def test_high_winds_flagged(self):
        result = forecast_summary(self._make_forecast())
        assert "[HIGH WINDS]" in result

    def test_contains_7_day_header(self):
        result = forecast_summary(self._make_forecast())
        assert "7-day forecast" in result


# ── get_forecast_for_property ─────────────────────────────────────────────────

class TestGetForecastForProperty:
    def test_returns_none_when_no_coords_no_address(self, session):
        prop = Property(name="No Location", slug="no-location",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        with patch("mihomes.services.weather.get_config", return_value=None):
            result = get_forecast_for_property(session, prop)
        assert result is None

    def test_uses_cached_coords(self, session):
        prop = Property(name="Cached Coords", slug="cached-coords",
                        property_type=PropertyType.PRIMARY,
                        latitude=33.7, longitude=-84.4)
        session.add(prop)
        session.flush()
        mock_forecast = MagicMock(spec=WeatherForecast)
        with patch("mihomes.services.weather.fetch_forecast", return_value=mock_forecast):
            result = get_forecast_for_property(session, prop)
        assert result is mock_forecast

    def test_geocodes_address_when_no_cached_coords(self, session):
        prop = Property(name="Atlanta House", slug="atlanta-house",
                        property_type=PropertyType.PRIMARY,
                        address="123 Peachtree St, Atlanta, GA")
        session.add(prop)
        session.flush()
        mock_forecast = MagicMock(spec=WeatherForecast)
        with patch("mihomes.services.weather.get_config", return_value=None), \
             patch("mihomes.services.weather.geocode_address", return_value=(33.7, -84.4)), \
             patch("mihomes.services.weather.fetch_forecast", return_value=mock_forecast):
            result = get_forecast_for_property(session, prop)
        assert result is mock_forecast
        # Coords should be cached on the property
        assert prop.latitude == 33.7

    def test_uses_default_location_when_geocode_fails(self, session):
        prop = Property(name="Private Estate", slug="private-estate",
                        property_type=PropertyType.PRIMARY,
                        address="1 Private Rd, Secret Bay")
        session.add(prop)
        session.flush()
        mock_forecast = MagicMock(spec=WeatherForecast)
        with patch("mihomes.services.weather.get_config", return_value="Atlanta, GA"), \
             patch("mihomes.services.weather.geocode_address",
                   side_effect=[None, (33.7, -84.4)]), \
             patch("mihomes.services.weather.fetch_forecast", return_value=mock_forecast):
            result = get_forecast_for_property(session, prop)
        assert result is mock_forecast

    def test_returns_none_when_all_geocoding_fails(self, session):
        prop = Property(name="Unknown Location", slug="unknown-location",
                        property_type=PropertyType.PRIMARY,
                        address="Somewhere Unknown")
        session.add(prop)
        session.flush()
        with patch("mihomes.services.weather.get_config", return_value=None), \
             patch("mihomes.services.weather.geocode_address", return_value=None):
            result = get_forecast_for_property(session, prop)
        assert result is None

    def test_returns_none_on_fetch_error(self, session):
        import urllib.error
        prop = Property(name="Fetch Error", slug="fetch-error",
                        property_type=PropertyType.PRIMARY,
                        latitude=33.7, longitude=-84.4)
        session.add(prop)
        session.flush()
        with patch("mihomes.services.weather.fetch_forecast",
                   side_effect=urllib.error.URLError("timeout")):
            result = get_forecast_for_property(session, prop)
        assert result is None


# ── generate_weather_alerts ───────────────────────────────────────────────────

class TestGenerateWeatherAlerts:
    def _make_property(self, session, name, slug):
        prop = Property(name=name, slug=slug, property_type=PropertyType.PRIMARY,
                        latitude=33.7, longitude=-84.4)
        session.add(prop)
        session.flush()
        return prop

    def test_creates_alert_for_frost(self, session):
        prop = self._make_property(session, "Frost House", "frost-house")
        frost_forecast = MagicMock()
        frost_day = DailyForecast(
            date=date(2026, 4, 15), temp_high=45.0, temp_low=28.0,
            precipitation=0.0, precip_probability=10, wind_gusts=12.0,
            weather_code=0, description="Clear sky",
        )
        frost_forecast.daily = [frost_day]
        with patch("mihomes.services.weather.get_forecast_for_property", return_value=frost_forecast):
            count = generate_weather_alerts(session)
        assert count >= 1

    def test_deduplicates_alerts(self, session):
        prop = self._make_property(session, "Dedup House", "dedup-house")
        frost_forecast = MagicMock()
        frost_day = DailyForecast(
            date=date(2026, 4, 15), temp_high=45.0, temp_low=28.0,
            precipitation=0.0, precip_probability=10, wind_gusts=12.0,
            weather_code=0, description="Clear sky",
        )
        frost_forecast.daily = [frost_day]
        with patch("mihomes.services.weather.get_forecast_for_property", return_value=frost_forecast):
            count1 = generate_weather_alerts(session)
            count2 = generate_weather_alerts(session)
        assert count2 == 0  # deduplicated

    def test_no_alerts_for_mild_weather(self, session):
        prop = self._make_property(session, "Mild House", "mild-house")
        mild_forecast = MagicMock()
        mild_day = DailyForecast(
            date=date(2026, 4, 15), temp_high=75.0, temp_low=60.0,
            precipitation=0.2, precip_probability=10, wind_gusts=12.0,
            weather_code=0, description="Clear sky",
        )
        mild_forecast.daily = [mild_day]
        with patch("mihomes.services.weather.get_forecast_for_property", return_value=mild_forecast):
            count = generate_weather_alerts(session)
        assert count == 0

    def test_skips_properties_with_no_forecast(self, session):
        self._make_property(session, "No Forecast House", "no-forecast-house")
        with patch("mihomes.services.weather.get_forecast_for_property", return_value=None):
            count = generate_weather_alerts(session)
        assert count == 0
