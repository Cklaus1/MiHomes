"""Email verification test helpers (2026-11-13).

Signup no longer signs anyone in: it mails a link, and the link signs them in. Tests whose
subject is something *after* signup (onboarding, RLS, invites) still need a signed-in new user,
so they go through the real flow: capture the mail at the provider, follow the link, press
Confirm. Nothing is shortcut, so these helpers also prove the link in the email works.

Captured at `ConsoleProvider.send`, not the outbox, for `test_password_reset.py::sent`'s reason:
there is no account at signup, so the message is sent inline and never queued.
"""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

#: The verify link inside a captured message (the HTML part, so it is entity-escaped).
_LINK = re.compile(r'href="([^"]*/verify-email/[^"]+)"')


def install_mailbox(monkeypatch) -> list[dict]:
    """Route mail to the console provider and capture every message. Returns the live list."""
    from mihomes.services.email import console_provider

    monkeypatch.setenv("EMAIL_PROVIDER", "console")
    seen: list[dict] = []
    real = console_provider.ConsoleProvider.send

    def capture(self, to, subject, html_body, **kw):
        seen.append({"to": to, "subject": subject, "html": html_body, "text": kw.get("text")})
        return real(self, to, subject, html_body, **kw)

    monkeypatch.setattr(console_provider.ConsoleProvider, "send", capture)
    return seen


def verify_link(mailbox: list[dict], to: str) -> str:
    """The most recent verification link mailed to `to`. Fails loudly if none was sent."""
    for message in reversed(mailbox):
        if message["to"] == to:
            match = _LINK.search(message["html"])
            if match:
                return html.unescape(match.group(1))
    raise AssertionError(f"no verification email reached {to}; mailbox: {[m['to'] for m in mailbox]}")


def confirm_email(client, mailbox: list[dict], to: str, **kwargs):
    """Follow the emailed link as a person would: open it, then press Confirm.

    Returns the POST response (a 303 into the app on success). `kwargs` go to both requests,
    e.g. `headers=`.
    """
    parts = urlsplit(verify_link(mailbox, to))
    page = client.get(f"{parts.path}?{parts.query}" if parts.query else parts.path, **kwargs)
    assert page.status_code == 200, f"the emailed link did not open: {page.status_code}"
    assert "Confirm my email" in page.text
    next_value = parse_qs(parts.query).get("next", [""])[0]
    return client.post(
        parts.path, data={"next": next_value}, follow_redirects=False, **kwargs
    )


def mark_verified(engine_or_session, user_id) -> None:
    """Stamp a directly-created user as verified, for tests whose subject is sign-in itself."""
    from datetime import datetime, timezone

    from sqlalchemy import text

    stmt = text("UPDATE users SET email_verified_at = :now WHERE id = :id")
    params = {"now": datetime.now(timezone.utc), "id": user_id}
    if hasattr(engine_or_session, "begin") and not hasattr(engine_or_session, "commit"):
        with engine_or_session.begin() as conn:
            conn.execute(stmt, params)
    else:
        engine_or_session.execute(stmt, params)
