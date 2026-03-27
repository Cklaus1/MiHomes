"""Calendar provider protocol — defines the interface for calendar integrations."""

from datetime import datetime
from typing import Protocol


class CalendarProvider(Protocol):
    """Protocol for calendar provider integrations.

    Implementations should handle creating, updating, and querying
    calendar events from providers like Google Calendar, Apple Calendar, etc.
    """

    def create_event(
        self,
        title: str,
        start: datetime,
        end: datetime,
        *,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict:
        """Create a calendar event.

        Args:
            title: Event title.
            start: Event start datetime.
            end: Event end datetime.
            description: Optional event description.
            location: Optional event location.
            attendees: Optional list of attendee email addresses.

        Returns:
            Dict with event_id and provider-specific metadata.
        """
        ...

    def update_event(
        self,
        event_id: str,
        **kwargs,
    ) -> dict:
        """Update an existing calendar event.

        Args:
            event_id: Provider-specific event ID.
            **kwargs: Fields to update (title, start, end, description, etc.).

        Returns:
            Dict with updated event metadata.
        """
        ...

    def delete_event(self, event_id: str) -> bool:
        """Delete a calendar event.

        Args:
            event_id: Provider-specific event ID.

        Returns:
            True if deletion was successful.
        """
        ...

    def list_events(
        self,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """List calendar events in a date range.

        Args:
            start: Range start datetime.
            end: Range end datetime.

        Returns:
            List of event dicts with id, title, start, end, etc.
        """
        ...

    def sync_from_mihomes(self, events: list[dict]) -> list[dict]:
        """Sync MiHomes events to the calendar provider.

        Args:
            events: List of MiHomes event dicts to sync.

        Returns:
            List of sync result dicts with status per event.
        """
        ...
