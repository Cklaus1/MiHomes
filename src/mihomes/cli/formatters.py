"""Shared Rich formatting helpers for CLI output."""

from datetime import date
from enum import Enum

import typer
from rich.console import Console
from rich.markup import escape as _rich_escape
from rich.panel import Panel
from rich.table import Table

from mihomes.services.parsing import parse_date, parse_money

console = Console()


def esc(value) -> str:
    """Escape a user-controlled string for safe rendering as Rich markup.

    Rich interprets ``[...]`` as style tags, so a stored value containing e.g.
    ``[/]`` raises ``MarkupError`` and crashes the command (L5). Wrap any
    user-supplied text (names, titles, note bodies, addresses, free-form
    fields) in ``esc()`` before embedding it in console output or a table cell.
    ``None`` renders as an empty string.
    """
    if value is None:
        return ""
    return _rich_escape(str(value))


def cli_date(value, param_hint: str) -> date | None:
    """Parse a user-supplied date for a CLI option.

    Routes through the canonical ``parse_date`` and re-raises any ValueError as
    ``typer.BadParameter`` so the user gets a friendly usage error (exit 2)
    instead of a raw traceback (M39/R3). ``param_hint`` is the option name,
    e.g. ``"--due"``.
    """
    try:
        return parse_date(value, field=param_hint)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint=param_hint) from None


def cli_money(value, param_hint: str) -> float | None:
    """Parse a user-supplied money/number for a CLI option → ``typer.BadParameter``."""
    try:
        return parse_money(value, field=param_hint)
    except ValueError as e:
        raise typer.BadParameter(str(e), param_hint=param_hint) from None


def format_table(title: str, columns: list[tuple[str, str]], rows: list[list[str]]) -> Table:
    """Create a Rich table. columns is [(name, style), ...]."""
    table = Table(title=title, show_lines=False)
    for name, style in columns:
        table.add_column(name, style=style)
    for row in rows:
        table.add_row(*row)
    return table


def format_panel(title: str, content: dict) -> Panel:
    """Create a Rich panel from a dict of key-value pairs.

    Keys are hardcoded field labels (trusted); values and the title are
    user-controlled entity data, so both are escaped (L5) — an entity named
    ``[/]`` would otherwise raise MarkupError and crash the command.
    """
    lines = []
    for key, value in content.items():
        if value is not None:
            lines.append(f"[bold]{key}:[/bold] {esc(value)}")
    return Panel("\n".join(lines), title=esc(title), expand=False)


def format_success(message: str) -> None:
    # Escape: callers build this message from user data (entity names etc.);
    # no caller passes intentional markup, so escaping is safe-by-default (L5).
    console.print(f"[green]{esc(message)}[/green]")


def format_error(message: str) -> None:
    console.print(f"[red]Error:[/red] {esc(message)}")


def format_enum(value) -> str:
    """Display-friendly enum rendering."""
    if isinstance(value, Enum):
        return value.value.replace("-", " ").replace("_", " ").title()
    return str(value)


def severity_color(severity: str) -> str:
    """Return a Rich color string for severity levels."""
    colors = {
        "critical": "red bold",
        "high": "red",
        "medium": "yellow",
        "low": "green",
        "urgent": "red bold",
    }
    if severity is None:
        return "white"
    return colors.get(severity.lower() if isinstance(severity, str) else severity.value.lower(), "white")


def status_icon(status: str) -> str:
    """Return an icon for common statuses."""
    icons = {
        "open": "🟢",
        "closed": "⚪",
        "caretaker-mode": "🟡",
        "under-renovation": "🔨",
        "reported": "🔴",
        "resolved": "✅",
        "completed": "✅",
        "pending": "⏳",
        "in_progress": "🔄",
        "cancelled": "❌",
    }
    key = status.lower() if isinstance(status, str) else status.value.lower()
    return icons.get(key, "○")
