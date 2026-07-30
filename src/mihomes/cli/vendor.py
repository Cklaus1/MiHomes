"""Vendor CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, esc, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.services import vendor as vendor_svc
from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

app = typer.Typer(name="vendor", help="Manage vendors and contractors")


@app.command("add")
def add_vendor(
    company_name: str = typer.Argument(..., help="Company name"),
    contact: Optional[str] = typer.Option(None, "--contact", help="Contact person name"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    email: Optional[str] = typer.Option(None, "--email"),
    category: Optional[list[str]] = typer.Option(None, "--category", "-c", help="Service category (repeatable)"),
    area: Optional[list[str]] = typer.Option(None, "--area", help="Service area (repeatable)"),
):
    """Add a vendor."""
    with get_session() as session:
        vendor = vendor_svc.create_vendor(
            session, company_name, contact_name=contact, phone=phone, email=email,
            service_categories=category, service_areas=area,
        )
        format_success(f"Vendor '{vendor.company_name}' added (slug: {vendor.slug})")


@app.command("list")
def list_vendors(
    category: Optional[str] = typer.Option(None, "--category", "-c", help="Filter by category"),
):
    """List all vendors."""
    with get_session() as session:
        vendors = vendor_svc.list_vendors(session, category=category)
        if not vendors:
            console.print("[dim]No vendors found.[/dim]")
            return
        table = Table(title="Vendors")
        table.add_column("ID", style="dim")
        table.add_column("Company", style="bold")
        table.add_column("Contact")
        table.add_column("Phone")
        table.add_column("Categories")
        table.add_column("Properties")
        table.add_column("Slug", style="dim")
        for v in vendors:
            cats = ", ".join(v.service_categories) if v.service_categories else "-"
            props = ", ".join(v.service_areas) if v.service_areas else "-"
            table.add_row(str(v.id), esc(v.company_name), esc(v.contact_name) or "-", esc(v.phone) or "-", esc(cats), esc(props), v.slug)
        console.print(table)


@app.command("show")
def show_vendor(id_or_slug: str = typer.Argument(...)):
    """Show vendor details and active work orders."""
    from mihomes.models.work_order import WorkOrder, WorkOrderStatus
    from mihomes.cli.formatters import status_icon
    with get_session() as session:
        try:
            vendor = vendor_svc.get_vendor(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        data = vendor_svc.get_vendor_ratings(session, id_or_slug)
        avg = data["averages"]
        rating_str = f"{avg['overall']:.1f}/5.0 ({avg['count']} rating(s))" if avg else "No ratings yet"
        content = {
            "ID": str(vendor.id),
            "Slug": vendor.slug,
            "Contact": vendor.contact_name,
            "Phone": vendor.phone,
            "Email": vendor.email,
            "Categories": ", ".join(vendor.service_categories) if vendor.service_categories else None,
            "Service Areas": ", ".join(vendor.service_areas) if vendor.service_areas else None,
            "Rating": rating_str,
            "Insurance": vendor.insurance_info,
            "Notes": vendor.notes,
            "Active": "Yes" if vendor.active else "No",
        }
        console.print(format_panel(f"Vendor: {vendor.company_name}", content))

        active_statuses = [
            WorkOrderStatus.DRAFT, WorkOrderStatus.ESTIMATED,
            WorkOrderStatus.APPROVED, WorkOrderStatus.ASSIGNED,
            WorkOrderStatus.IN_PROGRESS,
        ]
        work_orders = session.query(WorkOrder).filter(
            WorkOrder.vendor_id == vendor.id,
            WorkOrder.status.in_(active_statuses),
        ).order_by(WorkOrder.due_date.asc().nullslast()).all()

        if work_orders:
            table = Table(title=f"Active Work Orders ({len(work_orders)})")
            table.add_column("ID", style="dim")
            table.add_column("Title", style="bold")
            table.add_column("Property")
            table.add_column("Status")
            table.add_column("Est. Cost", justify="right")
            table.add_column("Due", style="dim")
            for wo in work_orders:
                table.add_row(
                    str(wo.id), esc(wo.title), esc(wo.property.name),
                    f"{status_icon(wo.status)} {wo.status.value.replace('_', ' ')}",
                    f"{wo.currency} {wo.estimated_cost:,.0f}" if wo.estimated_cost else "-",
                    str(wo.due_date.date()) if wo.due_date else "-",
                )
            console.print(table)
        else:
            console.print("[dim]No active work orders.[/dim]")


@app.command("edit")
def edit_vendor(
    id_or_slug: str = typer.Argument(...),
    company_name: Optional[str] = typer.Option(None, "--name"),
    contact: Optional[str] = typer.Option(None, "--contact"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    email: Optional[str] = typer.Option(None, "--email"),
):
    """Edit a vendor."""
    kwargs = {}
    if company_name is not None: kwargs["company_name"] = company_name
    if contact is not None: kwargs["contact_name"] = contact
    if phone is not None: kwargs["phone"] = phone
    if email is not None: kwargs["email"] = email
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            vendor = vendor_svc.update_vendor(session, id_or_slug, **kwargs)
            format_success(f"Vendor '{vendor.company_name}' updated")
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("rate")
def rate_vendor(
    id_or_slug: str = typer.Argument(..., help="Vendor ID or slug"),
    quality: int = typer.Option(..., "--quality", "-q", help="Quality of work (1-5)"),
    reliability: int = typer.Option(..., "--reliability", "-r", help="Reliability / on-time (1-5)"),
    cost: Optional[int] = typer.Option(None, "--cost", help="Value for money (1-5)"),
    communication: Optional[int] = typer.Option(None, "--communication", help="Communication (1-5)"),
    notes: Optional[str] = typer.Option(None, "--notes", "-n", help="Notes on the work"),
    work_order: Optional[int] = typer.Option(None, "--work-order", help="Work order ID to link"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Property this work was done at"),
):
    """Rate a vendor's work (1–5 scale)."""
    from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError
    from mihomes.models.property import Property

    with get_session() as session:
        try:
            prop_id = None
            if property:
                prop = vendor_svc.resolve_identifier(session, Property, property) if hasattr(vendor_svc, "resolve_identifier") else None
                if not prop:
                    from mihomes.services.slug import resolve_identifier
                    prop = resolve_identifier(session, Property, property)
                prop_id = prop.id

            rating = vendor_svc.rate_vendor(
                session, id_or_slug,
                quality=quality, reliability=reliability,
                cost=cost, communication=communication,
                notes=notes, work_order_id=work_order,
                property_id=prop_id,
            )
            vendor = vendor_svc.get_vendor(session, id_or_slug)
            format_success(
                f"Rated '{vendor.company_name}' — overall {rating.overall_score:.1f}/5.0 "
                f"(quality={quality}, reliability={reliability}"
                + (f", cost={cost}" if cost else "")
                + (f", communication={communication}" if communication else "")
                + ")"
            )
        except (EntityNotFoundError, ValueError) as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("ratings")
