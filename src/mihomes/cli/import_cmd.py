"""`mihomes import` — bring a pre-SPEC-002 SQLite install into a tenant account (G16).

Defaults to a **dry run**. An import is one-way, targets an empty account, and drops rows whose
parents no longer exist — so the default should show you the plan, not perform it. `--apply` is the
deliberate second step.
"""

from pathlib import Path

import typer

from mihomes.cli.formatters import console, format_error, format_success

app = typer.Typer(name="import", help="Import a pre-multitenant SQLite database")


@app.callback(invoke_without_command=True)
def import_cmd(
    ctx: typer.Context,
    source: Path = typer.Argument(..., help="Path to the old mihomes.db (SQLite)"),
    apply: bool = typer.Option(
        False, "--apply", help="Actually perform the import (default is a dry run)"
    ),
    media_root: Path = typer.Option(
        None, "--media-root", help="Directory that relative document paths are resolved against"
    ),
    storage_root: Path = typer.Option(
        None, "--storage-root", help="Where imported files are written (required if files move)"
    ),
) -> None:
    """Show what an import would do, or perform it with --apply."""
    if ctx.invoked_subcommand is not None:
        return

    from mihomes.db import get_engine
    from mihomes.services.importer import (
        FilesystemMover,
        ImportError_,
        import_sqlite,
        plan_import,
    )
    from mihomes.tenancy import require_account

    engine = get_engine()
    try:
        # The account comes from the top-level --account resolution (G13), so importing into a
        # specific tenant is `mihomes --account <slug> import ...` — one mechanism for choosing an
        # account rather than a second flag that could disagree with it.
        account_id = require_account()
    except LookupError:
        format_error("No account bound. Run `mihomes init` first, or pass --account <slug>.")
        raise typer.Exit(1) from None

    try:
        plan = plan_import(source, engine, media_root=media_root)
    except ImportError_ as e:
        format_error(str(e))
        raise typer.Exit(1) from None

    console.print(plan.render())
    console.print()

    if not apply:
        console.print(
            "[yellow]Dry run.[/yellow] Nothing was written. Re-run with [bold]--apply[/bold] "
            "to import."
        )
        if plan.missing_files:
            console.print(
                f"[dim]{len(plan.missing_files)} referenced file(s) are not on disk; their rows "
                "import with the path unchanged.[/dim]"
            )
        return

    mover = FilesystemMover(root=storage_root) if storage_root else None
    try:
        report = import_sqlite(
            source, engine, account_id, mover=mover, media_root=media_root
        )
    except ImportError_ as e:
        format_error(str(e))
        raise typer.Exit(1) from None

    console.print(report.render())
    format_success(f"{report.total_inserted:,} row(s) imported")
