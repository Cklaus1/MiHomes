"""Shared test fixtures."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from mihomes.models import Base


@event.listens_for(Engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, connection_record):
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
