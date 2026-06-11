"""Helpers for parsing/validating web form input."""


def parse_money(value: str, field: str = "Value") -> float | None:
    """Parse a money/number form field tolerantly.

    Returns None for empty input. Strips ``$``, commas, and surrounding
    whitespace. Raises ``ValueError`` with a user-friendly message if the
    remaining text isn't a number, so routes can surface it instead of a 500.
    """
    if value is None or str(value).strip() == "":
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        raise ValueError(f"{field} must be a number (got “{value}”).") from None
