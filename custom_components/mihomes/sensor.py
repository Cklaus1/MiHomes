"""MiHomes sensor platform — property health, task/issue counts."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import MiHomesCoordinator

_SEVERITY_WEIGHT = {"critical": 20, "high": 12, "medium": 5, "low": 2}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: MiHomesCoordinator = entry.runtime_data
    entities: list[SensorEntity] = []

    for prop in coordinator.data.properties:
        entities += [
            PropertyHealthSensor(coordinator, entry, prop),
            PropertyTaskCountSensor(coordinator, entry, prop),
            PropertyIssueCountSensor(coordinator, entry, prop),
        ]

    entities += [
        GlobalAlertCountSensor(coordinator, entry),
        GlobalOverdueTaskSensor(coordinator, entry),
    ]

    async_add_entities(entities, update_before_add=True)


def device_info_for_property(prop: dict) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, f"property_{prop['id']}")},
        name=prop["name"],
        manufacturer=MANUFACTURER,
        model=prop.get("property_type", "property").capitalize(),
    )


class MiHomesCoordinatorEntity(CoordinatorEntity[MiHomesCoordinator]):
    def __init__(self, coordinator: MiHomesCoordinator, entry: ConfigEntry, unique_suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{unique_suffix}"
        self._entry = entry


class PropertyHealthSensor(MiHomesCoordinatorEntity, SensorEntity):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:heart-pulse"

    def __init__(self, coordinator, entry, prop: dict) -> None:
        super().__init__(coordinator, entry, f"health_{prop['id']}")
        self._prop_id = prop["id"]
        self._attr_name = f"{prop['name']} Health Score"
        self._attr_device_info = device_info_for_property(prop)

    @property
    def native_value(self) -> int:
        score = 100
        for issue in self.coordinator.data.issues:
            if issue.get("property_id") == self._prop_id:
                score -= _SEVERITY_WEIGHT.get(issue.get("severity", "low"), 0)
        overdue = sum(
            1 for t in self.coordinator.data.tasks
            if t.get("property_id") == self._prop_id and t.get("overdue")
        )
        score -= min(overdue * 4, 20)
        return max(0, min(100, score))

    @property
    def extra_state_attributes(self) -> dict:
        v = self.native_value
        grade = "A" if v >= 90 else "B" if v >= 75 else "C" if v >= 60 else "D" if v >= 40 else "F"
        return {"grade": grade}


class PropertyTaskCountSensor(MiHomesCoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:checkbox-marked-circle-outline"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, prop: dict) -> None:
        super().__init__(coordinator, entry, f"tasks_{prop['id']}")
        self._prop_id = prop["id"]
        self._attr_name = f"{prop['name']} Open Tasks"
        self._attr_device_info = device_info_for_property(prop)

    @property
    def native_value(self) -> int:
        return sum(1 for t in self.coordinator.data.tasks if t.get("property_id") == self._prop_id)


class PropertyIssueCountSensor(MiHomesCoordinatorEntity, SensorEntity):
    _attr_icon = "mdi:alert-circle-outline"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry, prop: dict) -> None:
        super().__init__(coordinator, entry, f"issues_{prop['id']}")
        self._prop_id = prop["id"]
        self._attr_name = f"{prop['name']} Open Issues"
        self._attr_device_info = device_info_for_property(prop)

    @property
    def native_value(self) -> int:
        return sum(1 for i in self.coordinator.data.issues if i.get("property_id") == self._prop_id)


class GlobalAlertCountSensor(MiHomesCoordinatorEntity, SensorEntity):
    _attr_name = "MiHomes Active Alerts"
    _attr_icon = "mdi:bell-alert"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "global_alerts")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "global")},
            name="MiHomes",
            manufacturer=MANUFACTURER,
        )

    @property
    def native_value(self) -> int:
        return sum(
            1 for a in self.coordinator.data.alerts
            if a.get("status") in ("generated", "seen")
        )

    @property
    def extra_state_attributes(self) -> dict:
        critical = sum(
            1 for a in self.coordinator.data.alerts
            if a.get("severity") == "critical" and a.get("status") in ("generated", "seen")
        )
        return {"critical": critical}


class GlobalOverdueTaskSensor(MiHomesCoordinatorEntity, SensorEntity):
    _attr_name = "MiHomes Overdue Tasks"
    _attr_icon = "mdi:clock-alert-outline"
    _attr_state_class = SensorStateClass.TOTAL

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "global_overdue")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "global")},
            name="MiHomes",
            manufacturer=MANUFACTURER,
        )

    @property
    def native_value(self) -> int:
        return sum(1 for t in self.coordinator.data.tasks if t.get("overdue"))
