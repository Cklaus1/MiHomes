"""Alembic environment for the Phase 0 landing app — Postgres only, one table.

This is a **separate** migration tree from `alembic/`. SPEC-001 D1 makes the
landing app standalone ("shares the stack and nothing else") and D3 gives it a
database whose only table is `waitlist`. Replaying the single-user product's
revisions here would create all 37 of its tables — the opposite of D3 — and
would fail regardless, since those revisions are SQLite-only.

The single-user product keeps using `alembic/` on SQLite. Neither tree knows
about the other's revisions.
"""

import os
from logging.config import fileConfig

from sqlalchemy import MetaData, create_engine, pool

from alembic import context
from mihomes.models.waitlist import Waitlist

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# Scope the metadata to `waitlist` ALONE.
#
# `Waitlist` inherits the shared `Base`, whose metadata carries 37 tables. Pointing
# target_metadata at `Base.metadata` here would make autogenerate propose creating
# every single-user table in the landing database, silently violating D3. Copying
# just this one Table into a fresh MetaData is what keeps the two trees disjoint.
target_metadata = MetaData()
Waitlist.__table__.to_metadata(target_metadata)


def get_url() -> str:
    """Postgres only — no SQLite fallback.

    D3: "Phase 0 does not use SQLite — starting on the target engine avoids a
    pointless migration two weeks later." A fallback would let the migration be
    verified against the wrong engine, which is exactly what A3 exists to prevent.
    """
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or os.environ.get("TEST_DATABASE_URL")
    )
    if not url:
        raise RuntimeError(
            "No database URL. Set DATABASE_URL (or MIGRATION_DATABASE_URL / "
            "TEST_DATABASE_URL) to a Postgres DSN. Phase 0 is Postgres-only (D3)."
        )
    if url.startswith("sqlite"):
        raise RuntimeError(
            f"Refusing to run the landing migrations against SQLite ({url}). "
            "Phase 0 targets Postgres (SPEC-001 D3)."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
