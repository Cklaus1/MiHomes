"""Landing-app database access — Postgres only, its own engine.

Deliberately separate from `mihomes.db`, which is the single-user product's
SQLite engine with WAL pragmas and a `_SessionLocal` global. The landing app
shares the stack and nothing else (D1), and Phase 0 is Postgres-only (D3).

**Never calls `init_db()` or runs migrations** — N4. `web/server.py` does that on
startup today, which is a race the moment Fly runs more than one machine.
Migrations are a release step (D9).
"""

from __future__ import annotations

import os
from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

__all__ = ["get_landing_engine", "landing_session"]


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL") or os.environ.get("TEST_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Phase 0 is Postgres-only (SPEC-001 D3)."
        )
    if url.startswith("sqlite"):
        raise RuntimeError(
            f"Refusing a SQLite URL for the landing app ({url}). Phase 0 targets "
            "Postgres (D3) — starting on the target engine avoids a pointless "
            "migration two weeks later."
        )
    return url


@lru_cache(maxsize=1)
def get_landing_engine() -> Engine:
    # pool_pre_ping: Fly can move a machine or drop an idle connection between
    # requests, and a stale pooled connection would surface as a 500 on the
    # signup path rather than a retry.
    return create_engine(_database_url(), pool_pre_ping=True, future=True)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    return sessionmaker(bind=get_landing_engine(), expire_on_commit=False)


def landing_session() -> Session:
    """A fresh session. Callers own the transaction boundary."""
    return _session_factory()()


def reset_landing_engine() -> None:
    """Drop the cached engine — used by tests that repoint DATABASE_URL."""
    get_landing_engine.cache_clear()
    _session_factory.cache_clear()
