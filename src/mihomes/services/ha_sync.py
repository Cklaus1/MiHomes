"""Home Assistant supervisor API client.

MiHomes runs as an HA add-on. The Supervisor injects SUPERVISOR_TOKEN
automatically — no manual token or URL configuration is ever needed.

This module is the single point of contact between MiHomes and HA's
REST API. Everything goes through http://supervisor/core/api.
"""

from __future__ import annotations

import os
from typing import Any
import logging

logger = logging.getLogger(__name__)

_SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
_API_BASE = "http://supervisor/core/api"


def is_available() -> bool:
    """True when running inside an HA add-on with supervisor access."""
    return bool(_SUPERVISOR_TOKEN)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_SUPERVISOR_TOKEN}"}


async def fetch_states() -> list[dict]:
    """Fetch all current entity states from HA.

    Returns [] when not running as an add-on (dev / standalone).
    Each dict has: entity_id, state, attributes, last_changed, last_updated.
    """
    if not _SUPERVISOR_TOKEN:
        return []
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_API_BASE}/states", headers=_headers())
        r.raise_for_status()
        return r.json()


async def fetch_state(entity_id: str) -> dict | None:
    """Fetch a single entity state."""
    if not _SUPERVISOR_TOKEN:
        return None
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{_API_BASE}/states/{entity_id}", headers=_headers())
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


async def call_service(domain: str, service: str, data: dict[str, Any] | None = None) -> bool:
    """Call an HA service (e.g. light.turn_on, switch.toggle).

    Returns True on success. Silent no-op when not in add-on mode.
    """
    if not _SUPERVISOR_TOKEN:
        return False
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{_API_BASE}/services/{domain}/{service}",
            headers=_headers(),
            json=data or {},
        )
        return r.is_success


async def get_ha_version() -> str | None:
    """Return the running HA version string, or None."""
    if not _SUPERVISOR_TOKEN:
        return None
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{_API_BASE}/config", headers=_headers())
            r.raise_for_status()
            return r.json().get("version")
    except Exception:
        logger.exception("get_ha_version: suppressed exception")
        return


def group_states_by_domain(states: list[dict]) -> dict[str, list[dict]]:
    """Group a flat list of HA states by entity domain."""
    groups: dict[str, list[dict]] = {}
    for s in states:
        domain = s["entity_id"].split(".")[0]
        groups.setdefault(domain, []).append(s)
    return groups


def enrich_state(state: dict) -> dict:
    """Add convenience fields to a raw HA state dict."""
    attrs = state.get("attributes", {})
    domain = state["entity_id"].split(".")[0]
    return {
        **state,
        "domain": domain,
        "friendly_name": attrs.get("friendly_name") or state["entity_id"],
        "unit": attrs.get("unit_of_measurement") or "",
        "device_class": attrs.get("device_class") or "",
        "icon": attrs.get("icon") or _default_icon(domain, attrs.get("device_class")),
        "is_on": state.get("state") in ("on", "home", "open", "unlocked", "detected"),
        "is_unavailable": state.get("state") in ("unavailable", "unknown"),
    }


# ── Domain icon map (MDI names → used in template SVG lookup) ─────────────────

_DOMAIN_ICONS: dict[str, str] = {
    "sensor": "thermometer",
    "binary_sensor": "circle-small",
    "switch": "toggle-switch",
    "light": "lightbulb",
    "climate": "thermostat",
    "media_player": "television-play",
    "camera": "cctv",
    "lock": "lock",
    "cover": "garage",
    "person": "account",
    "device_tracker": "wifi",
    "automation": "robot",
    "script": "script-text",
    "scene": "palette",
    "input_boolean": "toggle-switch-variant",
    "input_number": "numeric",
    "weather": "weather-partly-cloudy",
    "sun": "white-balance-sunny",
    "alarm_control_panel": "shield-home",
    "fan": "fan",
    "vacuum": "robot-vacuum",
    "water_heater": "water-boiler",
}

_DEVICE_CLASS_ICONS: dict[str, str] = {
    "temperature": "thermometer",
    "humidity": "water-percent",
    "moisture": "water-alert",
    "motion": "motion-sensor",
    "door": "door",
    "window": "window-open",
    "smoke": "smoke-detector",
    "carbon_monoxide": "molecule-co",
    "battery": "battery",
    "power": "flash",
    "energy": "lightning-bolt",
    "pressure": "gauge",
    "illuminance": "brightness-5",
    "current": "current-ac",
    "voltage": "sine-wave",
    "gas": "gas-cylinder",
    "lock": "lock",
    "occupancy": "home-account",
    "connectivity": "wifi",
    "plug": "power-plug",
}


def _default_icon(domain: str, device_class: str | None) -> str:
    if device_class and device_class in _DEVICE_CLASS_ICONS:
        return _DEVICE_CLASS_ICONS[device_class]
    return _DOMAIN_ICONS.get(domain, "help-circle")
