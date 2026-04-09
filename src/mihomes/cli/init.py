"""Init command — first-run setup wizard."""

import typer
from rich import print as rprint
from rich.panel import Panel

from mihomes.cli.formatters import console, format_success
from mihomes.config import MIHOMES_DIR, DB_DIR, is_initialized
from mihomes.db import init_db, get_session


DEMO_DB_PATH = DB_DIR / "demo.db"
DEMO_DB_URL = f"sqlite:///{DEMO_DB_PATH}"


def register_init(app: typer.Typer):
    """Register the init command on the root app."""

    @app.command("init")
    def init_cmd(
        demo: bool = typer.Option(False, "--demo", help="Launch an isolated demo (uses demo.db, never touches your real data)"),
    ):
        """Initialize MiHomes — create database and directories."""
        if demo:
            _run_demo()
            return

        if is_initialized():
            rprint(f"[yellow]MiHomes is already initialized at {MIHOMES_DIR}[/yellow]")
            rprint("Run [bold]mihomes init --demo[/bold] to explore with sample data in an isolated database.")
            return

        rprint(f"\n[bold]Initializing MiHomes...[/bold]")
        init_db()
        format_success(f"Database created at {MIHOMES_DIR}")

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


def _run_demo():
    """Load and explore demo data in an isolated demo.db — never touches mihomes.db."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from mihomes.models import Base

    DB_DIR.mkdir(parents=True, exist_ok=True)

    fresh = not DEMO_DB_PATH.exists()
    engine = create_engine(DEMO_DB_URL, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        if fresh:
            rprint("\n[bold]Loading demo data into demo.db...[/bold]")
            from mihomes.services.demo import load_demo_data
            load_demo_data(session)
            format_success("Demo data loaded: 3 properties, 3 staff, 3 vendors, tasks, issues, budgets")
        else:
            rprint(f"[dim]Using existing demo database at {DEMO_DB_PATH}[/dim]")

    rprint(Panel(
        f"[bold]Demo database:[/bold] {DEMO_DB_PATH}\n"
        "[dim]This is completely isolated from your real data.[/dim]\n\n"
        "To explore the demo, run:\n\n"
        "  [bold]MIHOMES_DB_PATH={path} mihomes dashboard[/bold]\n"
        "  [bold]MIHOMES_DB_PATH={path} mihomes property list[/bold]\n"
        "  [bold]MIHOMES_DB_PATH={path} mihomes task list[/bold]\n\n"
        "To reset the demo:\n"
        "  [bold]del {path}[/bold]  (Windows)  or  [bold]rm {path}[/bold]  (Mac/Linux)\n"
        "  Then run [bold]mihomes init --demo[/bold] again".format(path=DEMO_DB_PATH),
        title="MiHomes Demo",
        expand=False,
    ))
