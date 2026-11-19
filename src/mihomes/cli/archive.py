"""Archive CLI — data retention stats and archival operations."""

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_success
from mihomes.db import get_session

app = typer.Typer(name="archive", help="Data retention and archival")


@app.command("stats")
def stats_cmd():
    """Show data volume per table and what would be archived."""
    from mihomes.services.archive import get_stats

    with get_session() as session:
        rows = get_stats(session)

    table = Table(title="Archive Stats")
    table.add_column("Table", style="bold")
    table.add_column("Description")
    table.add_column("Active Rows", justify="right")
    table.add_column("Eligible to Archive", justify="right")
    table.add_column("Already Archived", justify="right")
    table.add_column("Retention")
    table.add_column("Cutoff Date")

    archival_available = all(r.get("archival_available", True) for r in rows)

    for r in rows:
        eligible_style = "yellow" if r["eligible_to_archive"] > 0 else "dim"
        # `already_archived` is None when the archive tables do not exist. Rendering that
        # with str() would print the literal "None", which reads as a bug rather than as
        # "cannot be answered".
        archived = r["already_archived"]
        table.add_row(
            r["table"],
            r["description"],
            str(r["active_rows"]),
            f"[{eligible_style}]{r['eligible_to_archive']}[/{eligible_style}]",
            str(archived) if archived is not None else "[dim]n/a[/dim]",
            f"{r['retention_years']} year(s)",
            str(r["cutoff_date"]),
        )

    console.print(table)
    if archival_available:
        console.print(
            "[dim]Run 'mihomes archive run' to move eligible rows to archive tables.\n"
            "Change retention with: mihomes config set retention.audit_years 3[/dim]"
        )
    else:
        # Do not advertise a command that refuses. The counts above are still useful —
        # they say how much data is past its retention window.
        console.print(
            "[yellow]Archival is currently unavailable[/yellow] — the archive tables are not "
            "created by any migration and are not tenant-aware (SPEC-002 G10). The counts "
            "above still show what is past its retention window.\n"
            "[dim]Change retention with: mihomes config set retention.audit_years 3[/dim]"
        )


@app.command("run")
def run_cmd(
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview what would be archived without making changes"),
):
    """Archive old records based on configured retention periods."""
    from mihomes.services.archive import ArchivalUnavailableError, run_archival

    if dry_run:
        console.print("[dim]Dry run — no changes will be made.[/dim]")

    try:
        with get_session() as session:
            results = run_archival(session, dry_run=dry_run)
    except ArchivalUnavailableError as e:
        # Report the reason rather than a traceback: the service raises this deliberately
        # while the archive tables are absent and not tenant-aware (SPEC-002 G10).
        console.print(f"[yellow]Archival unavailable.[/yellow]\n{e}")
        raise typer.Exit(1) from None

    total = sum(results.values())
    if total == 0:
        console.print("[green]Nothing to archive — all records are within retention window.[/green]")
        return

    action = "Would archive" if dry_run else "Archived"
    for table, count in results.items():
        if count:
            console.print(f"  {action} [bold]{count}[/bold] row(s) from [cyan]{table}[/cyan]")

    if not dry_run:
        format_success(f"{total} row(s) archived across {len([v for v in results.values() if v])} table(s)")
    else:
        console.print(f"\n[dim]{total} row(s) would be archived. Run without --dry-run to apply.[/dim]")
