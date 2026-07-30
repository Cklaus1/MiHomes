"""Tolerant, user-facing parsers for form/query input.

Both helpers return ``None`` for empty input and raise ``ValueError`` with a
friendly, field-named message otherwise — so web routes can surface them as form
errors and CLI commands can wrap them in ``typer.BadParameter`` instead of
leaking a 500/traceback. This is the single source of truth for money and date
parsing across the web and CLI layers (M40/M16/M39).
"""

from __future__ import annotations

from datetime import date


def parse_money(value, field: str = "Value") -> float | None:
    """Parse a money/number field tolerantly.

    Returns ``None`` for empty input. Strips ``$``, commas, and surrounding
    whitespace. Raises ``ValueError`` with a user-friendly message if the
    remaining text isn't a number.
    """
    if value is None or str(value).strip() == "":
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError(f"{field} must be a number (got “{value}”).") from None


def parse_date(value, field: str = "Date") -> date | None:
    """Parse an ISO (YYYY-MM-DD) date field tolerantly.

    Returns ``None`` for empty input. Accepts an existing ``date`` unchanged.
    Raises ``ValueError`` with a user-friendly message on anything else.
    """
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if text == "":
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"{field} must be a valid date in YYYY-MM-DD format (got “{value}”)."
        ) from None
