"""MiHomes Estate Manager — Home Assistant Integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_API_URL, DOMAIN
from .coordinator import MiHomesCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.TODO,
]

type MiHomesConfigEntry = ConfigEntry[MiHomesCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MiHomesConfigEntry) -> bool:
    """Set up MiHomes from a config entry."""
    api_url = entry.data[CONF_API_URL]
    coordinator = MiHomesCoordinator(hass, api_url)

    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as err:
        await coordinator.async_close()
        raise ConfigEntryNotReady(
            f"Cannot connect to MiHomes at {api_url}: {err}"
        ) from err

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    _LOGGER.info(
        "MiHomes integration loaded: %d properties from %s",
        len(coordinator.data.properties),
        api_url,
    )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MiHomesConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_close()
    return unloaded
