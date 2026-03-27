"""Init command — first-run setup wizard."""

import typer
from rich import print as rprint
from rich.panel import Panel

from mihomes.cli.formatters import console, format_success
from mihomes.config import MIHOMES_DIR, is_initialized
from mihomes.db import init_db, get_session


def register_init(app: typer.Typer):
    """Register the init command on the root app."""

    @app.command("init")
    def init_cmd(
        demo: bool = typer.Option(False, "--demo", help="Load sample data for exploration"),
    ):
        """Initialize MiHomes — create database and directories."""
        if is_initialized() and not demo:
            rprint(f"[yellow]MiHomes is already initialized at {MIHOMES_DIR}[/yellow]")
            rprint("Use [bold]mihomes init --demo[/bold] to reload demo data.")
            return

        rprint(f"\n[bold]Initializing MiHomes...[/bold]")
        init_db()
        format_success(f"Database created at {MIHOMES_DIR}")

        if demo:
            rprint("\n[bold]Loading demo data...[/bold]")
            with get_session() as session:
                from mihomes.services.demo import load_demo_data
                load_demo_data(session)
            format_success("Demo data loaded: 3 properties, 3 staff, 3 vendors, 6 tasks, 4 issues, budgets")

        if not demo:
            # Interactive setup
            add_prop = typer.confirm("\nWould you like to add your first property?", default=False)
            if add_prop:
                name = typer.prompt("Property name")
                address = typer.prompt("Address", default="")
                with get_session() as session:
                    from mihomes.services.property import create_property
                    prop = create_property(session, name, address=address or None)
                    format_success(f"Property '{prop.name}' created")

        rprint(Panel(
            "[bold]Quick start:[/bold]\n\n"
            "  mihomes property list      — view properties\n"
            "  mihomes task list           — view tasks\n"
            "  mihomes issue list --open   — view open issues\n"
            "  mihomes dashboard           — full estate overview\n"
            "  mihomes --help              — see all commands",
            title="Welcome to MiHomes",
            expand=False,
        ))
