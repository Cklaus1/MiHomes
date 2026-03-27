"""Vendor CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.services import vendor as vendor_svc
from mihomes.services.slug import EntityNotFoundError

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
        table.add_column("Slug", style="dim")
        for v in vendors:
            cats = ", ".join(v.service_categories) if v.service_categories else "-"
            table.add_row(str(v.id), v.company_name, v.contact_name or "-", v.phone or "-", cats, v.slug)
        console.print(table)


@app.command("show")
def show_vendor(id_or_slug: str = typer.Argument(...)):
    """Show vendor details."""
    with get_session() as session:
        try:
            vendor = vendor_svc.get_vendor(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(vendor.id),
            "Slug": vendor.slug,
            "Contact": vendor.contact_name,
            "Phone": vendor.phone,
            "Email": vendor.email,
            "Categories": ", ".join(vendor.service_categories) if vendor.service_categories else None,
            "Service Areas": ", ".join(vendor.service_areas) if vendor.service_areas else None,
            "Insurance": vendor.insurance_info,
            "Notes": vendor.notes,
            "Active": "Yes" if vendor.active else "No",
        }
        console.print(format_panel(f"Vendor: {vendor.company_name}", content))


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
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_vendor(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a vendor."""
    with get_session() as session:
        try:
            vendor = vendor_svc.get_vendor(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete vendor '{vendor.company_name}'?", abort=True)
        name = vendor_svc.delete_vendor(session, id_or_slug)
        format_success(f"Vendor '{name}' deleted")
