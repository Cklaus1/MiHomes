"""Issue CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, esc, format_enum, format_error, format_panel, format_success, severity_color, status_icon
from mihomes.db import get_session
from mihomes.models.issue import IssueSeverity, IssueStatus
from mihomes.services import issue as issue_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="issue", help="Track and manage issues")


@app.command("add")
def add_issue(
    title: str = typer.Argument(..., help="Issue title"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    severity: IssueSeverity = typer.Option(IssueSeverity.MEDIUM, "--severity", "-s"),
    space: Optional[str] = typer.Option(None, "--space", help="Space ID or slug"),
    description: Optional[str] = typer.Option(None, "--desc"),
    ai_suggest: bool = typer.Option(False, "--ai-suggest", "-a", help="Get AI suggestions for severity and tags"),
):
    """Report a new issue."""
    with get_session() as session:
        try:
            issue = issue_svc.create_issue(
                session, title, property, severity=severity,
                description=description, space_id_or_slug=space,
            )
            format_success(f"Issue '{issue.title}' created (slug: {issue.slug}, severity: {issue.severity.value})")

            if ai_suggest:
                from mihomes.cli.task import _apply_ai_suggestions
                _apply_ai_suggestions(session, "issue", issue.slug, title, description, issue.property.name)

        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_issues(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    severity: Optional[IssueSeverity] = typer.Option(None, "--severity", "-s"),
    open_only: bool = typer.Option(False, "--open", help="Show only open issues"),
    resolved: bool = typer.Option(False, "--resolved", help="Show only resolved issues"),
    sort: Optional[str] = typer.Option(None, "--sort", help="Sort by: severity, date"),
):
    """List issues."""
    with get_session() as session:
        try:
            issues = issue_svc.list_issues(
                session, property_id_or_slug=property, severity=severity,
                open_only=open_only, resolved_only=resolved,
            )
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not issues:
            console.print("[dim]No issues found.[/dim]")
            return

        table = Table(title="Issues")
        table.add_column("ID", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Severity")
        table.add_column("Status")
        for i in issues:
            sev_style = severity_color(i.severity.value)
            table.add_row(
                str(i.id), esc(i.title), esc(i.property.name),
                f"[{sev_style}]{format_enum(i.severity)}[/{sev_style}]",
                f"{status_icon(i.status)} {format_enum(i.status)}",
            )
        console.print(table)


@app.command("show")
def show_issue(id_or_slug: str = typer.Argument(...)):
    """Show issue details."""
    with get_session() as session:
        try:
            issue = issue_svc.get_issue(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(issue.id),
            "Slug": issue.slug,
            "Property": issue.property.name,
            "Space": issue.space.name if issue.space else None,
            "Severity": format_enum(issue.severity),
            "Status": f"{status_icon(issue.status)} {format_enum(issue.status)}",
            "Description": issue.description,
            "Resolved At": str(issue.resolved_at) if issue.resolved_at else None,
            "Resolution Notes": issue.resolution_notes,
        }
        console.print(format_panel(f"Issue: {issue.title}", content))


@app.command("edit")
def edit_issue(
    id_or_slug: str = typer.Argument(...),
    title: Optional[str] = typer.Option(None, "--title"),
    severity: Optional[IssueSeverity] = typer.Option(None, "--severity"),
    status: Optional[IssueStatus] = typer.Option(None, "--status"),
    description: Optional[str] = typer.Option(None, "--desc"),
):
    """Edit an issue."""
    kwargs = {}
    if title is not None: kwargs["title"] = title
    if severity is not None: kwargs["severity"] = severity
    if status is not None: kwargs["status"] = status
    if description is not None: kwargs["description"] = description
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            issue = issue_svc.update_issue(session, id_or_slug, **kwargs)
            format_success(f"Issue '{issue.title}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("resolve")
def resolve_issue(
    id_or_slug: str = typer.Argument(...),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Resolution notes"),
):
    """Resolve an issue."""
    with get_session() as session:
        try:
            issue = issue_svc.resolve_issue(session, id_or_slug, notes=notes)
            format_success(f"Issue '{issue.title}' resolved")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_issue(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete an issue."""
    with get_session() as session:
        try:
            issue = issue_svc.get_issue(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete issue '{issue.title}'?", abort=True)
        name = issue_svc.delete_issue(session, id_or_slug)
        format_success(f"Issue '{name}' deleted")
