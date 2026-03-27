"""Work order CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success, status_icon
from mihomes.db import get_session
from mihomes.models.work_order import WorkOrderStatus
from mihomes.services import work_order as wo_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="workorder", help="Manage maintenance work orders")


@app.command("create")
def create_work_order(
    title: str = typer.Argument(..., help="Work order title"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
    description: Optional[str] = typer.Option(None, "--desc"),
    vendor: Optional[str] = typer.Option(None, "--vendor", "-v", help="Vendor ID or slug"),
    assignee: Optional[str] = typer.Option(None, "--assignee", help="Staff ID or slug"),
    estimated_cost: Optional[float] = typer.Option(None, "--estimate", help="Estimated cost"),
    source: Optional[str] = typer.Option(None, "--from", help="Source reference e.g. issue:42"),
    due: Optional[str] = typer.Option(None, "--due", help="Due date YYYY-MM-DD"),
):
    """Create a new work order."""
    from datetime import datetime
    source_type = None
    source_id = None
    if source:
        parts = source.split(":")
        if len(parts) == 2:
            source_type = parts[0]
            try:
                source_id = int(parts[1])
            except ValueError:
                format_error(f"Invalid source reference: {source}. Expected format: type:id")
                raise typer.Exit(1)
        else:
            format_error(f"Invalid source reference: {source}. Expected format: type:id")
            raise typer.Exit(1)
    due_date = None
    if due:
        due_date = datetime.fromisoformat(due)
    with get_session() as session:
        try:
            wo = wo_svc.create_work_order(
                session, title, property,
                description=description, vendor_id_or_slug=vendor,
                assignee_id_or_slug=assignee, estimated_cost=estimated_cost,
                source_type=source_type, source_id=source_id, due_date=due_date,
            )
            format_success(f"Work order '{wo.title}' created (slug: {wo.slug})")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_work_orders(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    status: Optional[WorkOrderStatus] = typer.Option(None, "--status", "-s"),
):
    """List work orders."""
    with get_session() as session:
        try:
            orders = wo_svc.list_work_orders(session, property_id_or_slug=property, status=status)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)

        if not orders:
            console.print("[dim]No work orders found.[/dim]")
            return

        table = Table(title="Work Orders")
        table.add_column("ID", style="dim")
        table.add_column("Title", style="bold")
        table.add_column("Property")
        table.add_column("Status")
        table.add_column("Vendor")
        table.add_column("Est. Cost", justify="right")
        table.add_column("Slug", style="dim")
        for wo in orders:
            table.add_row(
                str(wo.id), wo.title, wo.property.name,
                f"{status_icon(wo.status)} {format_enum(wo.status)}",
                wo.vendor.company_name if wo.vendor else "-",
                f"{wo.currency} {wo.estimated_cost:,.0f}" if wo.estimated_cost else "-",
                wo.slug,
            )
        console.print(table)


@app.command("show")
def show_work_order(id_or_slug: str = typer.Argument(...)):
    """Show work order details."""
    with get_session() as session:
        try:
            wo = wo_svc.get_work_order(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(wo.id),
            "Slug": wo.slug,
            "Property": wo.property.name,
            "Status": f"{status_icon(wo.status)} {format_enum(wo.status)}",
            "Vendor": wo.vendor.company_name if wo.vendor else None,
            "Assignee": wo.assignee.name if wo.assignee else None,
            "Source": f"{wo.source_type}:{wo.source_id}" if wo.source_type else None,
            "Estimated Cost": f"{wo.currency} {wo.estimated_cost:,.2f}" if wo.estimated_cost else None,
            "Actual Cost": f"{wo.currency} {wo.actual_cost:,.2f}" if wo.actual_cost else None,
            "Due Date": str(wo.due_date) if wo.due_date else None,
            "Started": str(wo.started_at) if wo.started_at else None,
            "Completed": str(wo.completed_at) if wo.completed_at else None,
            "Verified": str(wo.verified_at) if wo.verified_at else None,
            "Description": wo.description,
            "Completion Notes": wo.completion_notes,
        }
        console.print(format_panel(f"Work Order: {wo.title}", content))


@app.command("approve")
def approve_work_order(id_or_slug: str = typer.Argument(...)):
    """Approve a work order."""
    with get_session() as session:
        try:
            wo = wo_svc.approve(session, id_or_slug)
            format_success(f"Work order '{wo.title}' approved")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("complete")
def complete_work_order(
    id_or_slug: str = typer.Argument(...),
    actual_cost: Optional[float] = typer.Option(None, "--actual-cost", help="Actual cost"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n"),
):
    """Complete a work order."""
    with get_session() as session:
        try:
            wo = wo_svc.complete(session, id_or_slug, actual_cost=actual_cost, notes=notes)
            format_success(f"Work order '{wo.title}' completed")
            if wo.actual_cost:
                console.print(f"  Actual cost: {wo.currency} {wo.actual_cost:,.2f}")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("verify")
def verify_work_order(id_or_slug: str = typer.Argument(...)):
    """Verify a completed work order."""
    with get_session() as session:
        try:
            wo = wo_svc.verify(session, id_or_slug)
            format_success(f"Work order '{wo.title}' verified")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        except ValueError as e:
            format_error(str(e))
            raise typer.Exit(1)
