"""N5 enforcement — refuse to serve as a role that bypasses RLS.

**Why this exists.** SPEC-002 builds two independent tenant boundaries: the ORM filter
(§4.4) and row-level security (§4.3). The second one is worth exactly nothing if the
application connects as a superuser, because **superusers bypass RLS unconditionally — even
with `FORCE ROW LEVEL SECURITY`.** Measured during G7: as `postgres` with the tenant GUC
unset, a FORCE-protected table returned every row. No error, no warning; the rows are simply
there.

N5 already says the runtime role must not be an owner or a `BYPASSRLS` role. Until now that
was a sentence in a document — nothing in the code, the tests, or startup checked it. A single
wrong `DATABASE_URL` would silently reduce two enforcement layers to one, and **raw SQL would
have none at all**, since the ORM filter never sees a `text()` statement.

This turns that sentence into a control: the server refuses to start.

**Deliberately not called from `create_app()` or `get_engine()`.** Migrations legitimately run
as the owner and must bypass policies, and the test suite deliberately uses a superuser
connection for setup and a separate non-superuser one for RLS assertions. Binding the check to
engine creation would break both. It belongs at the point where *the application begins serving
requests* — that is the only moment where "this connection will handle tenant traffic" is true.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

__all__ = ["PrivilegedRoleError", "verify_runtime_role"]


class PrivilegedRoleError(RuntimeError):
    """The application is connected as a role that can see every tenant's rows."""


_MESSAGE = (
    "Refusing to start: the application is connected to Postgres as {role!r}, which "
    "{why}. Such a role BYPASSES row-level security entirely — with FORCE ROW LEVEL "
    "SECURITY set, and with no error — so every tenant's rows would be visible to every "
    "request, and raw SQL would have no tenant enforcement at all.\n\n"
    "SPEC-002 N5: the runtime role must be a non-owner, non-superuser, non-BYPASSRLS role. "
    "Migrations legitimately run as the owner; the app must not.\n\n"
    "Fix: point DATABASE_URL at the unprivileged application role, e.g.\n"
    "  CREATE ROLE mihomes_app LOGIN PASSWORD '...';\n"
    "  GRANT USAGE ON SCHEMA public TO mihomes_app;\n"
    "  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO mihomes_app;"
)


def verify_runtime_role(engine: Engine) -> None:
    """Raise `PrivilegedRoleError` if this connection can bypass RLS.

    A no-op for non-Postgres engines: SQLite has no RLS to bypass, and a SQLite deployment is
    already recorded as having no tenant enforcement (see `tenancy/rls.py`).

    Checks both `rolsuper` and `rolbypassrls`. They are different privileges and either one is
    sufficient to defeat RLS, so checking only `usesuper` — the obvious query — would miss a
    role explicitly granted `BYPASSRLS`.
    """
    if engine.dialect.name != "postgresql":
        return

    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT current_user AS role, "
                "       COALESCE(r.rolsuper, false)      AS is_super, "
                "       COALESCE(r.rolbypassrls, false)  AS bypasses_rls "
                "FROM pg_roles r WHERE r.rolname = current_user"
            )
        ).one()

    reasons = []
    if row.is_super:
        reasons.append("is a SUPERUSER")
    if row.bypasses_rls:
        reasons.append("has the BYPASSRLS attribute")
    if reasons:
        raise PrivilegedRoleError(
            _MESSAGE.format(role=row.role, why=" and ".join(reasons))
        )
