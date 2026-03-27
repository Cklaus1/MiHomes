"""WhatsApp CLI commands — bridge management, message review, group linking."""

from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from mihomes.cli.formatters import console, format_error, format_success, severity_color
from mihomes.db import get_session
from mihomes.services.gateways.whatsapp.client import WhatsAppClient, WhatsAppBridgeError

app = typer.Typer(name="whatsapp", help="WhatsApp staff gateway")


def _get_client() -> WhatsAppClient:
    return WhatsAppClient()


@app.command("status")
def status_cmd():
    """Show WhatsApp bridge connection status."""
    try:
        client = _get_client()
        status = client.get_status()
        icon = {"connected": "[green]Connected[/green]",
                "disconnected": "[red]Disconnected[/red]",
                "awaiting-qr": "[yellow]Awaiting QR scan[/yellow]",
                "reconnecting": "[yellow]Reconnecting...[/yellow]",
                "logged-out": "[red]Logged out[/red]"}.get(status["status"], status["status"])

        console.print(Panel(
            f"Status: {icon}\n"
            f"Messages in buffer: {status.get('messageCount', 0)}\n"
            f"Linked groups: {len(status.get('linkedGroups', {}))}",
            title="WhatsApp Bridge",
            expand=False,
        ))

        linked = status.get("linkedGroups", {})
        if linked:
            table = Table(title="Linked Groups")
            table.add_column("Group JID")
            table.add_column("Property", style="bold")
            for jid, slug in linked.items():
                table.add_row(jid, slug)
            console.print(table)

    except WhatsAppBridgeError as e:
        format_error(str(e))
        raise typer.Exit(1)


@app.command("setup")
def setup_cmd():
    """Start WhatsApp pairing — displays QR code."""
    console.print(Panel(
        "[bold]WhatsApp Setup[/bold]\n\n"
        "1. Start the bridge: [cyan]cd bridge && npm install && npm start[/cyan]\n"
        "2. Scan the QR code with your WhatsApp app\n"
        "3. Link groups: [cyan]mihomes whatsapp link-group <group-name> --property <slug>[/cyan]\n\n"
        "The bridge runs as a background process on port 7867.",
        title="WhatsApp Bridge Setup",
        expand=False,
    ))

    try:
        client = _get_client()
        qr_data = client.get_qr()
        if qr_data.get("qr"):
            console.print("\n[bold]QR Code available — check the bridge terminal.[/bold]")
        else:
            status = client.get_status()
            if status["status"] == "connected":
                format_success("Already connected to WhatsApp!")
            else:
                console.print(f"[yellow]Bridge status: {status['status']}[/yellow]")
    except WhatsAppBridgeError:
        console.print("[yellow]Bridge not running. Start with: cd bridge && npm install && npm start[/yellow]")


@app.command("groups")
def list_groups():
    """List all WhatsApp groups."""
    try:
        client = _get_client()
        groups = client.get_groups()
        if not groups:
            console.print("[dim]No groups found.[/dim]")
            return
        table = Table(title="WhatsApp Groups")
        table.add_column("Name", style="bold")
        table.add_column("Participants")
        table.add_column("Linked To")
        table.add_column("JID", style="dim")
        for g in groups:
            linked = g["linked"] or "-"
            table.add_row(g["name"], str(g["participants"]), linked, g["jid"])
        console.print(table)
    except WhatsAppBridgeError as e:
        format_error(str(e))
        raise typer.Exit(1)


@app.command("link-group")
def link_group(
    group_name: str = typer.Argument(..., help="WhatsApp group name (partial match)"),
    property: str = typer.Option(..., "--property", "-p", help="Property slug to link to"),
):
    """Link a WhatsApp group to a property."""
    try:
        client = _get_client()
        groups = client.get_groups()
        match = [g for g in groups if group_name.lower() in g["name"].lower()]
        if not match:
            format_error(f"No group matching '{group_name}' found")
            raise typer.Exit(1)
        if len(match) > 1:
            console.print("[yellow]Multiple matches:[/yellow]")
            for g in match:
                console.print(f"  - {g['name']} ({g['jid']})")
            format_error("Be more specific")
            raise typer.Exit(1)

        group = match[0]
        client.link_group(group["jid"], property)
        format_success(f"Group '{group['name']}' linked to property '{property}'")
    except WhatsAppBridgeError as e:
        format_error(str(e))
        raise typer.Exit(1)


