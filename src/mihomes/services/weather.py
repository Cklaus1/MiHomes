"""Weather service — fetch forecasts and generate alerts via Open-Meteo (no API key required)."""

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class CurrentWeather:
    temperature: float          # °F
    feels_like: float           # °F
    humidity: int               # %
    precipitation: float        # inches
    wind_speed: float           # mph
    wind_gusts: float           # mph
    weather_code: int
    description: str


@dataclass
class DailyForecast:
    date: date
    temp_high: float            # °F
    temp_low: float             # °F
    precipitation: float        # inches
    precip_probability: int     # %
    wind_gusts: float           # mph
    weather_code: int
    description: str


@dataclass
class WeatherForecast:
    property_name: str
    latitude: float
    longitude: float
    timezone: str
    current: CurrentWeather
    daily: list[DailyForecast]  # 7 days


# ---------------------------------------------------------------------------
# WMO weather code → human description
# ---------------------------------------------------------------------------

def _describe_code(code: int) -> str:
    if code == 0:
        return "Clear sky"
    if code in (1, 2, 3):
        return ("Mainly clear", "Partly cloudy", "Overcast")[code - 1]
    if code in (45, 48):
        return "Foggy"
    if code in (51, 53, 55):
        return "Drizzle"
    if code in (56, 57):
        return "Freezing drizzle"
    if code in (61, 63, 65):
        return ("Light rain", "Rain", "Heavy rain")[code - 61]
    if code in (66, 67):
        return "Freezing rain"
    if code in (71, 73, 75):
        return ("Light snow", "Snow", "Heavy snow")[code - 71]
    if code == 77:
        return "Snow grains"
    if code in (80, 81, 82):
        return ("Light showers", "Rain showers", "Heavy showers")[code - 80]
    if code in (85, 86):
        return "Snow showers"
    if code == 95:
        return "Thunderstorm"
    if code in (96, 99):
        return "Thunderstorm with hail"
    return "Unknown"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


# ---------------------------------------------------------------------------
# Geocoding (Nominatim / OpenStreetMap — free, no API key, handles full addresses)
# ---------------------------------------------------------------------------

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_USER_AGENT = "MiHomes/0.1 estate-management (local)"


def geocode_address(address: str) -> tuple[float, float] | None:
    """Return (latitude, longitude) for an address string, or None if not found.

    Uses Nominatim (OpenStreetMap) which handles full street addresses.
    Free, no API key required.
    """
    params = urllib.parse.urlencode({"q": address, "format": "json", "limit": 1})
    url = f"{_NOMINATIM_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read().decode())
            if results:
                return float(results[0]["lat"]), float(results[0]["lon"])
    except (urllib.error.URLError, KeyError, ValueError, IndexError):
        pass
    return None


# ---------------------------------------------------------------------------
# Forecast fetching
# ---------------------------------------------------------------------------

def fetch_forecast(latitude: float, longitude: float, property_name: str = "") -> WeatherForecast:
    """Fetch current conditions and 7-day forecast from Open-Meteo."""
    params = urllib.parse.urlencode({
        "latitude": latitude,
        "longitude": longitude,
        "current": ",".join([
            "temperature_2m", "apparent_temperature", "relative_humidity_2m",
            "precipitation", "weather_code", "wind_speed_10m", "wind_gusts_10m",
        ]),
        "daily": ",".join([
            "weather_code", "temperature_2m_max", "temperature_2m_min",
            "precipitation_sum", "precipitation_probability_max",
            "wind_gusts_10m_max",
        ]),
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
        "timezone": "auto",
        "forecast_days": 7,
    })
    url = f"https://api.open-meteo.com/v1/forecast?{params}"
    data = _get_json(url)

    c = data["current"]
    current = CurrentWeather(
        temperature=c["temperature_2m"],
        feels_like=c["apparent_temperature"],
        humidity=c["relative_humidity_2m"],
        precipitation=c["precipitation"],
        wind_speed=c["wind_speed_10m"],
        wind_gusts=c["wind_gusts_10m"],
        weather_code=c["weather_code"],
        description=_describe_code(c["weather_code"]),
    )

    d = data["daily"]
    daily = []
    for i, iso_date in enumerate(d["time"]):
        daily.append(DailyForecast(
            date=date.fromisoformat(iso_date),
            temp_high=d["temperature_2m_max"][i],
            temp_low=d["temperature_2m_min"][i],
            precipitation=d["precipitation_sum"][i] or 0.0,
            precip_probability=d["precipitation_probability_max"][i] or 0,
            wind_gusts=d["wind_gusts_10m_max"][i] or 0.0,
            weather_code=d["weather_code"][i],
            description=_describe_code(d["weather_code"][i]),
        ))

    return WeatherForecast(
        property_name=property_name,
        latitude=latitude,
        longitude=longitude,
        timezone=data.get("timezone", "UTC"),
        current=current,
        daily=daily,
    )


# ---------------------------------------------------------------------------
# Property-aware forecast (geocodes + caches lat/lon on the Property record)
# ---------------------------------------------------------------------------

