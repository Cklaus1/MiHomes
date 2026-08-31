#!/usr/bin/env python
"""Create, migrate, seed and sign into a local `mihomes_dev` database.

    py scripts/dev_setup.py

**Why this exists.** Since SPEC-002 the app refuses SQLite outright — every tenant control it
relies on (RLS, the `app.current_account` GUC, `FORCE ROW LEVEL SECURITY`, the drift-guard
trigger) is PostgreSQL-only, and a SQLite database built from these migrations would run with
*no tenant isolation and no sign that anything was wrong*. That correctly killed `--demo`,
which seeded SQLite, and left no way to simply look at the app.

Sign-in is Google OAuth only, so without credentials configured every route is a 401. This
writes the same rows the OAuth callback would — user, owner membership, session — and prints
the cookie to paste into a browser.

**Local development only.** It mints a session without authenticating anybody, which is exactly
what you do not want anywhere else. It refuses to run against a database not named
`mihomes_dev` for that reason.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid

DB = "mihomes_dev"
URL = f"postgresql+psycopg://postgres@localhost:5432/{DB}"
ADMIN = "postgresql+psycopg://postgres@localhost:5432/postgres"

if os.environ.get("DATABASE_URL") and not os.environ["DATABASE_URL"].endswith(DB):
    sys.exit(f"refusing to run: DATABASE_URL points somewhere other than {DB}")
os.environ["DATABASE_URL"] = URL

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


def main() -> None:
    admin = create_engine(ADMIN, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        if not c.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": DB}
        ).scalar():
            c.execute(text(f'CREATE DATABASE "{DB}"'))
            print(f"created database {DB}")
    admin.dispose()

    env = {**os.environ, "DATABASE_URL": URL, "MIGRATION_DATABASE_URL": URL}
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env, capture_output=True, text=True,
    )
    if r.returncode != 0:
        sys.exit(f"alembic failed:\n{r.stderr[-800:]}")
    print("migrations at head")

    engine = create_engine(URL, future=True)
    Session = sessionmaker(bind=engine, future=True)

    from mihomes.auth import sessions as sess
    from mihomes.tenancy.context import account_context

    # --- account ---------------------------------------------------------------------
    with engine.begin() as c:
        account_id = c.execute(text("SELECT id FROM accounts LIMIT 1")).scalar()
        if not account_id:
            account_id = uuid.uuid4()
            c.execute(
                text(
                    "INSERT INTO accounts (id, slug, name, type, plan, created_at, updated_at)"
                    " VALUES (:i, 'belle', 'Belle Estate', 'household', 'estate', now(), now())"
                ),
                {"i": account_id},
            )
            print("created account 'Belle Estate'")

    # --- content ---------------------------------------------------------------------
    with account_context(account_id), Session() as s:
        if s.execute(text("SELECT count(*) FROM properties")).scalar_one() == 0:
            from mihomes.models.issue import IssueSeverity
            from mihomes.services.issue import create_issue
            from mihomes.services.property import create_property
            from mihomes.services.staff import create_staff
            from mihomes.services.task import create_task

            main_prop = create_property(s, "Belle Estate")
            create_property(s, "Ibiza Villa")
            s.flush()

            for title in (
                "Service the pool pump",
                "Replace kitchen extractor filter",
                "Annual boiler inspection",
                "Repaint the guest bathroom",
                "Trim the hedge along the drive",
            ):
                create_task(s, title, main_prop.slug)

            for title, sev in (
                ("Kitchen boiler is leaking", "high"),
                ("Guest room window will not close", "medium"),
                ("Driveway gate motor intermittent", "low"),
            ):
                create_issue(s, title, main_prop.slug, severity=IssueSeverity(sev))

            create_staff(s, "Maria Gomez", role="housekeeper")
            create_staff(s, "Tom Reilly", role="groundskeeper")
            s.commit()
            print("seeded 2 properties, 5 tasks, 3 issues, 2 staff")

    # --- user, membership, session ----------------------------------------------------
    with engine.begin() as c:
        # **Reuse the account's existing owner if there is one.** `uq_membership_one_owner`
        # allows exactly one owner per account (SPEC-003 D6), so a second run that minted a
        # fresh user would violate it — which is the schema being right and this script being
        # naive. Found by running it twice.
        owner = c.execute(
            text(
                "SELECT user_id FROM memberships"
                " WHERE account_id = :a AND role = 'owner' AND status = 'active'"
            ),
            {"a": account_id},
        ).scalar()

        if owner:
            user_id = owner
            email = c.execute(
                text("SELECT email FROM users WHERE id = :u"), {"u": user_id}
            ).scalar()
            print(f"reusing existing owner ({email})")
        else:
            user_id = uuid.uuid4()
            c.execute(
                text(
                    "INSERT INTO users (id, google_sub, email, name, created_at)"
                    " VALUES (:i, :g, :e, 'Local Dev', now())"
                ),
                {"i": user_id, "g": f"local-dev-{user_id.hex[:12]}", "e": "dev@localhost"},
            )
            c.execute(
                text(
                    "INSERT INTO memberships (id, account_id, user_id, role, status, created_at)"
                    " VALUES (:i, :a, :u, 'owner', 'active', now())"
                ),
                {"i": uuid.uuid4(), "a": account_id, "u": user_id},
            )
            print("created owner user dev@localhost")

    with Session() as s:
        raw, _row = sess.create_session(s, user_id=user_id)
        s.commit()

    # `current_account_id` is what `resolve_principal` reads; without it every route is a 403
    # ("No account selected") even though the session is valid. The account picker sets this
    # in the real flow.
    with engine.begin() as c:
        c.execute(
            text("UPDATE sessions SET current_account_id = :a WHERE current_account_id IS NULL"),
            {"a": account_id},
        )

    print()
    print("=" * 78)
    print("  1. Start the server:")
    print(f'       $env:DATABASE_URL = "{URL}"')
    print("       mihomes-dev")
    print()
    print("  2. Open http://localhost:5000 — you will get a 401 until the cookie is set.")
    print()
    print("  3. Press F12, open the CONSOLE tab, paste this line, press Enter:")
    print()
    print(f'       document.cookie = "{sess.SESSION_COOKIE}={raw}; path=/"; location.reload();')
    print()
    # The Console one-liner rather than the Application panel: pasting a name/value pair into
    # the cookie editor means five clicks and an easy typo, and a mistyped token reads as
    # "still broken" rather than "mistyped".
    #
    # This works on http://localhost because `auth.py:_set_cookie` drops the `Secure` flag on
    # loopback — verified, not assumed. On any other host the browser would silently discard a
    # cookie set this way over http, and the symptom would be an unexplained 401.
    print("=" * 78)


if __name__ == "__main__":
    main()
