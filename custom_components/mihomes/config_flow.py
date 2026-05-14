"""Config flow for MiHomes integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.core import HomeAssistant

from .const import CONF_API_URL, DEFAULT_API_URL, DOMAIN
from .coordinator import MiHomesCoordinator

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_URL, default=DEFAULT_API_URL): str,
    }
)


async def _test_connection(hass: HomeAssistant, api_url: str) -> bool:
    coordinator = MiHomesCoordinator(hass, api_url)
    result = await coordinator.async_test_connection()
    await coordinator.async_close()
    return result


class MiHomesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MiHomes."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_url = user_input[CONF_API_URL].rstrip("/")

            # Prevent duplicate entries for the same URL
            await self.async_set_unique_id(api_url)
            self._abort_if_unique_id_configured()

            if await _test_connection(self.hass, api_url):
                return self.async_create_entry(
                    title=f"MiHomes ({api_url})",
                    data={CONF_API_URL: api_url},
                )
            errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
            description_placeholders={
                "default_url": DEFAULT_API_URL,
            },
        )