def show_ratings(
    id_or_slug: str = typer.Argument(..., help="Vendor ID or slug"),
):
    """Show all ratings and averages for a vendor."""
    from rich.panel import Panel
    from mihomes.services.slug import AmbiguousIdentifierError, EntityNotFoundError

    with get_session() as session:
        try:
            data = vendor_svc.get_vendor_ratings(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)

        vendor = data["vendor"]
        ratings = data["ratings"]
        averages = data["averages"]

        if not ratings:
            console.print(f"[dim]No ratings yet for {esc(vendor.company_name)}.[/dim]")
            return

        # Averages panel
        def _stars(score: float | None) -> str:
            # L9: cost/communication averages are None when no rating supplied
            # that dimension — show a dash rather than crashing on int(None).
            if score is None:
                return "— (not rated)"
            full = int(round(score))
            return "★" * full + "☆" * (5 - full) + f"  {score:.1f}"

        avg_lines = [
            f"Overall:       {_stars(averages['overall'])}  ({averages['count']} rating(s))",
            f"Quality:       {_stars(averages['quality'])}",
            f"Reliability:   {_stars(averages['reliability'])}",
            f"Cost/Value:    {_stars(averages['cost'])}",
            f"Communication: {_stars(averages['communication'])}",
        ]
        console.print(Panel("\n".join(avg_lines), title=f"[bold]{esc(vendor.company_name)}[/bold] — Ratings", expand=False))

        # Individual ratings table
        table = Table(title="Rating History")
        table.add_column("Date")
        table.add_column("Overall", justify="center")
        table.add_column("Quality", justify="center")
        table.add_column("Reliability", justify="center")
        table.add_column("Cost", justify="center")
        table.add_column("Comm.", justify="center")
        table.add_column("Notes", style="dim")
        for r in ratings:
            table.add_row(
                str(r.rated_date),
                f"{r.overall_score:.1f}",
                str(r.quality_score),
                str(r.reliability_score),
                str(r.cost_score) if r.cost_score is not None else "-",
                str(r.communication_score) if r.communication_score is not None else "-",
                esc(r.notes) or "-",
            )
        console.print(table)


@app.command("delete")
def delete_vendor(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a vendor."""
    with get_session() as session:
        try:
            vendor = vendor_svc.get_vendor(session, id_or_slug)
        except (AmbiguousIdentifierError, EntityNotFoundError) as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete vendor '{vendor.company_name}'?", abort=True)
        name = vendor_svc.delete_vendor(session, id_or_slug)
        format_success(f"Vendor '{name}' deleted")
