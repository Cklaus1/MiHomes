"""Shared Rich formatting helpers for CLI output."""

from enum import Enum

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def format_table(title: str, columns: list[tuple[str, str]], rows: list[list[str]]) -> Table:
    """Create a Rich table. columns is [(name, style), ...]."""
    table = Table(title=title, show_lines=False)
    for name, style in columns:
        table.add_column(name, style=style)
    for row in rows:
        table.add_row(*row)
    return table


def format_panel(title: str, content: dict) -> Panel:
    """Create a Rich panel from a dict of key-value pairs."""
    lines = []
    for key, value in content.items():
        if value is not None:
            lines.append(f"[bold]{key}:[/bold] {value}")
    return Panel("\n".join(lines), title=title, expand=False)


def format_success(message: str) -> None:
    console.print(f"[green]{message}[/green]")


def format_error(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")


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
