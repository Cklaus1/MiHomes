"""Tests for calendar_sync service."""

from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.services.calendar_sync import (
    _is_task_event,
    auto_sync,
    is_google_auth_available,
    pull_from_google,
    push_task_to_google,
    push_upcoming_to_google,
)


# ── _is_task_event ────────────────────────────────────────────────────────────

class TestIsTaskEvent:
    def test_maintenance_keyword(self):
        assert _is_task_event("HVAC maintenance scheduled") is True

    def test_repair_keyword(self):
        assert _is_task_event("Roof repair") is True

    def test_landscaping_keyword(self):
        assert _is_task_event("Landscaping crew visit") is True

    def test_inspection_keyword(self):
        assert _is_task_event("Annual inspection") is True

    def test_non_task_event(self):
        assert _is_task_event("Birthday dinner") is False

    def test_keyword_in_description(self):
        assert _is_task_event("Home visit", "contractor arriving at 9am") is True

    def test_plumbing_keyword(self):
        assert _is_task_event("Fix plumbing") is True

    def test_contractor_keyword(self):
        assert _is_task_event("Contractor review") is True

    def test_cleaning_keyword(self):
        assert _is_task_event("House cleaning service") is True

    def test_pool_service_keyword(self):
        assert _is_task_event("Pool service") is True

    def test_empty_event_not_task(self):
        assert _is_task_event("", "") is False

    def test_case_insensitive(self):
        assert _is_task_event("IRRIGATION SYSTEM CHECK") is True


# ── is_google_auth_available ──────────────────────────────────────────────────

class TestIsGoogleAuthAvailable:
    def test_returns_false_when_token_missing(self, tmp_path):
        with patch("mihomes.services.calendar_sync.TOKEN_FILE", tmp_path / "nonexistent.json"):
            assert is_google_auth_available() is False

    def test_returns_true_when_token_exists(self, tmp_path):
        token = tmp_path / "google_token.json"
        token.write_text('{"token": "abc"}')
        with patch("mihomes.services.calendar_sync.TOKEN_FILE", token):
            assert is_google_auth_available() is True


# ── push_task_to_google ───────────────────────────────────────────────────────

class TestPushTaskToGoogle:
    def _make_task(self, title="Pool Inspection", due_date=None, gcal_event_id=None):
        task = MagicMock()
        task.title = title
        task.due_date = due_date or date.today()
        task.gcal_event_id = gcal_event_id
        task.description = "Some description"
        return task

    def test_returns_false_when_not_authenticated(self):
        task = self._make_task()
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=False):
            result = push_task_to_google(task)
        assert result is False

    def test_returns_false_when_no_due_date(self):
        task = self._make_task(due_date=None)
        task.due_date = None
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True):
            result = push_task_to_google(task)
        assert result is False

    def test_returns_true_when_already_pushed(self):
        task = self._make_task(gcal_event_id="evt-123")
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True):
            result = push_task_to_google(task)
        assert result is True

    def test_creates_event_when_not_pushed(self):
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.create_event.return_value = {"event_id": "new-evt-456"}
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = push_task_to_google(task)
        assert result is True
        mock_provider.create_event.assert_called_once()

    def test_event_title_prefixed_with_mihomes(self):
        task = self._make_task(title="HVAC Check")
        mock_provider = MagicMock()
        mock_provider.create_event.return_value = {"event_id": "evt-789"}
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            push_task_to_google(task)
        call_kwargs = mock_provider.create_event.call_args[1]
        assert "[MiHomes]" in call_kwargs["title"]
        assert "HVAC Check" in call_kwargs["title"]

    def test_stores_event_id_on_task(self):
        task = self._make_task()
        mock_session = MagicMock()
        mock_provider = MagicMock()
        mock_provider.create_event.return_value = {"event_id": "stored-evt"}
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            push_task_to_google(task, session=mock_session)
        assert task.gcal_event_id == "stored-evt"

    def test_returns_false_on_api_error(self):
        task = self._make_task()
        mock_provider = MagicMock()
        mock_provider.create_event.side_effect = Exception("API error")
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = push_task_to_google(task)
        assert result is False