def get_forecast_for_property(session: Session, prop) -> WeatherForecast | None:
    """
    Fetch weather for a Property model instance.
    Geocodes the address on first call and caches lat/lon on the record.

    Geocoding priority:
      1. Cached lat/lon on the property record
      2. Property address
      3. `weather.default_location` config value (e.g. "Atlanta, GA")
    Returns None if no location can be resolved or geocoding fails.
    """
    if prop.latitude is None or prop.longitude is None:
        from mihomes.services.config_service import get_config
        default_location = get_config(session, "weather.default_location")

        if prop.address:
            # Try full address with city hint first, then fall back to city only.
            # Private roads and demo addresses may not exist in Nominatim.
            location = f"{prop.address}, {default_location}" if default_location else prop.address
            coords = geocode_address(location)
            if coords is None and default_location:
                coords = geocode_address(default_location)
        else:
            coords = geocode_address(default_location) if default_location else None

        if coords is None:
            return None
        prop.latitude, prop.longitude = coords
        session.flush()

    try:
        return fetch_forecast(prop.latitude, prop.longitude, property_name=prop.name)
    except (urllib.error.URLError, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Weather alert generation
# ---------------------------------------------------------------------------

def generate_weather_alerts(session: Session) -> int:
    """
    Check the 7-day forecast for every property and create Alert records
    for significant weather events. Returns count of new alerts created.
    """
    from mihomes.models.alert import Alert, AlertSeverity, AlertStatus
    from mihomes.models.property import Property

    properties = session.query(Property).all()
    count = 0

    for prop in properties:
        forecast = get_forecast_for_property(session, prop)
        if forecast is None:
            continue

        for day in forecast.daily:
            alerts_for_day = _assess_day(day, prop.name)
            for severity, message, alert_key in alerts_for_day:
                # Deduplicate: one alert per property+key+date
                dedup_key = f"weather_{alert_key}_{prop.id}_{day.date.isoformat()}"
                exists = session.query(Alert).filter(
                    Alert.alert_type == "weather",
                    Alert.message.like(f"%{dedup_key}%"),
                    Alert.status != AlertStatus.RESOLVED,
                ).first()
                if exists:
                    continue

                session.add(Alert(
                    alert_type="weather",
                    source_entity_type="property",
                    source_entity_id=prop.id,
                    severity=severity,
                    message=f"{message} at {prop.name} on {day.date} [{dedup_key}]",
                ))
                count += 1

    session.flush()
    return count


def _assess_day(day: DailyForecast, prop_name: str) -> list[tuple]:
    """Return list of (AlertSeverity, message, key) for notable weather on a given day."""
    results = []

    # Frost / freeze
    if day.temp_low <= 20:
        results.append((
            AlertSeverity.CRITICAL,
            f"Extreme cold: low of {day.temp_low:.0f}°F — protect pipes and sensitive plants",
            "extreme_cold",
        ))
    elif day.temp_low <= 32:
        results.append((
            AlertSeverity.HIGH,
            f"Frost/freeze warning: low of {day.temp_low:.0f}°F — protect pipes and plants",
            "frost",
        ))

    # Heavy rain
    if day.precipitation >= 2.0:
        results.append((
            AlertSeverity.HIGH,
            f"Heavy rain forecast: {day.precipitation:.1f}\" — check drains, sump pumps, and open issues",
            "heavy_rain",
        ))
    elif day.precipitation >= 1.0:
        results.append((
            AlertSeverity.MEDIUM,
            f"Significant rain forecast: {day.precipitation:.1f}\" — monitor exterior conditions",
            "rain",
        ))

    # High winds
    if day.wind_gusts >= 60:
        results.append((
            AlertSeverity.CRITICAL,
            f"Dangerous wind gusts: {day.wind_gusts:.0f} mph — secure outdoor furniture and structures",
            "extreme_wind",
        ))
    elif day.wind_gusts >= 45:
        results.append((
            AlertSeverity.HIGH,
            f"High wind gusts: {day.wind_gusts:.0f} mph — secure outdoor furniture",
            "high_wind",
        ))

    # Thunderstorm with hail
    if day.weather_code in (96, 99):
        results.append((
            AlertSeverity.HIGH,
            f"Thunderstorm with hail forecast — secure vehicles and outdoor equipment",
            "hail",
        ))

    # Heavy snow
    if day.weather_code == 75 and day.precipitation >= 3.0:
        results.append((
            AlertSeverity.HIGH,
            f"Heavy snow forecast: {day.precipitation:.1f}\" — arrange snow removal",
            "heavy_snow",
        ))

    return results


# ---------------------------------------------------------------------------
# Forecast summary for AI context
# ---------------------------------------------------------------------------

def forecast_summary(forecast: WeatherForecast) -> str:
    """Return a compact text summary of a forecast for injection into AI context."""
    c = forecast.current
    lines = [
        f"## Weather — {forecast.property_name}",
        f"Current: {c.temperature:.0f}°F (feels {c.feels_like:.0f}°F), "
        f"{c.description}, wind {c.wind_speed:.0f} mph gusts {c.wind_gusts:.0f} mph, "
        f"humidity {c.humidity}%",
        "7-day forecast:",
    ]
    for day in forecast.daily:
        notable = ""
        if day.temp_low <= 32:
            notable = " [FROST WARNING]"
        elif day.wind_gusts >= 45:
            notable = " [HIGH WINDS]"
        elif day.precipitation >= 1.0:
            notable = " [HEAVY RAIN]"
        lines.append(
            f"  {day.date}: {day.description}, "
            f"high {day.temp_high:.0f}°F / low {day.temp_low:.0f}°F, "
            f"precip {day.precipitation:.2f}\" ({day.precip_probability}%), "
            f"gusts {day.wind_gusts:.0f} mph{notable}"
        )
    return "\n".join(lines)
