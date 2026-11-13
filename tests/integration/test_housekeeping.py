"""`mihomes jobs cleanup` — retention periods enforced, nothing else touched (2026-11-10).

Run on the **app role** (`app_engine`), because a tenant-scoped delete that only works as the
superuser would pass here and remove nothing in production. Every table gets an old row that must
go and a recent one that must stay, so a cutoff on the wrong side, or a job that deletes
everything, fails.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text
from typer.testing import CliRunner

from mihomes.services.housekeeping import MESSENGER_DEFAULT_YEARS, messenger_retention


def _seed_account(conn, account_id, tag):
    """One household's rows: an old and a new of each kind, plus mail still queued."""
    user_id = conn.execute(text(
        "INSERT INTO users (id, email) VALUES (gen_random_uuid(), :e) RETURNING id"),
        {"e": f"hk-{tag}-{uuid.uuid4().hex[:6]}@example.com"}).scalar()
    membership_id = conn.execute(text(
        "INSERT INTO memberships (id, user_id, role, account_id, status) "
        "VALUES (gen_random_uuid(), :u, 'owner', :a, 'active') RETURNING id"),
        {"u": user_id, "a": account_id}).scalar()

    for label, age in (("old", "400 days"), ("new", "10 days")):
        conn.execute(text(
            "INSERT INTO chat_messages (id, account_id, channel, chat_id, message_id, direction, "
            "sender_name, text, has_media, sent_at) VALUES (gen_random_uuid(), :a, 'telegram', "
            "'c1', :m, 'in', 'Ana', :t, false, now() - CAST(:age AS interval))"),
            {"a": account_id, "m": f"{tag}-{label}", "t": label, "age": age})

    outbox = [("sent-old", "sent_at", "100 days"), ("failed-old", "failed_at", "100 days"),
              ("sent-new", "sent_at", "10 days"), ("queued-old", None, None)]
    for template, column, age in outbox:
        stamp = f", {column}" if column else ""
        value = ", now() - CAST(:age AS interval)" if column else ""
        conn.execute(text(
            "INSERT INTO email_outbox (id, account_id, to_address, template, context, klass, "
            f"attempts, next_attempt_at{stamp}) VALUES (gen_random_uuid(), :a, "
            f"'x@example.com', :t, '{{}}', 'transactional', 0, now() - interval '200 days'"
            f"{value})"),
            {"a": account_id, "t": template, "age": age})

    for template, age in (("old", "100 days"), ("new", "10 days")):
        conn.execute(text(
            "INSERT INTO email_deliveries (id, account_id, to_address, template, sent_at, provider) "
            "VALUES (gen_random_uuid(), :a, 'x@example.com', :t, now() - CAST(:age AS interval), "
            "'console')"), {"a": account_id, "t": template, "age": age})

    for label, expires in (("expired", "now() - interval '1 hour'"),
                           ("live", "now() + interval '10 minutes'")):
        conn.execute(text(
            "INSERT INTO gateway_link_tokens (id, account_id, membership_id, gateway, token_hash, "
            f"expires_at) VALUES (gen_random_uuid(), :a, :m, 'telegram', :h, {expires})"),
            {"a": account_id, "m": membership_id, "h": f"{tag}-{label}-{uuid.uuid4().hex}"})
    return user_id


def _count(conn, sql, **params):
    return conn.execute(text(sql), params).scalar()


@pytest.fixture
def seeded(_pg_engine, account_a, account_b):
    marker = uuid.uuid4().hex[:8]
    with _pg_engine.begin() as conn:
        users = [_seed_account(conn, account_a, "a"), _seed_account(conn, account_b, "b")]
        # Account B keeps its Messenger log for two years: its 400-day message must survive.
        conn.execute(text("INSERT INTO configurations (account_id, key, value) "
                          "VALUES (:a, 'retention.whatsapp_years', '2')"), {"a": account_b})
        for label, expires in (("expired", "now() - interval '1 day'"),
                               ("live", "now() + interval '1 day'")):
            conn.execute(text(
                "INSERT INTO sessions (id, session_id_hash, user_id, expires_at) "
                f"VALUES (gen_random_uuid(), :h, :u, {expires})"),
                {"h": f"hk-{marker}-{label}", "u": users[0]})
            conn.execute(text(
                "INSERT INTO password_reset_tokens (id, user_id, token_hash, expires_at) "
                f"VALUES (gen_random_uuid(), :u, :h, {expires})"),
                {"u": users[0], "h": f"hk-{marker}-{label}"})
        waitlist = [  # (name, confirmed?, age, confirmation sent?)
            ("unconfirmed-old", False, "40 days", True), ("unconfirmed-new", False, "5 days", True),
            ("confirmed-old", True, "40 days", True), ("confirmed-new", True, "5 days", True),
            # Waitlist mode sends no confirmation; such a row is interest, not abandonment.
            ("never-asked-old", False, "40 days", False)]
        for name, confirmed, age, sent in waitlist:
            conn.execute(text(
                "INSERT INTO waitlist (id, email, name, signup_ip, user_agent, confirmed_at, "
                "confirm_sent_at, created_at) VALUES (gen_random_uuid(), :e, :n, '203.0.113.9', "
                f"'Firefox', {'now()' if confirmed else 'NULL'}, "
                f"{'now() - CAST(:age AS interval)' if sent else 'NULL'}, "
                "now() - CAST(:age AS interval))"),
                {"e": f"hk-{marker}-{name}@example.com", "n": name, "age": age})
    yield {"accounts": [account_a, account_b], "users": users, "marker": marker}
    with _pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts WHERE id = ANY(:a)"), {"a": [account_a, account_b]})
        conn.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": users})
        conn.execute(text("DELETE FROM waitlist WHERE email LIKE :p"), {"p": f"hk-{marker}-%"})


