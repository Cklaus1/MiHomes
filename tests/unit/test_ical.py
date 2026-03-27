"""Tests for iCal file parser."""

import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

from mihomes.services.gateways.calendar.ical import parse_ical_file


SAMPLE_ICAL = """\
BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART:20260701
DTEND:20260715
SUMMARY:Beach House Summer Stay
LOCATION:123 Ocean Dr
DESCRIPTION:Family vacation at the beach house
END:VEVENT
BEGIN:VEVENT
DTSTART:20261220T140000
DTEND:20261227T120000
SUMMARY:Holiday at Mountain Lodge
END:VEVENT
BEGIN:VEVENT
DTSTART:20260315
SUMMARY:Spring Inspection
DESCRIPTION:Annual spring\\ncheckup for all systems
END:VEVENT
END:VCALENDAR
"""


class TestParseIcalFile:
    def test_parse_basic_events(self, tmp_path):
        f = tmp_path / "test.ics"
        f.write_text(SAMPLE_ICAL)
        events = parse_ical_file(f)
        assert len(events) == 3

    def test_date_only_event(self, tmp_path):
        f = tmp_path / "test.ics"
        f.write_text(SAMPLE_ICAL)
        events = parse_ical_file(f)
        ev = next(e for e in events if e["title"] == "Beach House Summer Stay")
        assert ev["start"] == date(2026, 7, 1)
        assert ev["end"] == date(2026, 7, 15)
        assert ev["location"] == "123 Ocean Dr"

    def test_datetime_event(self, tmp_path):
        f = tmp_path / "test.ics"
        f.write_text(SAMPLE_ICAL)
        events = parse_ical_file(f)
        ev = next(e for e in events if e["title"] == "Holiday at Mountain Lodge")
        assert isinstance(ev["start"], datetime)
        assert ev["start"].hour == 14

    def test_escaped_description(self, tmp_path):
        f = tmp_path / "test.ics"
        f.write_text(SAMPLE_ICAL)
        events = parse_ical_file(f)
        ev = next(e for e in events if e["title"] == "Spring Inspection")
        assert "\n" in ev["description"]

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            parse_ical_file("/nonexistent/file.ics")

    def test_empty_calendar(self, tmp_path):
        f = tmp_path / "empty.ics"
        f.write_text("BEGIN:VCALENDAR\nVERSION:2.0\nEND:VCALENDAR\n")
        events = parse_ical_file(f)
        assert events == []
