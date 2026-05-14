"""MiHomes binary sensor platform — critical issues, problem flags per property."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MANUFACTURER
from .coordinator import MiHomesCoordinator
from .sensor import MiHomesCoordinatorEntity, device_info_for_property


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: MiHomesCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []

    for prop in coordinator.data.properties:
        entities.append(PropertyHasIssuesBinarySensor(coordinator, entry, prop))

    entities.append(CriticalIssueBinarySensor(coordinator, entry))
    async_add_entities(entities, update_before_add=True)


class PropertyHasIssuesBinarySensor(MiHomesCoordinatorEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:home-alert"

    def __init__(self, coordinator, entry, prop: dict) -> None:
        super().__init__(coordinator, entry, f"has_issues_{prop['id']}")
        self._prop_id = prop["id"]
        self._attr_name = f"{prop['name']} Has Issues"
        self._attr_device_info = device_info_for_property(prop)

    @property
    def is_on(self) -> bool:
        return any(i.get("property_id") == self._prop_id for i in self.coordinator.data.issues)

    @property
    def extra_state_attributes(self) -> dict:
        issues = [i for i in self.coordinator.data.issues if i.get("property_id") == self._prop_id]
        return {"count": len(issues), "severities": [i.get("severity") for i in issues]}


class CriticalIssueBinarySensor(MiHomesCoordinatorEntity, BinarySensorEntity):
    _attr_name = "MiHomes Critical Issue"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert-octagon"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "critical_issue")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "global")},
            name="MiHomes",
            manufacturer=MANUFACTURER,
        )

    @property
    def is_on(self) -> bool:
        return any(i.get("severity") == "critical" for i in self.coordinator.data.issues)

    @property
    def extra_state_attributes(self) -> dict:
        critical = [i for i in self.coordinator.data.issues if i.get("severity") == "critical"]
        return {"count": len(critical), "titles": [i.get("title", "") for i in critical[:5]]}