@app.command("send")
def send_message(
    phone: str = typer.Argument(..., help="Phone number (with country code)"),
    message: str = typer.Argument(..., help="Message text"),
):
    """Send a WhatsApp message."""
    try:
        client = _get_client()
        client.send_message(phone, message)
        format_success(f"Message sent to {phone}")
    except WhatsAppBridgeError as e:
        format_error(str(e))
        raise typer.Exit(1)


@app.command("review")
def review_messages(
    property: Optional[str] = typer.Option(None, "--property", "-p"),
    accept: Optional[str] = typer.Option(None, "--accept", help="Comma-separated item numbers to accept"),
):
    """Review AI-extracted issues/tasks from group conversations."""
    from mihomes.services.gateways.whatsapp.review import analyze_messages
    from mihomes.services.ai.provider import AIAuthError, AIProviderError

    try:
        client = _get_client()
        messages = client.get_messages(limit=200)
    except WhatsAppBridgeError as e:
        format_error(str(e))
        raise typer.Exit(1)

    if not messages:
        console.print("[dim]No messages to review.[/dim]")
        return

    # Filter by property if specified
    if property:
        messages = [m for m in messages if m.get("propertySlug") == property]
        if not messages:
            console.print(f"[dim]No messages for property '{property}'.[/dim]")
            return

    with get_session() as session:
        try:
            with console.status("[bold blue]Analyzing conversations...", spinner="dots"):
                result = analyze_messages(session, messages, property_name=property)
        except (AIAuthError, AIProviderError) as e:
            format_error(str(e))
            raise typer.Exit(1)

    items = result.get("items", [])
    skipped = result.get("skipped", [])

    if not items:
        console.print("[green]No actionable items found.[/green]")
        return

    # Display extracted items
    table = Table(title=f"WhatsApp Review Queue ({len(items)} items)")
    table.add_column("#", style="dim")
    table.add_column("Category")
    table.add_column("Title", style="bold")
    table.add_column("Severity")
    table.add_column("Reported By")
    for i, item in enumerate(items, 1):
        sev = item.get("severity", "-")
        sev_display = f"[{severity_color(sev)}]{sev}[/{severity_color(sev)}]" if sev != "-" else "-"
        table.add_row(
            str(i), item.get("category", "-"),
            item.get("title", "-"), sev_display,
            item.get("reported_by", "-"),
        )
    console.print(table)

    if skipped:
        console.print(f"\n[dim]Skipped {len(skipped)} non-actionable messages[/dim]")

    # Auto-accept if --accept provided
    if accept:
        indices = [int(x.strip()) for x in accept.split(",")]
        _create_items(session, items, indices, property)


def _create_items(session, items: list[dict], indices: list[int], property_slug: str | None):
    """Create issues/tasks from accepted review items."""
    from mihomes.services.issue import create_issue
    from mihomes.services.task import create_task
    from mihomes.models.issue import IssueSeverity
    from mihomes.models.task import TaskPriority

    created = 0
    for idx in indices:
        if idx < 1 or idx > len(items):
            continue
        item = items[idx - 1]
        cat = item.get("category", "")
        title = item.get("title", "Unknown")

        if not property_slug:
            console.print(f"[yellow]Skipping '{title}' — no property specified[/yellow]")
            continue

        try:
            if cat == "issue":
                sev = IssueSeverity(item.get("severity", "medium"))
                create_issue(session, title, property_slug, severity=sev, description=item.get("description"))
                created += 1
            elif cat in ("task", "supply_need"):
                create_task(session, title, property_slug, description=item.get("description"))
                created += 1
        except Exception as e:
            console.print(f"[yellow]Failed to create '{title}': {e}[/yellow]")

    if created:
        format_success(f"{created} item(s) created from review")
