"""Init command — first-run setup wizard."""

import typer
from rich import print as rprint
from rich.panel import Panel

from mihomes.cli.formatters import format_success
from mihomes.config import MIHOMES_DIR, DB_DIR, is_initialized
from mihomes.db import init_db, get_session


DEMO_DB_PATH = DB_DIR / "demo.db"
DEMO_DB_URL = f"sqlite:///{DEMO_DB_PATH}"


def register_init(app: typer.Typer):
    """Register the init command on the root app."""

    @app.command("init")
    def init_cmd(
        demo: bool = typer.Option(False, "--demo", help="Launch MiHomes with isolated demo data"),
    ):
        """Initialize MiHomes — create database and directories."""
        if demo:
            _run_demo()
            return

        if is_initialized():
            rprint(f"[yellow]MiHomes is already initialized at {MIHOMES_DIR}[/yellow]")
            rprint("Run [bold]mihomes init --demo[/bold] to explore with sample data.")
            return

        rprint(f"\n[bold]Initializing MiHomes...[/bold]")
        init_db()
        format_success(f"Database created at {MIHOMES_DIR}")

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


def _run_demo():
    """Seed demo.db if needed, point the engine at it, then launch the dashboard."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from mihomes.models import Base
    import mihomes.db as db_module

    DB_DIR.mkdir(parents=True, exist_ok=True)

    # Build / reuse demo engine
    demo_engine = create_engine(DEMO_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(demo_engine)

    fresh = True
    with Session(demo_engine) as session:
        from mihomes.models.property import Property
        fresh = session.query(Property).count() == 0

    if fresh:
        rprint("[dim]Setting up demo data...[/dim]")
        with Session(demo_engine) as session:
            from mihomes.services.demo import load_demo_data
            load_demo_data(session)
            session.commit()

    # Redirect all subsequent DB calls to demo.db for this process
    db_module._engine = demo_engine
    db_module._SessionLocal = None  # force rebuild against demo engine

    # Signal demo mode so dashboard skips real integrations (e.g. Google Calendar)
    import os
    os.environ["MIHOMES_DEMO"] = "1"

    # Launch the dashboard directly
    from mihomes.cli.dashboard import app as dashboard_app
    dashboard_app(standalone_mode=False, args=[])