def test_cleanup_enforces_each_retention_period(_pg_engine, app_engine, seeded):
    from mihomes import db
    from mihomes.cli.jobs import app as jobs_app

    a, b = seeded["accounts"]
    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    try:
        result = CliRunner().invoke(jobs_app, ["cleanup"])
    finally:
        db._engine, db._SessionLocal = prev
        app_engine.dispose()
    assert result.exit_code == 0, result.output
    assert "0 part(s) failed" in " ".join(result.output.split()), result.output  # Rich wraps

    with _pg_engine.connect() as conn:
        def texts(account):
            return sorted(conn.execute(text(
                "SELECT text FROM chat_messages WHERE account_id = :a"), {"a": account}).scalars())

        assert texts(a) == ["new"]                 # 1 year: the 400-day message went
        assert texts(b) == ["new", "old"]          # 2 years: it stayed

        for account in (a, b):
            assert sorted(conn.execute(text(
                "SELECT template FROM email_outbox WHERE account_id = :a"),
                {"a": account}).scalars()) == ["queued-old", "sent-new"]
            assert list(conn.execute(text(
                "SELECT template FROM email_deliveries WHERE account_id = :a"),
                {"a": account}).scalars()) == ["new"]
            assert _count(conn, "SELECT count(*) FROM gateway_link_tokens WHERE account_id = :a "
                          "AND expires_at > now()", a=account) == 1
            assert _count(conn, "SELECT count(*) FROM gateway_link_tokens WHERE account_id = :a",
                          a=account) == 1

        marker = seeded["marker"]
        for table, column in (("sessions", "session_id_hash"),
                              ("password_reset_tokens", "token_hash")):
            assert list(conn.execute(text(
                f"SELECT {column} FROM {table} WHERE {column} LIKE :p"),
                {"p": f"hk-{marker}-%"}).scalars()) == [f"hk-{marker}-live"], table

        rows = {name: (ip, ua) for name, ip, ua in conn.execute(text(
            "SELECT name, signup_ip, user_agent FROM waitlist WHERE email LIKE :p"),
            {"p": f"hk-{marker}-%"})}
        assert rows == {
            "unconfirmed-new": ("203.0.113.9", "Firefox"),
            "confirmed-old": (None, None),            # kept, tracking details cleared
            "confirmed-new": ("203.0.113.9", "Firefox"),
            "never-asked-old": (None, None),          # kept: never asked to confirm
        }


def test_running_twice_changes_nothing_more(_pg_engine, app_engine, seeded):
    """Idempotent, like every scheduled job: a re-run (or a retried cron) removes nothing new."""
    from mihomes import db
    from mihomes.cli.jobs import app as jobs_app

    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    try:
        CliRunner().invoke(jobs_app, ["cleanup"])
        with _pg_engine.connect() as conn:
            before = _count(conn, "SELECT count(*) FROM chat_messages WHERE account_id = ANY(:a)",
                            a=seeded["accounts"])
        second = CliRunner().invoke(jobs_app, ["cleanup"])
    finally:
        db._engine, db._SessionLocal = prev
        app_engine.dispose()
    assert second.exit_code == 0, second.output
    with _pg_engine.connect() as conn:
        assert _count(conn, "SELECT count(*) FROM chat_messages WHERE account_id = ANY(:a)",
                      a=seeded["accounts"]) == before


@pytest.mark.parametrize("raw,years", [
    (None, MESSENGER_DEFAULT_YEARS), ("2", 2), (" 3 ", 3),
    ("0", MESSENGER_DEFAULT_YEARS), ("-1", MESSENGER_DEFAULT_YEARS),
    ("forever", MESSENGER_DEFAULT_YEARS),
])
def test_messenger_retention_never_falls_below_a_year(monkeypatch, raw, years):
    """`0` or garbage must not mean "delete every message tonight"."""
    from mihomes.services import config_service

    monkeypatch.setattr(config_service, "get_config",
                        lambda session, key, default=None: default if raw is None else raw)
    assert messenger_retention(object()) == timedelta(days=365 * years)
