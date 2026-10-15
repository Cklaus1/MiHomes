"""Money display and new-record currency (2026-10-15).

The estate's `currency.default` used to be read by nothing: 8 templates printed a literal `$`
and every service defaulted to USD. These pin the two halves that make the setting real.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

from mihomes.services import money
from mihomes.services.money import (
    currency_symbol,
    estate_currency,
    format_money,
    resolve_currency,
)


@pytest.fixture
def estate(request):
    """Bind the per-request estate currency the way `web/deps.py` does."""
    token = estate_currency.set(request.param)
    yield request.param
    estate_currency.reset(token)


class TestFormatMoney:
    @pytest.mark.parametrize(
        "amount, currency, decimals, expected",
        [
            (1250, "USD", 0, "$1,250"),
            (1250, "GBP", 0, "£1,250"),
            (1250.5, "EUR", 2, "€1,250.50"),
            (Decimal("99.999"), "USD", 2, "$100.00"),
            (1250, "JPY", 2, "¥1,250"),  # no minor unit — decimals are dropped
            (1250, "SEK", 0, "1,250 SEK"),  # unknown code → suffix, never a wrong symbol
            (-40, "USD", 0, "-$40"),
            (0, "GBP", 0, "£0"),
        ],
    )
    def test_formats_with_the_given_currency(self, amount, currency, decimals, expected):
        assert format_money(amount, currency, decimals) == expected

    def test_none_is_an_em_dash(self):
        assert format_money(None, "USD") == "—"

    @pytest.mark.parametrize("estate", ["GBP"], indirect=True)
    def test_falls_back_to_the_estate_currency(self, estate):
        """Totals and asset values carry no currency of their own."""
        assert format_money(1250) == "£1,250"

    @pytest.mark.parametrize("estate", ["GBP"], indirect=True)
    def test_a_records_own_currency_wins_over_the_estate(self, estate):
        """A budget stored in EUR stays EUR after the estate switches to GBP."""
        assert format_money(1250, "EUR") == "€1,250"

    def test_default_with_nothing_bound_is_usd(self):
        assert format_money(5) == "$5"

    @pytest.mark.parametrize("estate", ["EUR"], indirect=True)
    def test_symbol_for_charts(self, estate):
        assert currency_symbol() == "€"
        assert currency_symbol("SEK") == "SEK "


class TestResolveCurrency:
    def test_an_explicit_currency_is_kept_and_upper_cased(self):
        with patch("mihomes.services.config_service.get_config") as get_config:
            assert resolve_currency(object(), "eur") == "EUR"
        get_config.assert_not_called()

    def test_no_currency_takes_the_estate_setting(self):
        with patch("mihomes.services.config_service.get_config", return_value="gbp"):
            assert resolve_currency(object(), None) == "GBP"

    def test_unset_setting_falls_back_to_usd(self):
        with patch("mihomes.services.config_service.get_config", return_value=None):
            assert resolve_currency(object(), None) == "USD"


def test_the_template_filter_is_registered():
    from mihomes.web.deps import templates

    assert "money" in templates.env.filters
    assert "currency_symbol" in templates.env.globals
    rendered = templates.env.from_string("{{ 1250 | money('GBP') }} {{ 9.5 | money('EUR', 2) }}")
    assert rendered.render() == "£1,250 €9.50"


def test_no_template_prints_a_literal_dollar_amount():
    """The bug this file exists for: `${{ ... }}` ignores the estate's currency."""
    import pathlib
    import re

    root = pathlib.Path(money.__file__).resolve().parents[1] / "web" / "templates"
    offenders = []
    for path in root.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if re.search(r"\$\s*\{\{|'\$'\s*\+", text):
            offenders.append(path.relative_to(root).as_posix())
    assert not offenders, f"hard-coded $ amounts: {offenders}"
