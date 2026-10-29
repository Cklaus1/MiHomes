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


@pytest.fixture
def estate(_pg_engine, app_engine, account_a):
    e = seed_estate(_pg_engine, account_a)
    try:
        yield e
    finally:
        app_engine.dispose()  # no stamped GUC may ride a pooled connection into a later test
        cleanup_estate(_pg_engine, e)
