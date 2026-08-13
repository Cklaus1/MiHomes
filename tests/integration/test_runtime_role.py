"""N5 — the app must not serve as a role that bypasses RLS.

This is the control that turns N5 from a sentence in the spec into something the code
enforces. Until it existed, nothing anywhere — no test, no startup check — verified which role
the application connects as, while G7 measured that a superuser sees every tenant's rows
through a FORCE-protected table with no error at all.
"""

import pytest

from mihomes.tenancy.runtime_role import PrivilegedRoleError, verify_runtime_role


def test_rejects_a_superuser_connection(_pg_engine):
    """`_pg_engine` is `postgres`. Serving on it would make RLS decorative.

    Uses the suite's own privileged engine deliberately: it is exactly the connection a
    misconfigured `DATABASE_URL` would produce, and it is what the whole suite ran on before
    G7.
    """
    with pytest.raises(PrivilegedRoleError) as exc:
        verify_runtime_role(_pg_engine)
    message = str(exc.value)
    assert "SUPERUSER" in message
    assert "DATABASE_URL" in message, "the error must say how to fix it, not just what is wrong"


def test_accepts_the_unprivileged_app_role(app_engine):
    """The role the RLS tests use — non-superuser, no BYPASSRLS — must pass."""
    verify_runtime_role(app_engine)  # must not raise


def test_rejects_bypassrls_even_without_superuser(_pg_engine):
    """`BYPASSRLS` and `SUPERUSER` are different privileges, and either defeats RLS.

    Checking only `usesuper` — the obvious query — would wave through a role explicitly
    granted `BYPASSRLS`, which is a likelier misconfiguration than a superuser because it
    sounds like a narrow, reasonable grant.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    role = "mihomes_test_bypassrls"
    with _pg_engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.exec_driver_sql(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') "
            f"THEN CREATE ROLE {role} LOGIN PASSWORD '{role}' BYPASSRLS; END IF; END $$;"
        )
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public TO {role}")
        conn.commit()

    url = make_url(str(_pg_engine.url)).set(username=role, password=role)
    engine = create_engine(url, future=True)
    try:
        with pytest.raises(PrivilegedRoleError) as exc:
            verify_runtime_role(engine)
        assert "BYPASSRLS" in str(exc.value)
        assert "SUPERUSER" not in str(exc.value), (
            "this role is not a superuser — the message should name the actual privilege"
        )
    finally:
        engine.dispose()
        with _pg_engine.connect() as conn:
            conn.execution_options(isolation_level="AUTOCOMMIT")
            conn.exec_driver_sql(f"REVOKE USAGE ON SCHEMA public FROM {role}")
            conn.exec_driver_sql(f"DROP ROLE IF EXISTS {role}")
            conn.commit()


def test_sqlite_is_a_no_op():
    """No RLS to bypass, and a SQLite deployment is already recorded as unenforced.

    Raising here would block the local single-user mode for a reason that does not apply to it.
    """
    from sqlalchemy import create_engine

    engine = create_engine("sqlite://")
    try:
        verify_runtime_role(engine)  # must not raise
    finally:
        engine.dispose()


def test_the_server_entrypoint_calls_it():
    """The control is worthless if nothing invokes it.

    Asserted against the module's source because `main()` starts a web server and cannot be
    called in a test. This is the narrow case where reading source is the only option — and
    unlike the guards that tripped over their own prose, a false positive here would be a
    *passing* test, not a failing one, so the risk runs the safe way.
    """
    import inspect

    from mihomes.web import server

    source = inspect.getsource(server.main)
    assert "verify_runtime_role" in source, (
        "server.main() no longer calls verify_runtime_role — N5 is unenforced again"
    )
