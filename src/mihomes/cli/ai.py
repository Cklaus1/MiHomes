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
    from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

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
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
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


@app.command("budget-review")
def budget_review_cmd(
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Scope to a single property"),
):
    """AI financial analysis — variance, anomalies, and cost optimization recommendations."""
    from mihomes.services.ai.orchestrator import budget_review

    with get_session() as session:
        try:
            with console.status("[bold blue]Analyzing financials...", spinner="dots"):
                response = budget_review(session, property_slug=property)

            console.print()
            scope = f" — {property}" if property else " — All Properties"
            console.print(Panel(f"[bold]Budget Review[/bold]{scope}", expand=True))
            console.print(Markdown(response.text))
            console.print()
        except AIAuthError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except AIProviderError as e:
            format_error(f"AI error: {e}")
            raise typer.Exit(1)


@app.command("plan")
def plan_cmd(
    scenario: str = typer.Argument(..., help="Scenario to plan for (e.g. 'opening for summer')"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Property to plan for"),
):
    """Generate an AI action plan for a property scenario."""
    from mihomes.services.ai.orchestrator import ask

    with get_session() as session:
        try:
            query = (
                f"Create a detailed action plan for the following scenario: '{scenario}'.\n\n"
                "Include:\n"
                "1. All tasks that need to be completed, in order\n"
                "2. Which staff or vendors should handle each task\n"
                "3. Suggested timeline\n"
                "4. Any risks or things to watch out for\n"
                "Use SPACE framework to prioritize. Be specific and practical."
            )
            with console.status("[bold blue]Planning...", spinner="dots"):
                response = ask(session, query, role="estate_manager", property_slug=property)

            console.print()
            scope = f" — {property}" if property else ""
            console.print(Panel(f"[bold]Action Plan: {scenario}[/bold]{scope}", expand=False))
            console.print(Markdown(response.text))
            console.print()
        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("rank-resumes")
def rank_resumes_cmd(
    folder: str = typer.Argument(..., help="Path to folder containing resumes (.pdf, .txt, .md)"),
    role: str = typer.Option(..., "--role", "-r", help="Role slug matching a job description file (e.g. housekeeper)"),
    top: int = typer.Option(10, "--top", "-n", help="Number of top candidates to return"),
    save_notes: bool = typer.Option(True, "--save-notes/--no-save-notes", help="Save per-candidate markdown notes to knowledge/staff/candidates/"),
    format: str = typer.Option("rich", "--format", "-f", help="Output format: rich or markdown"),
):
    """
    AI force-ranks candidates from a folder of resumes against a job description.

    Drop resumes (.pdf, .txt, .md) into a folder, then run:

        mihomes ai rank-resumes ./resumes --role housekeeper

    Requires the job description at: knowledge/staff/job-descriptions/<role>.md
    Saves per-candidate notes to: knowledge/staff/candidates/
    """
    from pathlib import Path
    from mihomes.services import resume_ranker as ranker
    from mihomes.services.ai.provider import AIAuthError, AIProviderError
    from rich import box
    from rich.table import Table

    resume_dir = Path(folder).expanduser().resolve()
    if not resume_dir.exists():
        format_error(f"Folder not found: {resume_dir}")
        raise typer.Exit(1)

    # Load resumes
    with console.status("[dim]Loading resumes...[/dim]"):
        resumes = ranker.load_resumes(resume_dir)

    if not resumes:
        format_error(f"No .pdf, .txt, or .md files found in {resume_dir}")
        raise typer.Exit(1)

    # Report any load errors
    errors = [r for r in resumes if r.get("error")]
    readable = [r for r in resumes if not r.get("error")]
    console.print(f"\n  Loaded [bold]{len(readable)}[/bold] resume(s)", end="")
    if errors:
        console.print(f" [yellow]({len(errors)} could not be read)[/yellow]", end="")
    console.print()

    # Load job description
    try:
        jd = ranker.load_job_description(role)
    except FileNotFoundError as e:
        format_error(str(e))
        raise typer.Exit(1)

    console.print(f"  Job description: [cyan]{role}[/cyan]")
    console.print(f"  Ranking top [bold]{min(top, len(readable))}[/bold] of {len(readable)} candidate(s)\n")

    # Run AI ranking
    try:
        with console.status("[bold blue]AI is ranking candidates...[/bold blue]", spinner="dots"):
            result = ranker.rank_resumes(resumes, jd, top_n=top)
    except AIAuthError as e:
        format_error(str(e))
        raise typer.Exit(1)
    except AIProviderError as e:
        format_error(f"AI error: {e}")
        raise typer.Exit(1)

    rankings = result.get("rankings", [])
    hiring_notes = result.get("hiring_notes", "")

    if format == "markdown":
        _print_rankings_markdown(rankings, hiring_notes, role)
    else:
        _print_rankings_rich(rankings, hiring_notes)

    # Save candidate notes
    if save_notes and rankings:
        saved = []
        for r in rankings:
            try:
                path = ranker.save_candidate_notes(r, role)
                saved.append(path)
            except Exception:
                pass
        if saved:
            console.print(f"\n[dim]  Saved {len(saved)} candidate note(s) to knowledge/staff/candidates/[/dim]")


def _print_rankings_rich(rankings: list[dict], hiring_notes: str) -> None:
    from rich import box
    from rich.table import Table

    action_style = {"phone_screen": "green", "hold": "yellow", "decline": "red dim"}

    # Summary table
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", pad_edge=False)
    t.add_column("#", style="dim", justify="right", width=3)
    t.add_column("Candidate", style="bold", min_width=20)
    t.add_column("Score", justify="center", width=7)
    t.add_column("Action", width=14)
    t.add_column("Summary")

    for r in rankings:
        action = r.get("recommended_action", "hold")
        style = action_style.get(action, "")
        t.add_row(
            str(r["rank"]),
            r["candidate_name"],
            f"{r['overall_score']}/100",
            f"[{style}]{action.replace('_', ' ')}[/{style}]",
            r.get("one_line_summary", "")[:70],
        )

    console.print()
    console.print(t)

    # Detail cards for phone_screen candidates
    phone_screen = [r for r in rankings if r.get("recommended_action") == "phone_screen"]
    if phone_screen:
        console.print(f"\n[bold green]── Phone Screen Candidates ({len(phone_screen)}) ─────────────────[/bold green]\n")
        for r in phone_screen:
            scores = r.get("scores", {})
            score_line = "  ".join(
                f"{k.replace('_',' ').title()}: {v}"
                for k, v in scores.items()
            )
            console.print(f"[bold]#{r['rank']} {r['candidate_name']}[/bold]  [dim]{r['overall_score']}/100[/dim]")
            console.print(f"  [dim]{score_line}[/dim]")
            if r.get("strengths"):
                console.print(f"  [green]✓[/green] " + "  ·  ".join(r["strengths"][:3]))
            if r.get("concerns"):
                console.print(f"  [yellow]?[/yellow] " + "  ·  ".join(r["concerns"][:2]))
            if r.get("red_flags"):
                console.print(f"  [red]⚠[/red] " + "  ·  ".join(r["red_flags"]))
            console.print()

    if hiring_notes:
        console.print(f"[bold dim]── Hiring Notes ───────────────────────────────────────────[/bold dim]")
        console.print(f"[dim]{hiring_notes}[/dim]\n")


def _print_rankings_markdown(rankings: list[dict], hiring_notes: str, role: str) -> None:
    from datetime import date
    lines = [
        f"# Resume Rankings — {role.replace('-', ' ').title()}",
        f"**Date**: {date.today()}  ",
        f"**Candidates evaluated**: {len(rankings)}",
        "",
        "## Summary",
        "",
        "| Rank | Candidate | Score | Action |",
        "|------|-----------|-------|--------|",
    ]
    for r in rankings:
        action = r.get("recommended_action", "hold").replace("_", " ").title()
        lines.append(f"| #{r['rank']} | {r['candidate_name']} | {r['overall_score']}/100 | {action} |")

    lines += [""]
    for r in rankings:
        if r.get("recommended_action") != "phone_screen":
            continue
        lines += [
            f"## #{r['rank']} {r['candidate_name']} — {r['overall_score']}/100",
            "",
            f"_{r.get('one_line_summary', '')}_",
            "",
        ]
        scores = r.get("scores", {})
        if scores:
            lines += ["**Scores**", ""]
            for k, v in scores.items():
                lines.append(f"- {k.replace('_', ' ').title()}: {v}")
            lines.append("")
        if r.get("strengths"):
            lines += ["**Strengths**", ""]
            for s in r["strengths"]:
                lines.append(f"- {s}")
            lines.append("")
        if r.get("concerns"):
            lines += ["**Concerns**", ""]
            for c in r["concerns"]:
                lines.append(f"- {c}")
            lines.append("")
        if r.get("red_flags"):
            lines += ["**⚠ Red Flags**", ""]
            for rf in r["red_flags"]:
                lines.append(f"- {rf}")
            lines.append("")

    if hiring_notes:
        lines += ["## Hiring Notes", "", hiring_notes, ""]

    print("\n".join(lines))
>>>>>>> origin/main


@app.command("setup")
def setup_cmd(
    provider_arg: Optional[str] = typer.Option(None, "--provider", help="AI provider: claude, openai, nim, ollama"),
    key_arg: Optional[str] = typer.Option(None, "--key", help="API key (skips interactive prompt)"),
):
    """Configure AI provider and API key."""
    from mihomes.services.config_service import set_config

    console.print("\n[bold]AI Setup[/bold]\n")

    provider = provider_arg or typer.prompt("AI provider [claude/openai/nim/ollama]", default="nim")

    env_vars = {
        "claude": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "nim": "NVIDIA_API_KEY",
    }

    if provider not in ("claude", "openai", "nim", "ollama"):
        console.print(f"[yellow]Unknown provider '{provider}'. Supported: claude, openai, nim, ollama[/yellow]")
        raise typer.Exit(1)

    env_var = env_vars.get(provider, "")

    if provider == "ollama":
        key = ""
        console.print("[dim]Ollama runs locally — no API key needed.[/dim]")
    elif key_arg:
        key = key_arg
    else:
        console.print(f"[dim]Tip: you can also run: mihomes ai setup --key <your-key>[/dim]")
        key = input(f"{env_var}: ").strip()
        if not key:
            format_error("No key entered.")
            raise typer.Exit(1)

    with get_session() as session:
        set_config(session, "ai.provider", provider)
        if key:
            set_config(session, f"ai.{provider}_api_key", key)
        format_success(f"AI configured: provider={provider}")
        if env_var:
            console.print(f"[dim]Tip: You can also set {env_var} environment variable instead.[/dim]")
