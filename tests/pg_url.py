"""Scratch-database URLs derived from `TEST_DATABASE_URL`, password included.

Migration tests each create their own throwaway database on the same server as the main
suite. Hardcoding `postgres@localhost` worked only against a passwordless local Postgres;
CI's service requires a password, so every one of those tests failed to connect there.

`render_as_string(hide_password=False)`, never `str(url)`: SQLAlchemy masks the password
as `***` in `str()`, which fails the same way (see `mihomes.db`).
"""

import os

from sqlalchemy.engine import make_url


def pg_url(database: str) -> str:
    """`TEST_DATABASE_URL` pointed at `database`, credentials preserved."""
    base = make_url(os.environ["TEST_DATABASE_URL"])
    return base.set(database=database).render_as_string(hide_password=False)
