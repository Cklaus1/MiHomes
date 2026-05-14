"""MiHomes To-do platform — property task lists, bidirectional sync."""

from __future__ import annotations

import logging
from datetime import date

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import MiHomesCoordinator
from .sensor import MiHomesCoordinatorEntity, device_info_for_property

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator: MiHomesCoordinator = entry.runtime_data
    entities = [
        MiHomesTaskList(coordinator, entry, prop)
        for prop in coordinator.data.properties
    ]
    async_add_entities(entities, update_before_add=True)


def _task_to_item(task: dict) -> TodoItem:
    due: date | None = None
    if task.get("due_date"):
        try:
            due = date.fromisoformat(task["due_date"])
        except (ValueError, TypeError):
            pass

    status = (
        TodoItemStatus.COMPLETE
        if task.get("status") == "completed"
        else TodoItemStatus.NEEDS_ACTION
    )
    return TodoItem(
        uid=task["slug"],
        summary=task["title"],
        status=status,
        due=due,
        description=task.get("description"),
    )


class MiHomesTaskList(MiHomesCoordinatorEntity, TodoListEntity):
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: MiHomesCoordinator, entry: ConfigEntry, prop: dict) -> None:
        super().__init__(coordinator, entry, f"todo_{prop['id']}")
        self._prop_id = prop["id"]
        self._prop_slug = prop["slug"]
        self._attr_name = f"{prop['name']} Tasks"
        self._attr_device_info = device_info_for_property(prop)

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            _task_to_item(t)
            for t in self.coordinator.data.tasks
            if t.get("property_id") == self._prop_id
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        due_str = item.due.isoformat() if isinstance(item.due, date) else None
        try:
            await self.coordinator.async_create_task(
                title=item.summary or "Untitled task",
                property_slug=self._prop_slug,
                due_date=due_str,
                description=item.description,
            )
        except Exception as err:
            _LOGGER.error("Failed to create MiHomes task: %s", err)
            raise
        await self.coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        if item.status == TodoItemStatus.COMPLETE and item.uid:
            try:
                await self.coordinator.async_complete_task(item.uid)
            except Exception as err:
                _LOGGER.error("Failed to complete MiHomes task %s: %s", item.uid, err)
                raise
            await self.coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        import asyncio
        results = await asyncio.gather(
            *[self.coordinator.async_delete_task(uid) for uid in uids],
            return_exceptions=True,
        )
        for uid, result in zip(uids, results):
            if isinstance(result, Exception):
                _LOGGER.error("Failed to delete MiHomes task %s: %s", uid, result)
        await self.coordinator.async_request_refresh()
