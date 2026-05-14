"""MiHomes DataUpdateCoordinator — polls the MiHomes REST API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    SCAN_INTERVAL_MINUTES,
    API_PROPERTIES,
    API_TASKS,
    API_ISSUES,
    API_ALERTS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class MiHomesData:
    properties: list[dict] = field(default_factory=list)
    tasks: list[dict] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)


class MiHomesCoordinator(DataUpdateCoordinator[MiHomesData]):
    """Coordinator that polls the MiHomes REST API."""

    def __init__(self, hass: HomeAssistant, api_url: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MINUTES),
        )
        self.api_url = api_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    async def _fetch(self, path: str, params: dict | None = None) -> list[dict]:
        session = await self._get_session()
        url = f"{self.api_url}{path}"
        try:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                return await resp.json()
        except aiohttp.ClientResponseError as err:
            raise UpdateFailed(f"MiHomes API error {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise UpdateFailed(f"Cannot reach MiHomes at {url}: {err}") from err

    async def async_test_connection(self) -> bool:
        """Test that MiHomes API is reachable. Called during config flow."""
        try:
            await self._fetch(API_PROPERTIES)
            return True
        except Exception:
            return False

    async def _async_update_data(self) -> MiHomesData:
        """Fetch all data from MiHomes API."""
        try:
            properties, tasks, issues, alerts = await _gather(
                self._fetch(API_PROPERTIES),
                self._fetch(API_TASKS, {"status": "pending"}),
                self._fetch(API_ISSUES, {"open_only": "true"}),
                self._fetch(API_ALERTS),
            )
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Unexpected error: {err}") from err

        return MiHomesData(
            properties=properties,
            tasks=tasks,
            issues=issues,
            alerts=alerts,
        )

    async def async_complete_task(self, task_slug: str) -> None:
        """Mark a MiHomes task as complete."""
        session = await self._get_session()
        url = f"{self.api_url}{API_TASKS}/{task_slug}/complete"
        async with session.post(url, json={}) as resp:
            resp.raise_for_status()

    async def async_create_task(
        self,
        title: str,
        property_slug: str,
        *,
        due_date: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Create a new task in MiHomes."""
        session = await self._get_session()
        url = f"{self.api_url}{API_TASKS}"
        payload: dict = {"title": title, "property_id_or_slug": property_slug}
        if due_date:
            payload["due_date"] = due_date
        if description:
            payload["description"] = description
        async with session.post(url, json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def async_delete_task(self, task_slug: str) -> None:
        """Delete a task in MiHomes."""
        session = await self._get_session()
        url = f"{self.api_url}{API_TASKS}/{task_slug}"
        async with session.delete(url) as resp:
            resp.raise_for_status()

    async def async_close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


async def _gather(*coros):
    """Run coroutines concurrently."""
    import asyncio
    return await asyncio.gather(*coros)
