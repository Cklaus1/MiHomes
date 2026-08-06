"""Alembic environment configuration — dynamic DB URL from mihomes config."""

import os
from logging.config import fileConfig

from sqlalchemy import create_engine, event, pool

from alembic import context
from mihomes.config import DB_URL
from mihomes.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables created/managed outside the ORM metadata (raw-SQL migrations only, not
# registered on Base.metadata). Autogenerate must not propose dropping them, or
# every future diff would be dirtied by phantom drop_table ops.
_UNMANAGED_TABLES = {"audit_log_archive", "ai_conversations_archive"}


def include_object(obj, name, type_, reflected, compare_to):
    """Exclude unmanaged tables (and their indexes) from autogenerate diffs."""
    if type_ == "table" and name in _UNMANAGED_TABLES:
        return False
    if type_ == "index" and getattr(obj, "table", None) is not None \
            and obj.table.name in _UNMANAGED_TABLES:
        return False
    return True


def get_url():
    # Precedence: an explicit owner-role URL for migrations, then the app URL,
    # then the hardcoded SQLite default. alembic.ini carries no `sqlalchemy.url`
    # key, so get_main_option always returns None — before the env vars were
    # honored here there was no path by which a Postgres URL could reach Alembic
    # at all (SPEC-001 §10, SPEC-002's MIGRATION_DATABASE_URL split: the app must
    # not connect as the owner, but migrations legitimately must).
    return (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or DB_URL
    )


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(get_url(), poolclass=pool.NullPool)

    # SQLite batch-mode rebuilds a table by copy → DROP → RENAME. When other
    # tables carry a FK referencing the target, that DROP fails while
    # foreign_keys is ON (mihomes.db's connect listener forces it ON on the app
    # engine). SQLite only honors `PRAGMA foreign_keys` *outside* a transaction,
    # so we set it in a connect-event listener — it fires on the raw DBAPI
    # connection before Alembic opens any transaction. Existing-row integrity is
    # preserved separately by explicit orphan-cleanup inside each migration
    # (SQLite does not re-validate rows when FK is re-enabled later).
    # All of the above is SQLite-only. Against Postgres the PRAGMA is a syntax
    # error that fires before the first migration runs, and batch mode is a
    # SQLite affordance with nothing to do there.
    is_sqlite = connectable.dialect.name == "sqlite"

    if is_sqlite:
        @event.listens_for(connectable, "connect")
        def _disable_fk_for_migration(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=is_sqlite,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
