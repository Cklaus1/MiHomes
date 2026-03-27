"""CSV import/export CLI commands."""

import sys
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success
from mihomes.db import get_session
from mihomes.services import csv_io

export_app = typer.Typer(name="export", help="Export data to CSV")
import_app = typer.Typer(name="import-csv", help="Import data from CSV")


@export_app.command("csv")
def export_csv(
    entity_type: str = typer.Argument(..., help="Entity type: property, staff, vendor, task, issue"),
    output: Optional[str] = typer.Option(None, "--output", "-o", help="Output file (default: stdout)"),
    template: bool = typer.Option(False, "--template", help="Export empty template with headers only"),
):
    """Export entities as CSV."""
    try:
        if template:
            csv_text = csv_io.generate_template(entity_type)
        else:
            with get_session() as session:
                csv_text = csv_io.export_csv(session, entity_type)

        if output:
            Path(output).write_text(csv_text)
            format_success(f"Exported to {output}")
        else:
            sys.stdout.write(csv_text)
    except ValueError as e:
        format_error(str(e))
        raise typer.Exit(1)


@import_app.command("csv")
def import_csv(
    entity_type: str = typer.Argument(..., help="Entity type: property, staff, vendor, task, issue"),
    file: str = typer.Argument(..., help="CSV file path"),
):
    """Import entities from a CSV file."""
    path = Path(file)
    if not path.exists():
        format_error(f"File not found: {file}")
        raise typer.Exit(1)

    csv_text = path.read_text()

    with get_session() as session:
        try:
            result = csv_io.import_csv(session, entity_type, csv_text)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)

    created = result.get("created", [])
    errors = result.get("errors", [])

    if created:
        format_success(f"{len(created)} {entity_type}(s) imported")

    if errors:
        console.print(f"\n[yellow]{len(errors)} error(s):[/yellow]")
        for err in errors:
            console.print(f"  Row {err['row']}: {err['error']}")
