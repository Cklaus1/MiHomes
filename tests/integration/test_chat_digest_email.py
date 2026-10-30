"""SPEC-014 A16–A19 — the chat digest email: suppression, lifecycle headers, no message text."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import text
from typer.testing import CliRunner

from tests.integration.chat_helpers import app_session, estate
from tests.integration.chat_notify_helpers import digests, email_of, post, rooms, run

__all__ = ["estate"]  # re-exported pytest fixture (see chat_helpers)

OLD = timedelta(minutes=15)


def test_suppressed_address_skipped(app_engine, _pg_engine, estate):
    """A16 — no outbox row at enqueue for a suppressed address (the owner, in a second DM, is
    the control)."""
    from mihomes.models.membership import Membership
    from mihomes.services.chat import conversations as conv
    from mihomes.services.email.suppression import suppress

    e = estate
    r = rooms(app_engine, e)
    with app_session(app_engine, e.account_id) as s:
        dm2 = conv.get_or_create_dm(s, s.get(Membership, e.admin), e.owner).id
        suppress(s, email_of(_pg_engine, e.staff_belle), reason="unsubscribe")
        s.commit()
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "to staff", age=OLD)
    post(app_engine, _pg_engine, e, dm2, e.admin, "to owner", age=OLD)
    run(app_engine)
    assert [to for to, _ in digests(_pg_engine, e)] == [email_of(_pg_engine, e.owner)]


def test_digest_is_lifecycle_with_unsubscribe(app_engine, _pg_engine, estate, monkeypatch):
    """A17 — lifecycle class; drains with List-Unsubscribe; links use MIHOMES_BASE_URL."""
    from mihomes.services.email.outbox import drain
    from mihomes.services.email.service import EmailService
    from mihomes.tenancy.connection import bind_account_guc
    from mihomes.tenancy.context import account_context

    monkeypatch.setenv("MIHOMES_BASE_URL", "https://evo.example")
    e = estate
    r = rooms(app_engine, e)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "hello", age=OLD)
    run(app_engine)
    with _pg_engine.connect() as c:
        klass, ctx = c.execute(text("SELECT klass, context FROM email_outbox WHERE account_id = :a "
                                    "AND template = 'chat_digest'"), {"a": e.account_id}).one()
    assert klass == "lifecycle"
    assert json.loads(ctx)["chat_url"] == "https://evo.example/chat/"

    sent = []

    class Capture:
        def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
            from mihomes.services.email.provider import EmailResult

            sent.append((to, subject, {"headers": headers}))
            return EmailResult(provider_message_id="x", provider="console")

    with account_context(e.account_id):
        from sqlalchemy.orm import Session

        with Session(app_engine) as s:
            bind_account_guc(s, e.account_id)
            from datetime import UTC, datetime

            drain(s, EmailService(Capture(), session=s).provider, now=datetime.now(UTC))
            s.commit()
    assert sent, "the digest never drained"
    headers = sent[0][2].get("headers") or {}
    assert any(k.lower() == "list-unsubscribe" for k in headers)


def test_digest_contains_no_message_text(app_engine, _pg_engine, estate):
    """A18 — rendered .html and .txt carry no post body, sender name or attachment name."""
    from mihomes.services.email.render import render_template

    e = estate
    r = rooms(app_engine, e)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "SENTINEL-7f3a-body", age=OLD)
    with _pg_engine.begin() as c:
        c.execute(text("UPDATE users SET name = 'SENTINEL-sender-name' WHERE id = "
                       "(SELECT user_id FROM memberships WHERE id = :m)"), {"m": e.owner})
    run(app_engine)
    (_to, ctx), = digests(_pg_engine, e)
    rendered = render_template("chat_digest", json.loads(ctx))
    blob = json.dumps(rendered, default=str) + ctx
    assert "SENTINEL" not in blob
    assert "Direct messages" in blob


def test_job_twice_is_noop(app_engine, _pg_engine, estate, monkeypatch):
    """A19 — the CLI command runs; a second run queues nothing new."""
    from mihomes import db
    from mihomes.cli.jobs import app as jobs_app

    e = estate
    r = rooms(app_engine, e)
    post(app_engine, _pg_engine, e, r["dm"], e.owner, "cli", age=OLD)
    # As on evo-dev: no email provider configured at all. The job only enqueues, so it must
    # still succeed (it failed every account here before 2026-10-30).
    monkeypatch.delenv("EMAIL_PROVIDER", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    try:
        first = CliRunner().invoke(jobs_app, ["chat-notify"])
        second = CliRunner().invoke(jobs_app, ["chat-notify"])
    finally:
        db._engine, db._SessionLocal = prev
        app_engine.dispose()
    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    assert "0 account(s) failed" in second.output
    assert len(digests(_pg_engine, e)) == 1