# ── push_upcoming_to_google ───────────────────────────────────────────────────

class TestPushUpcomingToGoogle:
    def test_skips_when_not_authenticated(self, session):
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=False):
            result = push_upcoming_to_google(session)
        assert result["skipped"] == "not authenticated"
        assert result["pushed"] == 0

    def test_returns_zero_when_no_tasks(self, session):
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync.get_upcoming_tasks", return_value=[]):
            result = push_upcoming_to_google(session)
        assert result["pushed"] == 0
        assert result["errors"] == []

    def test_skips_already_pushed_tasks(self, session):
        task = MagicMock()
        task.due_date = date.today()
        task.gcal_event_id = "already-pushed"
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync.get_upcoming_tasks", return_value=[task]):
            result = push_upcoming_to_google(session)
        assert result["pushed"] == 0

    def test_pushes_unpushed_tasks(self, session):
        task = MagicMock()
        task.due_date = date.today() + timedelta(days=5)
        task.gcal_event_id = None
        task.title = "Test Push Task"
        task.description = ""
        mock_provider = MagicMock()
        mock_provider.create_event.return_value = {"event_id": "new-evt"}
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync.get_upcoming_tasks", return_value=[task]), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = push_upcoming_to_google(session)
        assert result["pushed"] == 1
        assert result["errors"] == []

    def test_records_error_on_api_failure(self, session):
        task = MagicMock()
        task.due_date = date.today() + timedelta(days=3)
        task.gcal_event_id = None
        task.title = "Failing Task"
        task.description = ""
        mock_provider = MagicMock()
        mock_provider.create_event.side_effect = Exception("API down")
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync.get_upcoming_tasks", return_value=[task]), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = push_upcoming_to_google(session)
        assert result["pushed"] == 0
        assert len(result["errors"]) == 1


# ── pull_from_google ──────────────────────────────────────────────────────────

