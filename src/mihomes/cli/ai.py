"""AI advisory CLI commands."""

from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.live import Live

from mihomes.cli.formatters import format_error, format_success
from mihomes.db import get_session
from mihomes.services.ai.provider import AIAuthError, AIProviderError

console = Console()
app = typer.Typer(name="ai", help="AI advisory commands")


@app.command("ask")
def ask_cmd(
    query: str = typer.Argument(..., help="Your question"),
    role: Optional[str] = typer.Option(None, "--role", "-r", help="Force AI role (estate_manager, maintenance, financial, vendor_strategist, compliance)"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Scope to property"),
    continue_session: bool = typer.Option(False, "--continue", "-c", help="Continue last conversation"),
):
    """Ask the AI estate advisor a question."""
    from mihomes.services.ai.orchestrator import ask

    with get_session() as session:
        try:
            with console.status("[bold blue]Thinking...", spinner="dots"):
                response = ask(
                    session, query,
                    role=role, property_slug=property,
                    continue_session=continue_session,
                )
            console.print()
            console.print(Panel(f"[dim]{response.role}[/dim]", expand=False))
            console.print(Markdown(response.text))
            console.print()
        except AIAuthError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except AIProviderError as e:
            format_error(f"AI error: {e}")
            raise typer.Exit(1)


@app.command("review")
def review_cmd(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    format: str = typer.Option("rich", "--format", help="Output format: rich, brief"),
):
    """Get proactive AI recommendations for your estate."""
    from mihomes.services.ai.orchestrator import review

    with get_session() as session:
        try:
            with console.status("[bold blue]Analyzing estate...", spinner="dots"):
                response = review(session, property_slug=property)

            if format == "brief":
                print(response.text)
                return

            console.print()
            console.print(Panel("[bold]Estate Review[/bold] — AI Recommendations", expand=True))
            console.print(Markdown(response.text))
            console.print()
        except AIAuthError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except AIProviderError as e:
            format_error(f"AI error: {e}")
            raise typer.Exit(1)


@app.command("search")
def ai_search_cmd(
    query: str = typer.Argument(..., help="Natural language search query"),
):
    """Search across estate data using natural language."""
    from mihomes.services.ai.orchestrator import ask

    with get_session() as session:
        try:
            search_query = (
                f"Search the estate data to answer: {query}\n\n"
                "Use the provided data to give a factual answer. "
                "Reference specific entities, dates, and amounts."
            )
            with console.status("[bold blue]Searching...", spinner="dots"):
                response = ask(session, search_query, role="estate_manager")

            console.print()
            console.print(Markdown(response.text))
            console.print()
        except AIAuthError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except AIProviderError as e:
            format_error(f"AI error: {e}")
            raise typer.Exit(1)


@app.command("assess-issue")
def assess_issue_cmd(
    id_or_slug: str = typer.Argument(..., help="Issue ID or slug"),
):
    """AI assessment of an issue's severity and recommended actions."""
    from mihomes.services.ai.orchestrator import ask
    from mihomes.services.issue import get_issue
    from mihomes.services.slug import EntityNotFoundError

    with get_session() as session:
        try:
            issue = get_issue(session, id_or_slug)
            query = (
                f"Assess this issue and recommend actions:\n"
                f"Title: {issue.title}\n"
                f"Property: {issue.property.name}\n"
                f"Current severity: {issue.severity.value}\n"
                f"Status: {issue.status.value}\n"
                f"Description: {issue.description or 'None'}\n\n"
                "Evaluate: Is the severity rating correct? What SPACE classification applies? "
                "What are the recommended next steps? Should we escalate?"
            )
            with console.status("[bold blue]Assessing...", spinner="dots"):
                response = ask(session, query, role="maintenance", property_slug=issue.property.slug)

            console.print()
            console.print(Panel(f"[bold]Issue Assessment: {issue.title}[/bold]", expand=False))
            console.print(Markdown(response.text))
            console.print()
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("prioritize")
def prioritize_cmd(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
):
    """AI-ranked task prioritization using SPACE framework."""
    from mihomes.services.ai.orchestrator import ask

    with get_session() as session:
        try:
            query = (
                "Analyze all open tasks and rank them by priority using the SPACE framework. "
                "For each task, explain why it has that ranking. "
                "Group into: Must Do This Week, Should Do This Month, Can Defer."
            )
            with console.status("[bold blue]Prioritizing...", spinner="dots"):
                response = ask(session, query, role="estate_manager", property_slug=property)

            console.print()
            console.print(Panel("[bold]Task Prioritization[/bold]", expand=False))
            console.print(Markdown(response.text))
            console.print()
        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("import")
def import_cmd(
    entity_type: str = typer.Argument(..., help="Entity type: vendor, task, issue"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Property for tasks/issues"),
):
    """Paste unstructured text; AI parses it into records for confirmation."""
    from mihomes.services.ai.assessors import parse_import_text
    from rich.table import Table

    console.print(f"\n[bold]AI Import: {entity_type}[/bold]")
    console.print("Paste your text below (press Enter twice when done):\n")

    lines = []
    empty_count = 0
    while True:
        try:
            line = input()
            if line == "":
                empty_count += 1
                if empty_count >= 2:
                    break
            else:
                empty_count = 0
            lines.append(line)
        except EOFError:
            break

    text = "\n".join(lines).strip()
    if not text:
        format_error("No text provided.")
        raise typer.Exit(1)

    with get_session() as session:
        try:
            with console.status("[bold blue]Parsing...", spinner="dots"):
                items = parse_import_text(session, entity_type, text, property_slug=property)

            if not items:
                console.print("[yellow]No records found in the text.[/yellow]")
                return

            # Display parsed records
            console.print(f"\n[bold]Found {len(items)} {entity_type}(s):[/bold]\n")
            for i, item in enumerate(items, 1):
                parts = [f"  {i}. "]
                for k, v in item.items():
                    if v:
                        parts.append(f"{k}={v}")
                console.print(" | ".join(parts))

            if not typer.confirm(f"\nCreate these {len(items)} {entity_type}(s)?"):
                console.print("[dim]Cancelled.[/dim]")
                return

            # Create records
            created = 0
            if entity_type == "vendor":
                from mihomes.services.vendor import create_vendor
                for item in items:
                    create_vendor(
                        session, item["company_name"],
                        contact_name=item.get("contact_name"),
                        phone=item.get("phone"),
                        email=item.get("email"),
                        service_categories=item.get("categories"),
                    )
                    created += 1
            elif entity_type == "task":
                if not property:
                    format_error("--property required for task import")
                    raise typer.Exit(1)
                from mihomes.services.task import create_task
                from mihomes.models.task import TaskPriority, RecurrenceFrequency
                for item in items:
                    prio = TaskPriority(item.get("priority", "medium"))
                    rec = RecurrenceFrequency(item.get("recurrence", "once"))
                    due = None
                    if item.get("due_date"):
                        from datetime import date
                        try:
                            due = date.fromisoformat(item["due_date"])
                        except ValueError:
                            pass
                    create_task(session, item["title"], property, priority=prio, recurrence=rec, due_date=due, description=item.get("description"))
                    created += 1
            elif entity_type == "issue":
                if not property:
                    format_error("--property required for issue import")
                    raise typer.Exit(1)
                from mihomes.services.issue import create_issue
                from mihomes.models.issue import IssueSeverity
                for item in items:
                    sev = IssueSeverity(item.get("severity", "medium"))
                    create_issue(session, item["title"], property, severity=sev, description=item.get("description"))
                    created += 1

            format_success(f"{created} {entity_type}(s) created")

        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("setup")
def setup_cmd():
    """Configure AI provider and API key."""
    from mihomes.services.config_service import set_config

    console.print("\n[bold]AI Setup[/bold]\n")

    provider = typer.prompt("AI provider", default="claude", type=str)

    if provider == "claude":
        key = typer.prompt("Anthropic API key", hide_input=True)
        env_var = "ANTHROPIC_API_KEY"
    elif provider == "openai":
        key = typer.prompt("OpenAI API key", hide_input=True)
        env_var = "OPENAI_API_KEY"
    else:
        console.print(f"[yellow]Unknown provider '{provider}'. Supported: claude, openai[/yellow]")
        raise typer.Exit(1)

    with get_session() as session:
        set_config(session, "ai.provider", provider)
        set_config(session, f"ai.{provider}_api_key", key)
        format_success(f"AI configured: provider={provider}")
        console.print(f"[dim]Tip: You can also set {env_var} environment variable instead.[/dim]")
