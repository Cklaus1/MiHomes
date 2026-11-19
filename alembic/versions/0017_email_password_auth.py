"""Email/password authentication — SPEC-010 §4, Step 2 (A4, A5).

Three changes to `users`, one new global table, and one partial unique index.

## `google_sub` becomes nullable, and that inverts a shipped gate

`test_membership.py:95` asserts `google_sub` is NOT NULL. It was right when written — every user
arrived through Google — and SPEC-010 D6 deliberately reverses it. **That test is edited in this
same commit**, never afterwards: a migration that lands ahead of the gate defending the old
behaviour leaves the suite red for the length of the gap, and the natural fix at that point is
to "correct" the migration rather than the test.

Its sibling at `:94` — `google_sub` is unique — is **unchanged**, and needs to be. A nullable
unique column is fine in Postgres: NULLs do not collide, so every password user can carry
`google_sub IS NULL` while Google users stay unique among themselves.

## The partial unique index is the whole of D3

    CREATE UNIQUE INDEX uq_users_email_password
    ON users (lower(email)) WHERE password_hash IS NOT NULL

Two properties, and **both halves are load-bearing**:

* two PASSWORD users cannot share a case-folded email — without it, "sign up" silently becomes
  "create a second account nobody can tell apart at the login form";
* two GOOGLE users still can. `test_auth.py:283` asserts that the same address under two
  different subjects is two different people, which is correct: an email address can be
  reassigned to a new person, a Google subject cannot.

A table-wide `UNIQUE(email)` satisfies the first and breaks the second. That is why A5 asserts
both, and why `users.email` must stay non-unique at the column level — `test_membership.py:103`
says so and stays untouched. If that test ever needs editing, the design has gone wrong.

Raw SQL rather than `op.create_index`: the index is both **expression-based** (`lower(email)`)
and **partial** (`WHERE`), and Alembic cannot express that combination portably. It is declared
on the model too (`user.py.__table_args__`) or `test_baseline_matches_metadata` sees drift.

`lower(email)` rather than a CITEXT column: nobody expects `Alice@` and `alice@` to be separate
logins, and an expression index needs no extension and no column rewrite.

## `password_reset_tokens` is GLOBAL

No RLS policy and no drift-guard trigger, unlike `0016`. A reset happens before sign-in, so
there is no account context to scope to — the same carve-out `users` and `sessions` hold.
`test_pg_baseline.py` goes 56 -> 57 **in this commit**, per that file's own rule that raising
the count is a recorded decision rather than a silent adjustment.

## Downgrade is lossy, and says so

Dropping `password_hash` destroys every password. The downgrade is written and tested because
the round-trip gate requires it, but a real rollback past this point locks out every
password-only user — they would have to be re-invited or reset through Google. Recorded here
because "the downgrade ran cleanly" is not the same as "the downgrade was safe".

Revision ID: 0017_email_password_auth
Revises: 0016_gateway_link_tokens
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017_email_password_auth"
down_revision: Union[str, None] = "0016_gateway_link_tokens"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PARTIAL_INDEX = "uq_users_email_password"


def upgrade() -> None:
    # --- users: the credential columns ---------------------------------------
    op.add_column("users", sa.Column("password_hash", sa.String(255), nullable=True))
    op.add_column(
        "users",
        sa.Column("password_set_at", sa.DateTime(timezone=True), nullable=True),
    )

    # D6 — a password user has no Google subject. The UNIQUE constraint survives untouched.
    op.alter_column(
        "users",
        "google_sub",
        existing_type=sa.String(255),
        nullable=True,
    )

    # --- D3: the partial unique index ----------------------------------------
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            f"CREATE UNIQUE INDEX {PARTIAL_INDEX} "
            "ON users (lower(email)) WHERE password_hash IS NOT NULL"
        )

    # --- the reset table (GLOBAL: no RLS policy, no drift-guard trigger) ------
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        # sha256 hex of the raw token — the raw value is emailed once and never stored.
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_password_reset_tokens_user_id"),
        "password_reset_tokens",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_password_reset_tokens_token_hash"),
        "password_reset_tokens",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_password_reset_tokens_token_hash"), table_name="password_reset_tokens"
    )
    op.drop_index(
        op.f("ix_password_reset_tokens_user_id"), table_name="password_reset_tokens"
    )
    op.drop_table("password_reset_tokens")

    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {PARTIAL_INDEX}")

    # Restoring NOT NULL requires every row to have a subject. Any password-only user has
    # google_sub IS NULL and would block this — which is the lossy rollback the docstring
    # warns about, surfacing as a constraint violation rather than silent data loss.
    op.alter_column(
        "users",
        "google_sub",
        existing_type=sa.String(255),
        nullable=False,
    )

    op.drop_column("users", "password_set_at")
    op.drop_column("users", "password_hash")
