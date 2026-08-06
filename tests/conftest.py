"""Shared test fixtures."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from mihomes.models import Base


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
    # Bound to the Engine *class*, so this fires for every engine in the test
    # session — including the Postgres one SPEC-001 introduces, where PRAGMA is
    # a syntax error. Check the driver on the raw connection: there is no engine
    # in scope to ask for a dialect.
    if type(dbapi_conn).__module__.split(".")[0] != "sqlite3":
        return
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


@pytest.fixture
def engine():
    """In-memory SQLite engine with all tables created."""
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    """Database session that rolls back after each test."""
    Session = sessionmaker(bind=engine)
    sess = Session()
    yield sess
    sess.rollback()
    sess.close()
