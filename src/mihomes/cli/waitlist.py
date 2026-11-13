"""`mihomes waitlist` — see who signed up, and invite testers (waitlist mode, 2026-11-13).

The waitlist is global (no account), so these commands bind no tenant, like `jobs`.

    mihomes waitlist list
    mihomes waitlist invite someone@example.com [--days 14]

`invite` prints a sign-up link that works only for that address. Email is not live yet, so send
the link yourself. The address does not have to be on the waitlist.
"""

from __future__ import annotations

import os

import typer
from rich.table import Table

from mihomes.cli.formatters import console

app = typer.Typer(name="waitlist", help="Waitlist sign-ups and tester invites")

DEFAULT_BASE_URL = "https://mihomes.ai"


@app.command("list")
def list_signups() -> None:
    """Everyone on the waitlist, newest first."""
    from sqlalchemy import select

    from mihomes.db import get_session
    from mihomes.models.waitlist import Waitlist

    with get_session() as session:
        rows = session.execute(
            select(Waitlist).order_by(Waitlist.created_at.desc())
        ).scalars().all()
        table = Table(title=f"Waitlist — {len(rows)} sign-up(s)")
        for column in ("Joined", "Email", "Name", "Homes", "Staff", "Source"):
            table.add_column(column)
        for row in rows:
            table.add_row(
                f"{row.created_at:%Y-%m-%d}",
                row.email,
                row.name or "",
                row.num_homes or "",
                {True: "yes", False: "no"}.get(row.has_staff, ""),
                row.utm_source or row.source or "",
            )
    console.print(table)


@app.command("invite")
def invite(
    email: str = typer.Argument(..., help="The tester's email address."),
    days: int = typer.Option(14, "--days", min=1, max=90, help="How long the link works."),
    base_url: str = typer.Option(
        "", "--base-url", help="Site address. Default: MIHOMES_BASE_URL, else https://mihomes.ai."
    ),
) -> None:
    """Print a sign-up link that lets EMAIL create an account while signup is closed."""
    from mihomes.auth.signup_gate import tester_invite_link
    from mihomes.services.waitlist import normalize_email

    try:
        address = normalize_email(email)
    except ValueError:
        console.print(f"[red]Not an email address:[/red] {email}")
        raise typer.Exit(1) from None
    base = base_url or os.environ.get("MIHOMES_BASE_URL", "").strip() or DEFAULT_BASE_URL
    try:
        link = tester_invite_link(base, address, days=days)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(f"Invite for [bold]{address}[/bold] — valid {days} day(s), for that address only:")
    # Plain print: Rich would wrap a long URL across lines and break copy-paste.
    print(link)
