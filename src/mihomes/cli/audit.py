"""Audit CLI — view change history."""

from typing import Optional

import typer
from rich.table import Table

from mihomes.cli.formatters import console
from mihomes.db import get_session
from mihomes.models.audit_log import AuditLog

app = typer.Typer(name="audit", help="View change history")


@app.command("show")
def show_audit(
    entity_type: str = typer.Argument(..., help="Entity type (e.g., property, task, issue)"),
    entity_id: int = typer.Argument(..., help="Entity ID"),
):
    """Show full change history for an entity."""
    with get_session() as session:
        entries = session.query(AuditLog).filter(
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
        ).order_by(AuditLog.timestamp.desc()).all()

        if not entries:
            console.print(f"[dim]No audit history for {entity_type} {entity_id}.[/dim]")
            return

        table = Table(title=f"Audit History: {entity_type} #{entity_id}")
        table.add_column("Time", style="dim")
        table.add_column("Action")
        table.add_column("Actor")
        table.add_column("Changes")
        for e in entries:
            changes_str = _format_changes(e.action, e.changes)
            table.add_row(
                str(e.timestamp)[:19], e.action, e.actor, changes_str,
            )
        console.print(table)


@app.command("list")
@app.command("recent")
def recent_audit(
    days: int = typer.Option(7, "--days", "-d", help="Number of days to look back"),
):
    """Show recent changes across all entities."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    with get_session() as session:
        entries = session.query(AuditLog).filter(
            AuditLog.timestamp >= cutoff,
        ).order_by(AuditLog.timestamp.desc()).limit(50).all()

        if not entries:
            console.print(f"[dim]No changes in the last {days} days.[/dim]")
            return

        table = Table(title=f"Recent Changes (last {days} days)")
        table.add_column("Time", style="dim")
        table.add_column("Entity")
        table.add_column("Action")
        table.add_column("Actor")
        table.add_column("Summary")
        for e in entries:
            summary = _format_changes(e.action, e.changes, brief=True)
            table.add_row(
                str(e.timestamp)[:19],
                f"{e.entity_type} #{e.entity_id}",
                e.action, e.actor, summary,
            )
        console.print(table)


@app.command("export")
def export_audit(
    output: Optional[str] = typer.Option(
        None, "--output", "-o", help="Write JSON here instead of stdout."
    ),
    days: Optional[int] = typer.Option(
        None, "--days", "-d", help="Only entries from the last N days."
    ),
):
    """Export this account's audit log as JSON. **Estate-only** (SPEC-005 Step 14, A34).

    The CLI half of the same gate the route enforces. A34 requires the denial to name the
    upgrade target *at both surfaces*, because an operator scripting against this command gets
    the same dead end a customer would: "denied" with nothing to do about it is indistinguishable
    from a bug.

    Exits **2** on a plan denial rather than 1: a script can tell "you need a different plan"
    from "the command failed", and only one of those is worth retrying.
    """
    import json as _json
    from datetime import date, timedelta

    from mihomes.entitlements.service import EntitlementDenied
    from mihomes.models.account import Account
    from mihomes.services.audit import export as _export
    from mihomes.tenancy.context import require_account

    start = date.today() - timedelta(days=days) if days else None

    with get_session() as session:
        account = session.get(Account, require_account())
        try:
            entries = _export(session, account=account, start_date=start)
        except EntitlementDenied as denied:
            console.print(f"[red]Audit export is not available on this plan.[/red] {denied}")
            if denied.upgrade_target:
                console.print(
                    f"Upgrade to [bold]{denied.upgrade_target}[/bold] to enable it."
                )
            raise typer.Exit(code=2) from denied

        payload = [
            {
                "timestamp": e.timestamp.isoformat(),
                "entity_type": e.entity_type,
                "entity_id": str(e.entity_id),
                "action": e.action,
                "actor": e.actor,
                "changes": e.changes,
            }
            for e in entries
        ]

    rendered = _json.dumps(payload, indent=2, default=str)
    if output:
        from pathlib import Path

        Path(output).write_text(rendered, encoding="utf-8")
        console.print(f"Exported {len(payload)} entr(ies) to {output}.")
    else:
        console.print_json(rendered)


def _format_changes(action: str, changes: dict | None, brief: bool = False) -> str:
    if not changes:
        return "-"
    if action == "create":
        if brief:
            name = changes.get("name") or changes.get("title") or changes.get("company_name") or ""
            return f"Created: {name}" if name else "Created"
        return f"Created with {len(changes)} fields"
    if action == "delete":
        if brief:
            name = changes.get("name") or changes.get("title") or changes.get("company_name") or ""
            return f"Deleted: {name}" if name else "Deleted"
        return f"Deleted ({len(changes)} fields)"
    if action == "update":
        parts = []
        for field, vals in changes.items():
            if isinstance(vals, dict) and "old" in vals and "new" in vals:
                if brief:
                    parts.append(f"{field}: {vals['old']} → {vals['new']}")
                else:
                    parts.append(f"{field}: {vals['old']} → {vals['new']}")
            else:
                parts.append(f"{field}: {vals}")
        return "; ".join(parts[:3]) + ("..." if len(parts) > 3 else "")
    return str(changes)[:100]
