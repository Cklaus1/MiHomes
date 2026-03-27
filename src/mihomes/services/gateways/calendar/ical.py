"""iCal file parser — import .ics files into MiHomes calendar events."""

import re
from datetime import date, datetime
from pathlib import Path


def parse_ical_file(file_path: str | Path) -> list[dict]:
    """Parse an .ics file and return a list of event dicts.

    Returns list of {title, start, end, location, description}.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"iCal file not found: {file_path}")

    text = path.read_text(encoding="utf-8", errors="replace")
    events = []
    current_event = None

    for line in _unfold_lines(text.splitlines()):
        line = line.strip()
        if line == "BEGIN:VEVENT":
            current_event = {}
        elif line == "END:VEVENT" and current_event is not None:
            if "title" in current_event:
                events.append(current_event)
            current_event = None
        elif current_event is not None:
            key, _, value = line.partition(":")
            # Strip parameters (e.g., DTSTART;VALUE=DATE:20260701)
            key_base = key.split(";")[0]

            if key_base == "SUMMARY":
                current_event["title"] = _unescape(value)
            elif key_base == "DTSTART":
                current_event["start"] = _parse_ical_date(value)
            elif key_base == "DTEND":
                current_event["end"] = _parse_ical_date(value)
            elif key_base == "LOCATION":
                current_event["location"] = _unescape(value)
            elif key_base == "DESCRIPTION":
                current_event["description"] = _unescape(value)

    return events


def _unfold_lines(lines: list[str]) -> list[str]:
    """Handle iCal line folding (continuation lines start with space/tab)."""
    result = []
    for line in lines:
        if line.startswith((" ", "\t")) and result:
            result[-1] += line[1:]
        else:
            result.append(line)
    return result


def _parse_ical_date(value: str) -> date | datetime | None:
    """Parse iCal date/datetime formats."""
    value = value.strip().rstrip("Z")
    try:
        if len(value) == 8:  # YYYYMMDD
            return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
        elif len(value) >= 15:  # YYYYMMDDTHHmmss
            return datetime(
                int(value[:4]), int(value[4:6]), int(value[6:8]),
                int(value[9:11]), int(value[11:13]), int(value[13:15]),
            )
    except (ValueError, IndexError):
        pass
    return None


def _unescape(value: str) -> str:
    """Unescape iCal text values."""
    return value.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
