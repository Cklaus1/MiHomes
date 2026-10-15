"""Money display — the estate's currency, not a hard-coded `$` (2026-10-15).

Two sources, in order:
  1. The record's own `currency` column, when the template passes it — a budget, contract, work
     order or transaction stored in EUR stays EUR even after the estate switches to GBP. Changing
     the setting never converts an amount.
  2. The estate's `currency.default` setting, bound per request by `web/deps.py` — for totals
     across records and for models with no currency column (assets, inventory).

`estate_currency` is a ContextVar bound beside the role and scope (`authz_context`), so the
filter reads it with no database session of its own: one settings read per request, through the
request's own tenant-scoped session.
"""

from __future__ import annotations

from contextvars import ContextVar
from decimal import Decimal

estate_currency: ContextVar[str] = ContextVar("estate_currency", default="USD")

_SYMBOLS = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "CAD": "CA$",
    "AUD": "A$",
    "CHF": "CHF ",
    "JPY": "¥",
    "MXN": "MX$",
}

#: Currencies with no minor unit — showing ¥1,234.00 would invent precision.
_ZERO_DECIMAL = {"JPY"}


def resolve_currency(session, currency: str | None) -> str:
    """The currency a NEW record is stored in: the caller's, else the estate's setting.

    Services take `currency=None` and call this, so a budget or work order created from the web
    (which never passes one) lands in the estate's currency instead of a hard-coded USD.
    """
    if currency:
        return currency.upper()
    from mihomes.services.config_service import get_config

    return (get_config(session, "currency.default") or "USD").upper()


def currency_symbol(currency: str | None = None) -> str:
    """The prefix for the estate's (or a given) currency — for client-side chart labels, which
    format numbers in JavaScript and so can't use the `money` filter. An unknown code returns the
    code plus a space, matching `format_money`'s suffix style closely enough for an axis tick."""
    code = (currency or estate_currency.get() or "USD").upper()
    return _SYMBOLS.get(code, f"{code} ")


def format_money(amount, currency: str | None = None, decimals: int = 0) -> str:
    """`amount` with its currency symbol and thousands separators, e.g. `£1,250`.

    An unknown currency code falls back to a code suffix (`1,250 SEK`) rather than a wrong
    symbol. `None` renders as an em dash, matching the pages' existing empty-value convention.
    """
    if amount is None:
        return "—"
    code = (currency or estate_currency.get() or "USD").upper()
    if code in _ZERO_DECIMAL:
        decimals = 0
    value = float(amount) if isinstance(amount, Decimal) else amount
    number = f"{abs(value):,.{decimals}f}"
    sign = "-" if value < 0 else ""
    symbol = _SYMBOLS.get(code)
    if symbol is None:
        return f"{sign}{number} {code}"
    return f"{sign}{symbol}{number}"
