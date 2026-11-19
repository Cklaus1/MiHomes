"""SPEC-014 A1 — 0028 on its own scratch database through real Alembic."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from alembic import command
from tests.integration.test_pg_baseline import _config, pytestmark, scratch_db  # noqa: F401

__all__ = ["pytestmark", "scratch_db"]

_TABLES = ("push_subscriptions", "chat_notify_prefs")


def _state(url: str) -> dict:
    engine = create_engine(url, future=True)
    try:
        with engine.connect() as c:
            return {
                "tables": {r[0] for r in c.execute(text(
                    "SELECT tablename FROM pg_tables WHERE tablename = ANY(:t)"), {"t": list(_TABLES)})},
                "policies": {r[0] for r in c.execute(text(
                    "SELECT tablename FROM pg_policies WHERE tablename = ANY(:t)"),
                    {"t": list(_TABLES)})},
                "forced": {r[0] for r in c.execute(text(
                    "SELECT relname FROM pg_class WHERE relname = ANY(:t) "
                    "AND relrowsecurity AND relforcerowsecurity"), {"t": list(_TABLES)})},
                "triggers": {r[0] for r in c.execute(text(
                    "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                    "WHERE NOT t.tgisinternal AND c.relname = ANY(:t)"), {"t": list(_TABLES)})},
                "column": c.execute(text(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_name = 'chat_participants' AND column_name = 'last_notified_seq'")).scalar(),
            }
    finally:
        engine.dispose()


def test_up_down_up(scratch_db):
    cfg = _config(scratch_db)
    command.upgrade(cfg, "head")
    assert _state(scratch_db)["tables"] == set(_TABLES)
    command.downgrade(cfg, "0027_native_chat")
    down = _state(scratch_db)
    assert down["tables"] == set() and down["column"] == 0
    command.upgrade(cfg, "head")
    assert _state(scratch_db)["column"] == 1


def test_policies_and_guards(scratch_db):
    command.upgrade(_config(scratch_db), "head")
    s = _state(scratch_db)
    assert s["policies"] == set(_TABLES)
    assert s["forced"] == set(_TABLES)
    assert s["triggers"] == set(_TABLES)   # membership_id -> memberships drift guard
