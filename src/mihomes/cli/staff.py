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


@app.command("schedule")
def staff_schedule(
    id_or_slug: Optional[str] = typer.Argument(None, help="Staff ID or slug (optional)"),
    property: Optional[str] = typer.Option(None, "--property", "-p", help="Filter by property"),
):
    """Show assigned tasks for staff, grouped by person."""
    from mihomes.models.task import Task, TaskStatus
    from mihomes.models.property import Property
    from mihomes.cli.formatters import severity_color as priority_color
    with get_session() as session:
        members = staff_svc.list_staff(session)
        if id_or_slug:
            try:
                members = [staff_svc.get_staff(session, id_or_slug)]
            except Exception as e:
                format_error(str(e))
                raise typer.Exit(1)

        if property:
            prop_obj = session.query(Property).filter(
                (Property.slug == property) | (Property.id == property)
            ).first()
            if not prop_obj:
                format_error(f"Property '{property}' not found")
                raise typer.Exit(1)
            members = [m for m in members if any(p.id == prop_obj.id for p in m.properties)]

        if not members:
            console.print("[dim]No staff found for that property.[/dim]")
            return

        any_tasks = False
        for member in members:
            query = session.query(Task).filter(
                Task.assignee_id == member.id,
                Task.status.in_([TaskStatus.PENDING, TaskStatus.IN_PROGRESS]),
            )
            if property and prop_obj:
                query = query.filter(Task.property_id == prop_obj.id)
            tasks = query.order_by(Task.due_date.asc().nullslast()).all()

            if not tasks:
                continue

            any_tasks = True
            table = Table(title=f"{member.name} ({member.role.value.replace('-', ' ').title()})")
            table.add_column("Due", style="dim")
            table.add_column("Task", style="bold")
            table.add_column("Priority")
            table.add_column("Status")
            table.add_column("Property")

            for t in tasks:
                due = t.due_date.isoformat() if t.due_date else "-"
                if t.due_date and t.due_date.isoformat() < __import__("datetime").date.today().isoformat():
                    due = f"[red]{due} (overdue)[/red]"
                pri = t.priority.value if t.priority else "-"
                pri_display = f"[{priority_color(pri)}]{pri}[/{priority_color(pri)}]"
                status = "⏳ Pending" if t.status == TaskStatus.PENDING else "🔄 In Progress"
                prop_name = t.property.name if t.property else "-"
                table.add_row(due, t.title, pri_display, status, prop_name)

            console.print(table)

        if not any_tasks:
            console.print("[dim]No assigned tasks found.[/dim]")


@app.command("pto")
def pto_balance(
    id_or_slug: str = typer.Argument(..., help="Staff ID or slug"),
):
    """Show PTO history and days used for a staff member."""
    from mihomes.services.staff_pto import get_pto_balance
    from mihomes.models.staff_pto import PTOStatus
    with get_session() as session:
        try:
            data = get_pto_balance(session, id_or_slug)
        except Exception as e:
            format_error(str(e))
            raise typer.Exit(1)

        staff = data["staff"]
        console.print(f"\n[bold]{staff.name}[/bold] — PTO {data['year']}")
        console.print(f"  Approved: [green]{data['approved_days']} day(s)[/green]")
        console.print(f"  Pending:  [yellow]{data['pending_days']} day(s)[/yellow]\n")

        if not data["requests"]:
            console.print("[dim]No PTO requests on record.[/dim]")
            return

        table = Table(title="PTO Requests")
        table.add_column("ID", style="dim")
        table.add_column("Dates")
        table.add_column("Status")
        table.add_column("Decided By")
        table.add_column("Warning", style="dim")
        for req in data["requests"]:
            status_color = {"approved": "green", "denied": "red", "pending": "yellow"}.get(req.status.value, "white")
            table.add_row(
                str(req.id),
                ", ".join(req.dates) if req.dates else "-",
                f"[{status_color}]{req.status.value}[/{status_color}]",
                req.decided_by or "-",
                req.coverage_warning or "-",
            )
        console.print(table)


@app.command("pto-requests")
def pto_requests(
    status: Optional[str] = typer.Option(None, "--status", "-s", help="Filter: pending, approved, denied"),
):
    """List all PTO requests across all staff."""
    from mihomes.services.staff_pto import list_pto_requests
    from mihomes.models.staff_pto import PTOStatus
    with get_session() as session:
        status_filter = PTOStatus(status) if status else None
        requests = list_pto_requests(session, status=status_filter)
        if not requests:
            console.print("[dim]No PTO requests found.[/dim]")
            return
        table = Table(title="PTO Requests")
        table.add_column("ID", style="dim")
        table.add_column("Staff", style="bold")
        table.add_column("Dates")
        table.add_column("Status")
        table.add_column("Coverage Warning", style="dim")
        for req in requests:
            status_color = {"approved": "green", "denied": "red", "pending": "yellow"}.get(req.status.value, "white")
            table.add_row(
                str(req.id),
                req.staff.name if req.staff else "-",
                ", ".join(req.dates) if req.dates else "-",
                f"[{status_color}]{req.status.value}[/{status_color}]",
                req.coverage_warning or "-",
            )
        console.print(table)


@app.command("pto-approve")
def pto_approve(
    request_id: int = typer.Argument(..., help="PTO request ID"),
):
    """Approve a PTO request."""
    from mihomes.services.staff_pto import approve_pto, notify_staff
    with get_session() as session:
        try:
            req = approve_pto(session, request_id, decided_by="admin")
            notify_staff(session, req)
            format_success(f"PTO approved for {req.staff.name} — {', '.join(req.dates)}")
        except Exception as e:
            format_error(str(e))
            raise typer.Exit(1)


@app.command("pto-deny")
def pto_deny(
    request_id: int = typer.Argument(..., help="PTO request ID"),
    reason: Optional[str] = typer.Option(None, "--reason", "-r"),
):
    """Deny a PTO request."""
    from mihomes.services.staff_pto import deny_pto, notify_staff
    with get_session() as session:
        try:
            req = deny_pto(session, request_id, decided_by="admin", reason=reason)
            notify_staff(session, req)
            format_success(f"PTO denied for {req.staff.name} — {', '.join(req.dates)}")
        except Exception as e:
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
