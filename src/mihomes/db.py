"""Database engine, session management, and initialization."""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

import mihomes.config as config
from mihomes.config import ensure_dirs

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def _is_sqlite_connection(dbapi_conn) -> bool:
    """True when the raw DBAPI connection belongs to the stdlib sqlite3 driver.

    The listener below binds to the Engine *class*, so it fires for every engine
    in the process — including a Postgres one (SPEC-001 onward). PRAGMA is a
    syntax error outside SQLite, so the dialect has to be checked on the
    connection itself; there is no engine in scope to ask.
    """
    return type(dbapi_conn).__module__.split(".")[0] == "sqlite3"


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Enable WAL mode and foreign key enforcement on every SQLite connection."""
    if not _is_sqlite_connection(dbapi_conn):
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def _active_url() -> str:
    """The database this process should use.

    **`DATABASE_URL` wins (SPEC-002 Step 13).** SPEC-002 makes the product Postgres-only, and
    the reason is stronger than RLS: `0001_pg_baseline` is Postgres-native, so a SQLite database
    built from it is not merely unenforced but **subtly broken** — its `created_at` columns carry
    a `DEFAULT now()` that SQLite has no such function for, so the first INSERT into `accounts`
    fails with `unknown function: now()`. Measured while wiring G13.

    The SQLite fallback is kept for one reason only: a pre-SPEC-002 local install whose data has
    not yet been imported (G16). It is not a supported runtime — `verify_runtime_role` does not
    even apply there, and neither RLS nor the drift guard exists.

    Resolve config paths live rather than binding them at import: a test that reloads
    `mihomes.config` (logging/backup isolation) rebinds `config.DB_DIR` to a new object, and a
    by-value import here would silently keep the stale one.
    """
    if os.environ.get("MIHOMES_DEMO") == "1":
        return f"sqlite:///{config.DB_DIR / 'demo.db'}"
    return os.environ.get("DATABASE_URL") or config.DB_URL


def get_engine(url: str | None = None) -> Engine:
    """Get or create the SQLAlchemy engine.

    `pool_pre_ping=True` for Postgres (§5, Step 9): a pooled connection can be closed by the
    server, a network blip, or PgBouncer between checkouts, and without a pre-ping the first
    query of the next request fails instead of transparently reconnecting. Not applied to
    SQLite, where there is no server to lose and the ping is pure overhead.

    **The engine is shared; the tenant is never baked into it.** The account travels on the
    ContextVar and reaches the connection through `tenancy/connection.py`'s transaction-local
    GUC. An engine or pool per tenant would be the obvious-looking alternative and is the
    thing N3 exists to prevent — see that module for what a session-scoped GUC does to a
    reused connection.
    """
    global _engine, _SessionLocal
    if _engine is None or url is not None:
        resolved = url or _active_url()
        kwargs = {"echo": False}
        if not resolved.startswith("sqlite"):
            kwargs["pool_pre_ping"] = True
        _engine = create_engine(resolved, **kwargs)
        # H3: a swapped engine must invalidate the cached session factory, or
        # get_session() keeps binding new sessions to the previous DB. cli/init.py
        # used to hand-poke this global as a workaround; the reset belongs here.
        _SessionLocal = None
    return _engine


def dispose_engine() -> None:
    """Dispose the global engine and reset the session factory.

    Restore (spec D1) must release this process's SQLite handle before the
    on-disk ``mihomes.db{,-wal,-shm}`` files are deleted, otherwise the stale
    WAL is replayed against the freshly restored file and corrupts it. The next
    ``get_engine`` call lazily recreates the engine against the new file.
    """
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def get_session_factory(engine: Engine | None = None) -> sessionmaker:
    """Get or create the session factory."""
    global _SessionLocal
    if _SessionLocal is None or engine is not None:
        _SessionLocal = sessionmaker(bind=engine or get_engine())
    return _SessionLocal


@contextmanager
def get_session(engine: Engine | None = None) -> Generator[Session, None, None]:
    """Context manager yielding a database session with auto commit/rollback."""
    factory = get_session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(url: str | None = None) -> None:
    """Initialize the database: create dirs, run migrations, ensure an account exists.

    The account bootstrap lives here rather than in the CLI callback so that **every** path
    which initialises a database gets one — the CLI, the demo seeder, and the test fixtures that
    call `init_db()` directly. Under SPEC-002 a write with no account raises `LookupError` (G8.3
    stamps `account_id` and fails closed), so a database without an account is a database nothing
    can write to.
    """
    ensure_dirs()
    engine = get_engine(url)

    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", _get_alembic_dir())
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")

    # Imported here, not at module scope: `mihomes.tenancy` imports the models, and db.py is
    # imported by them in turn during CLI startup.
    from mihomes.tenancy.bootstrap import ensure_default_account

    ensure_default_account(engine)


def _get_alembic_dir() -> str:
    """Get the alembic directory path."""
    from pathlib import Path

    return str(Path(__file__).parent.parent.parent / "alembic")
