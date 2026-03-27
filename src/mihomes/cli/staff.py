"""Staff CLI commands."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console, format_enum, format_error, format_panel, format_success
from mihomes.db import get_session
from mihomes.models.staff import StaffRole
from mihomes.services import staff as staff_svc
from mihomes.services.slug import EntityNotFoundError

app = typer.Typer(name="staff", help="Manage household staff")


@app.command("add")
def add_staff(
    name: str = typer.Argument(..., help="Staff member name"),
    role: StaffRole = typer.Option(StaffRole.OTHER, "--role", "-r", help="Staff role"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Assign to property"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    email: Optional[str] = typer.Option(None, "--email"),
    whatsapp: Optional[str] = typer.Option(None, "--whatsapp", help="WhatsApp phone number"),
):
    """Add a staff member."""
    with get_session() as session:
        try:
            member = staff_svc.create_staff(
                session, name, role=role, phone=phone, email=email,
                whatsapp_phone=whatsapp, property_id_or_slug=property,
            )
            format_success(f"Staff '{member.name}' added (slug: {member.slug})")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("list")
def list_staff(
    role: Optional[StaffRole] = typer.Option(None, "--role", "-r"),
):
    """List all staff members."""
    with get_session() as session:
        members = staff_svc.list_staff(session, role=role)
        if not members:
            console.print("[dim]No staff found.[/dim]")
            return
        table = Table(title="Staff")
        table.add_column("ID", style="dim")
        table.add_column("Name", style="bold")
        table.add_column("Role")
        table.add_column("Phone")
        table.add_column("Properties")
        table.add_column("Slug", style="dim")
        for m in members:
            props = ", ".join(p.name for p in m.properties) or "-"
            table.add_row(str(m.id), m.name, format_enum(m.role), m.phone or "-", props, m.slug)
        console.print(table)


@app.command("show")
def show_staff(id_or_slug: str = typer.Argument(..., help="Staff ID or slug")):
    """Show staff member details."""
    with get_session() as session:
        try:
            member = staff_svc.get_staff(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        content = {
            "ID": str(member.id),
            "Slug": member.slug,
            "Role": format_enum(member.role),
            "Phone": member.phone,
            "Email": member.email,
            "WhatsApp": member.whatsapp_phone,
            "Certifications": member.certifications,
            "Properties": ", ".join(p.name for p in member.properties) or "None",
            "Active": "Yes" if member.active else "No",
        }
        console.print(format_panel(f"Staff: {member.name}", content))


@app.command("edit")
def edit_staff(
    id_or_slug: str = typer.Argument(...),
    name: Optional[str] = typer.Option(None, "--name"),
    role: Optional[StaffRole] = typer.Option(None, "--role"),
    phone: Optional[str] = typer.Option(None, "--phone"),
    email: Optional[str] = typer.Option(None, "--email"),
    whatsapp: Optional[str] = typer.Option(None, "--whatsapp"),
):
    """Edit a staff member."""
    kwargs = {}
    if name is not None: kwargs["name"] = name
    if role is not None: kwargs["role"] = role
    if phone is not None: kwargs["phone"] = phone
    if email is not None: kwargs["email"] = email
    if whatsapp is not None: kwargs["whatsapp_phone"] = whatsapp
    if not kwargs:
        format_error("No fields to update.")
        raise typer.Exit(1)
    with get_session() as session:
        try:
            member = staff_svc.update_staff(session, id_or_slug, **kwargs)
            format_success(f"Staff '{member.name}' updated")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("delete")
def delete_staff(
    id_or_slug: str = typer.Argument(...),
    force: bool = typer.Option(False, "--force", "-f"),
):
    """Delete a staff member."""
    with get_session() as session:
        try:
            member = staff_svc.get_staff(session, id_or_slug)
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)
        if not force:
            typer.confirm(f"Delete staff member '{member.name}'?", abort=True)
        name = staff_svc.delete_staff(session, id_or_slug)
        format_success(f"Staff '{name}' deleted")


@app.command("assign")
def assign_staff(
    id_or_slug: str = typer.Argument(..., help="Staff ID or slug"),
    property: str = typer.Option(..., "--property", "-p", help="Property ID or slug"),
):
    """Assign staff member to a property."""
    with get_session() as session:
        try:
            member = staff_svc.assign_to_property(session, id_or_slug, property)
            format_success(f"'{member.name}' assigned to property")
        except EntityNotFoundError as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("workload")
def staff_workload():
    """Show task counts per staff member."""
    from mihomes.models.task import Task, TaskStatus
    with get_session() as session:
        members = staff_svc.list_staff(session)
        if not members:
            console.print("[dim]No staff found.[/dim]")
            return
        table = Table(title="Staff Workload")
        table.add_column("Name", style="bold")
        table.add_column("Role")
        table.add_column("Pending", justify="right")
        table.add_column("In Progress", justify="right")
        table.add_column("Completed", justify="right")
        table.add_column("Properties")
        for m in members:
            pending = session.query(Task).filter(Task.assignee_id == m.id, Task.status == TaskStatus.PENDING).count()
            in_prog = session.query(Task).filter(Task.assignee_id == m.id, Task.status == TaskStatus.IN_PROGRESS).count()
            done = session.query(Task).filter(Task.assignee_id == m.id, Task.status == TaskStatus.COMPLETED).count()
            props = ", ".join(p.name for p in m.properties) or "-"
            table.add_row(m.name, format_enum(m.role), str(pending), str(in_prog), str(done), props)
        console.print(table)
