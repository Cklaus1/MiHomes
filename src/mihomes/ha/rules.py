"""Sensor-to-action rules for the HA bridge.

Each rule defines:
  - pattern: regex matched against entity_id
  - state_trigger: state value(s) that trigger the rule (or a callable)
  - severity: issue severity
  - title_fn: callable(entity_id, state, attributes) -> str
  - description_fn: callable(entity_id, state, attributes) -> str | None
  - alert_only: if True, create an Alert instead of an Issue (default False)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from mihomes.models.issue import IssueSeverity


@dataclass
class HARule:
    pattern: str
    state_trigger: str | list[str] | Callable[[str], bool]
    severity: IssueSeverity
    title_fn: Callable[[str, str, dict], str]
    description_fn: Callable[[str, str, dict], str | None] = field(
        default=lambda eid, state, attrs: None
    )
    alert_only: bool = False

    def matches(self, entity_id: str, state: str) -> bool:
        if not re.search(self.pattern, entity_id, re.IGNORECASE):
            return False
        if callable(self.state_trigger):
            return self.state_trigger(state)
        if isinstance(self.state_trigger, list):
            return state in self.state_trigger
        return state == self.state_trigger


def _entity_name(entity_id: str) -> str:
    """Convert entity_id to human-readable name."""
    return entity_id.split(".")[-1].replace("_", " ").title()


RULES: list[HARule] = [
    # ── Water / moisture ─────────────────────────────────────────────────────
    HARule(
        pattern=r"(moisture|leak|water)",
        state_trigger="on",
        severity=IssueSeverity.HIGH,
        title_fn=lambda eid, s, a: f"Water leak detected — {_entity_name(eid)}",
        description_fn=lambda eid, s, a: (
            f"Sensor {eid} reported moisture/water. "
            f"Check area immediately. Friendly name: {a.get('friendly_name', eid)}"
        ),
    ),
    # ── Smoke / CO ───────────────────────────────────────────────────────────
    HARule(
        pattern=r"(smoke|carbon_monoxide|co_detector)",
        state_trigger="on",
        severity=IssueSeverity.CRITICAL,
        title_fn=lambda eid, s, a: f"SMOKE/CO alarm — {_entity_name(eid)}",
        description_fn=lambda eid, s, a: (
            f"Smoke or CO sensor {eid} triggered. "
            f"Evacuate and investigate immediately. Friendly name: {a.get('friendly_name', eid)}"
        ),
    ),
    # ── Flood ────────────────────────────────────────────────────────────────
    HARule(
        pattern=r"flood",
        state_trigger="on",
        severity=IssueSeverity.CRITICAL,
        title_fn=lambda eid, s, a: f"Flood sensor triggered — {_entity_name(eid)}",
        description_fn=lambda eid, s, a: (
            f"Flood sensor {eid} is active. Water damage risk. "
            f"Friendly name: {a.get('friendly_name', eid)}"
        ),
    ),
    # ── HVAC / temperature ───────────────────────────────────────────────────
    HARule(
        pattern=r"sensor\..*(temperature)",
        state_trigger=lambda s: _is_extreme_temp(s),
        severity=IssueSeverity.MEDIUM,
        title_fn=lambda eid, s, a: f"Extreme temperature — {_entity_name(eid)} ({s}°)",
        description_fn=lambda eid, s, a: (
            f"Temperature sensor {eid} reading {s}° "
            f"(unit: {a.get('unit_of_measurement', '?')}). "
            f"Check HVAC. Friendly name: {a.get('friendly_name', eid)}"
        ),
        alert_only=True,
    ),
    # ── Low battery ──────────────────────────────────────────────────────────
    HARule(
        pattern=r"sensor\..*(battery)",
        state_trigger=lambda s: _is_low_battery(s),
        severity=IssueSeverity.LOW,
        title_fn=lambda eid, s, a: f"Low battery — {_entity_name(eid)} ({s}%)",
        description_fn=lambda eid, s, a: (
            f"Battery sensor {eid} is at {s}%. "
            f"Friendly name: {a.get('friendly_name', eid)}"
        ),
        alert_only=True,
    ),
    # ── Door/window left open ────────────────────────────────────────────────
    HARule(
        pattern=r"binary_sensor\..*(door|window|garage)",
        state_trigger="on",
        severity=IssueSeverity.LOW,
        title_fn=lambda eid, s, a: f"Entry point open — {_entity_name(eid)}",
        description_fn=lambda eid, s, a: (
            f"{eid} is open. Friendly name: {a.get('friendly_name', eid)}"
        ),
        alert_only=True,
    ),
    # ── Motion in secured area ────────────────────────────────────────────────
    HARule(
        pattern=r"binary_sensor\..*(motion|occupancy)",
        state_trigger="on",
        severity=IssueSeverity.LOW,
        title_fn=lambda eid, s, a: f"Motion detected — {_entity_name(eid)}",
        description_fn=lambda eid, s, a: (
            f"Motion sensor {eid} triggered. "
            f"Friendly name: {a.get('friendly_name', eid)}"
        ),
        alert_only=True,
    ),
    # ── Power / energy spike ─────────────────────────────────────────────────
    HARule(
        pattern=r"sensor\..*(power|energy|watt)",
        state_trigger=lambda s: _is_power_spike(s),
        severity=IssueSeverity.LOW,
        title_fn=lambda eid, s, a: f"High energy usage — {_entity_name(eid)} ({s}W)",
        description_fn=lambda eid, s, a: (
            f"Power sensor {eid} reading {s} {a.get('unit_of_measurement', 'W')}. "
            f"Possible appliance fault. Friendly name: {a.get('friendly_name', eid)}"
        ),
        alert_only=True,
    ),
]


def _is_extreme_temp(state: str) -> bool:
    try:
        temp = float(state)
        return temp < 45 or temp > 95  # Fahrenheit range
    except (ValueError, TypeError):
        return False


def _is_low_battery(state: str) -> bool:
    try:
        return float(state) <= 20
    except (ValueError, TypeError):
        return state in ("low", "very_low")


def _is_power_spike(state: str) -> bool:
    try:
        return float(state) > 5000  # 5kW threshold
    except (ValueError, TypeError):
        return False


def find_matching_rules(entity_id: str, state: str) -> list[HARule]:
    return [r for r in RULES if r.matches(entity_id, state)]