class TestPullFromGoogle:
    def test_skips_when_not_authenticated(self, session):
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=False):
            result = pull_from_google(session)
        assert result["skipped"] == "not authenticated"

    def test_returns_zero_when_no_events(self, session):
        mock_provider = MagicMock()
        mock_provider.list_events.return_value = []
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = pull_from_google(session)
        assert result["pulled"] == 0

    def test_skips_mihomes_prefixed_events(self, session):
        prop = Property(name="Test House", slug="test-house",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        mock_provider = MagicMock()
        mock_provider.list_events.return_value = [
            {"title": "[MiHomes] Pool Cleaning", "start": datetime.now(timezone.utc)},
        ]
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = pull_from_google(session)
        assert result["pulled"] == 0
        assert result.get("tasks_created", 0) == 0

    def test_skips_events_without_start(self, session):
        prop = Property(name="Test House2", slug="test-house2",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        mock_provider = MagicMock()
        mock_provider.list_events.return_value = [
            {"title": "No Start Event", "start": None},
        ]
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = pull_from_google(session)
        assert result["pulled"] == 0

    def test_handles_provider_exception(self, session):
        mock_provider = MagicMock()
        mock_provider.list_events.side_effect = Exception("Auth failed")
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = pull_from_google(session)
        assert result["pulled"] == 0
        assert len(result["errors"]) > 0

    def test_vendor_event_creates_task(self, session):
        prop = Property(name="Main Estate", slug="main-estate",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        mock_provider = MagicMock()
        mock_provider.list_events.return_value = [
            {
                # H20: event must name the property to be matched (no default).
                "title": "Pool maintenance crew at Main Estate",
                "start": datetime.now(timezone.utc),
                "end": datetime.now(timezone.utc) + timedelta(hours=2),
                "description": "",
            },
        ]
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mock_provider):
            result = pull_from_google(session)
        assert result.get("tasks_created", 0) == 1


class TestPullFromGoogleH20:
    """H20 — unmatched events must be skipped (not force-assigned to a default
    property); occupancy events must dedup on the gcal id and must not re-spawn
    turnover tasks when the occupancy window is unchanged."""

    def _mp(self, events):
        mp = MagicMock()
        mp.list_events.return_value = events
        return mp

    def test_unmatched_event_is_skipped_not_defaulted(self, session):
        # A property exists, but the event mentions neither its name in title
        # nor description → it must NOT be force-attached to that property.
        prop = Property(name="Beach House", slug="beach-house",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        # Distinct multi-day window so occupy_property would succeed (not raise)
        # if the event were wrongly matched — proving the skip, not a ValueError.
        mp = self._mp([
            {"title": "Dentist appointment", "start": datetime.now(timezone.utc),
             "end": datetime.now(timezone.utc) + timedelta(days=3), "description": "",
             "id": "evt-unmatched"},
        ])
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mp):
            result = pull_from_google(session)
        assert result["pulled"] == 0
        assert result.get("tasks_created", 0) == 0
        # Property occupancy must be untouched.
        session.refresh(prop)
        assert prop.occupied is False

    def test_unmatched_task_event_is_skipped(self, session):
        prop = Property(name="Beach House", slug="beach-house",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        # Task-type event (has 'maintenance' keyword) but no property mention.
        mp = self._mp([
            {"title": "Car maintenance", "start": datetime.now(timezone.utc),
             "end": datetime.now(timezone.utc) + timedelta(hours=1), "description": "",
             "id": "evt-carmaint"},
        ])
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mp):
            result = pull_from_google(session)
        assert result.get("tasks_created", 0) == 0

    def test_occupancy_event_no_respawn_on_repeat(self, session):
        from mihomes.models.task import Task
        from mihomes.services.template import create_template

        # Seed the turnover template so occupy_property actually spawns tasks —
        # otherwise the assertion passes vacuously.
        create_template(session, "Guest Turnover", slug="guest-turnover",
                        steps=["Strip beds", "Clean bathrooms"])

        prop = Property(name="Lake Cabin", slug="lake-cabin",
                        property_type=PropertyType.PRIMARY)
        session.add(prop)
        session.flush()
        start = datetime.now(timezone.utc)
        end = start + timedelta(days=3)
        events = [{"title": "Guests at Lake Cabin", "start": start, "end": end,
                   "description": "", "id": "evt-stay-1"}]
        mp = self._mp(events)
        with patch("mihomes.services.calendar_sync.is_google_auth_available", return_value=True), \
             patch("mihomes.services.calendar_sync._get_provider", return_value=mp):
            pull_from_google(session)
            tasks_after_first = session.query(Task).filter(
                Task.property_id == prop.id).count()
            # Pull the SAME event again — occupancy window unchanged.
            pull_from_google(session)
            tasks_after_second = session.query(Task).filter(
                Task.property_id == prop.id).count()
        assert tasks_after_second == tasks_after_first, (
            "turnover tasks re-spawned on unchanged occupancy pull"
        )


# ── auto_sync ─────────────────────────────────────────────────────────────────

class TestAutoSync:
    def test_combines_push_and_pull_results(self, session):
        with patch("mihomes.services.calendar_sync.push_upcoming_to_google",
                   return_value={"pushed": 3, "errors": []}), \
             patch("mihomes.services.calendar_sync.pull_from_google",
                   return_value={"pulled": 2, "tasks_created": 1, "errors": []}):
            result = auto_sync(session)
        assert result["pushed"] == 3
        assert result["pulled"] == 2
        assert result["tasks_created"] == 1
        assert result["errors"] == []

    def test_merges_errors_from_both(self, session):
        with patch("mihomes.services.calendar_sync.push_upcoming_to_google",
                   return_value={"pushed": 0, "errors": ["push error"]}), \
             patch("mihomes.services.calendar_sync.pull_from_google",
                   return_value={"pulled": 0, "tasks_created": 0, "errors": ["pull error"]}):
            result = auto_sync(session)
        assert "push error" in result["errors"]
        assert "pull error" in result["errors"]
