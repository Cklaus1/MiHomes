"""W.4 · M40 — canonical parsing helpers in services/parsing.py.

`parse_money` and `parse_date` give tolerant, user-friendly parsing that raises
`ValueError` (surfaceable as a form error) rather than letting FastAPI's native
coercion emit a bare 422 or letting a `ValueError` escape as a 500.
"""

from datetime import date

import pytest

from mihomes.services.parsing import parse_date, parse_money


class TestParseMoney:
    def test_empty_is_none(self):
        assert parse_money("") is None
        assert parse_money("   ") is None
        assert parse_money(None) is None

    def test_plain_number(self):
        assert parse_money("42") == 42.0
        assert parse_money("42.50") == 42.50

    def test_strips_currency_and_commas(self):
        assert parse_money("$1,234.56") == 1234.56
        assert parse_money("  $ 1,000 ") == 1000.0

    def test_invalid_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_money("abc")

    def test_error_message_names_field(self):
        with pytest.raises(ValueError, match="Amount"):
            parse_money("xyz", field="Amount")


class TestParseDate:
    def test_empty_is_none(self):
        assert parse_date("") is None
        assert parse_date("   ") is None
        assert parse_date(None) is None

    def test_iso_date(self):
        assert parse_date("2026-07-29") == date(2026, 7, 29)

    def test_passthrough_date_object(self):
        d = date(2026, 1, 2)
        assert parse_date(d) == d

    def test_invalid_raises_valueerror(self):
        with pytest.raises(ValueError):
            parse_date("not-a-date")

    def test_error_message_names_field(self):
        with pytest.raises(ValueError, match="Due date"):
            parse_date("31/12/2026", field="Due date")
