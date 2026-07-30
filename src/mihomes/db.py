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


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    """Enable WAL mode and foreign key enforcement on every connection."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def _active_url() -> str:
    # Resolve config paths live rather than binding them at import: a test that
    # reloads mihomes.config (logging/backup isolation) rebinds config.DB_DIR to
    # a new object, and a by-value import here would silently keep the stale one.
    if os.environ.get("MIHOMES_DEMO") == "1":
        return f"sqlite:///{config.DB_DIR / 'demo.db'}"
    return config.DB_URL


def get_engine(url: str | None = None) -> Engine:
    """Get or create the SQLAlchemy engine."""
    global _engine, _SessionLocal
    if _engine is None or url is not None:
        _engine = create_engine(url or _active_url(), echo=False)
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
    """Initialize the database: create dirs, run migrations."""
    ensure_dirs()
    engine = get_engine(url)

    from alembic.config import Config

    from alembic import command

    alembic_cfg = Config()
    alembic_cfg.set_main_option("script_location", _get_alembic_dir())
    alembic_cfg.set_main_option("sqlalchemy.url", str(engine.url))
    command.upgrade(alembic_cfg, "head")


def _get_alembic_dir() -> str:
    """Get the alembic directory path."""
    from pathlib import Path

    return str(Path(__file__).parent.parent.parent / "alembic")
