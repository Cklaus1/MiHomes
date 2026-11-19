"""Shared fixtures for the SPEC-012 chat tests.

**App role, committed rows.** Chat visibility is a security boundary, and the suite's ordinary
`session` fixture connects as `postgres`, which bypasses RLS entirely — an access test run through
it is a false green (SPEC-012 §9). So the estate is seeded and **committed** through `_pg_engine`,
then read through `app_session(...)`: a `Session` on the non-superuser `app_engine`, inside
`account_context`, with the tenant GUC bound — the same conditions a production request runs in.
Teardown deletes the seeded account (tenant tables cascade from `accounts`) and its users.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from mihomes.tenancy.connection import bind_account_guc
from mihomes.tenancy.context import account_context


@dataclass
class Estate:
    account_id: uuid.UUID
    owner: uuid.UUID          # membership ids
    admin: uuid.UUID
    staff_belle: uuid.UUID    # staff scoped to Belle only
    staff_blue: uuid.UUID     # staff scoped to Blue only
    staff_none: uuid.UUID     # staff with no property scope
    revoked: uuid.UUID        # a revoked staff membership
    belle: uuid.UUID          # property ids
    blue: uuid.UUID
    user_ids: list[uuid.UUID]


def _insert_member(conn, account_id, role, status="active") -> tuple[uuid.UUID, uuid.UUID]:
    uid, mid = uuid.uuid4(), uuid.uuid4()
    conn.execute(text("INSERT INTO users (id, google_sub, email, name) VALUES (:u, :s, :e, :n)"),
                 {"u": uid, "s": f"sub-{uid.hex[:10]}", "e": f"chat-{uid.hex[:8]}@example.com",
                  "n": f"{role.title()} {uid.hex[:4]}"})
    conn.execute(text("INSERT INTO memberships (id, account_id, user_id, role, status) "
                      "VALUES (:m, :a, :u, :r, :st)"),
                 {"m": mid, "a": account_id, "u": uid, "r": role, "st": status})
    return uid, mid


def _insert_property(conn, account_id, name) -> uuid.UUID:
    pid = uuid.uuid4()
    conn.execute(text("INSERT INTO properties (id, account_id, name, slug, property_type, status, "
                      "currency, occupied) VALUES (:p, :a, :n, :s, 'OTHER', 'OPEN', 'USD', false)"),
                 {"p": pid, "a": account_id, "n": name, "s": f"{name.lower()}-{pid.hex[:6]}"})
    return pid


def _scope(conn, account_id, membership_id, property_id):
    conn.execute(text("INSERT INTO membership_property_scopes (id, account_id, membership_id, "
                      "property_id) VALUES (:i, :a, :m, :p)"),
                 {"i": uuid.uuid4(), "a": account_id, "m": membership_id, "p": property_id})


def seed_estate(_pg_engine, account_id) -> Estate:
    with _pg_engine.begin() as conn:
        users = []
        ids = {}
        for key, role, status in (("owner", "owner", "active"), ("admin", "admin", "active"),
                                  ("staff_belle", "staff", "active"),
                                  ("staff_blue", "staff", "active"),
                                  ("staff_none", "staff", "active"),
                                  ("revoked", "staff", "revoked")):
            uid, mid = _insert_member(conn, account_id, role, status)
            users.append(uid)
            ids[key] = mid
        belle = _insert_property(conn, account_id, "Belle")
        blue = _insert_property(conn, account_id, "Blue")
        _scope(conn, account_id, ids["staff_belle"], belle)
        _scope(conn, account_id, ids["staff_blue"], blue)
        _scope(conn, account_id, ids["revoked"], belle)
    return Estate(account_id=account_id, belle=belle, blue=blue, user_ids=users, **ids)


def cleanup_estate(_pg_engine, estate: Estate) -> None:
    with _pg_engine.begin() as conn:
        conn.execute(text("DELETE FROM accounts WHERE id = :a"), {"a": estate.account_id})
        conn.execute(text("DELETE FROM users WHERE id = ANY(:u)"), {"u": estate.user_ids})


@contextmanager
def app_session(app_engine, account_id):
    """A Session on the non-superuser role, bound to `account_id` (ContextVar + RLS GUC)."""
    with account_context(account_id):
        with Session(app_engine) as session:
            bind_account_guc(session, account_id)
            yield session


def sign_in(_pg_engine, account_id, membership_id) -> str:
    """A session row for this membership's user; returns the raw cookie value."""
    from mihomes.auth.sessions import hash_session_id

    raw = f"chat-session-{uuid.uuid4().hex}"
    with _pg_engine.begin() as conn:
        user_id = conn.execute(text("SELECT user_id FROM memberships WHERE id = :m"),
                               {"m": membership_id}).scalar_one()
        conn.execute(text("INSERT INTO sessions (id, session_id_hash, user_id, current_account_id, "
                          "created_at, expires_at) VALUES (:i, :h, :u, :a, now(), "
                          "now() + interval '1 day')"),
                     {"i": uuid.uuid4(), "h": hash_session_id(raw), "u": user_id,
                      "a": account_id})
    return raw


@pytest.fixture
def app_client(_pg_engine, app_engine, monkeypatch):
    """`app_client(account_id, membership_id) -> TestClient` — the real app, the real `get_db`,
    on the **non-superuser** role. `web_client_as`/`web_client_factory` override `get_db` with a
    `postgres` connection, which bypasses RLS: an [app] criterion run through them is a false green.

    **The AI hand-off is off by default.** A group post schedules `ai_bridge.dispatch`, which
    would otherwise call the real AI provider over the network from the test suite (it did, until
    2026-10-30). Tests about the AI patch `dispatch` or `analyze_messages` themselves.
    """
    from fastapi.testclient import TestClient

    from mihomes import db
    from mihomes.auth.sessions import SESSION_COOKIE
    from mihomes.services.chat import ai_bridge
    from mihomes.web.app import create_app

    monkeypatch.setattr(ai_bridge, "dispatch", lambda *a, **k: None)
    prev = db._engine, db._SessionLocal
    db._engine, db._SessionLocal = app_engine, None
    with db.get_session() as probe:   # the guard on the guard: requests really run without bypass
        assert not probe.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles "
                                      "WHERE rolname = current_user")).scalar()
    clients: list[TestClient] = []

    def make(account_id, membership_id) -> TestClient:
        client = TestClient(create_app(), base_url="http://localhost")
        client.cookies.set(SESSION_COOKIE, sign_in(_pg_engine, account_id, membership_id))
        clients.append(client)
        return client

    try:
        yield make
    finally:
        for c in clients:
            c.close()
        db._engine, db._SessionLocal = prev


@pytest.fixture
def estate(_pg_engine, app_engine, account_a):
    e = seed_estate(_pg_engine, account_a)
    try:
        yield e
    finally:
        app_engine.dispose()  # no stamped GUC may ride a pooled connection into a later test
        cleanup_estate(_pg_engine, e)
