"""Identity model shape — roles, the one-owner index, status enum (SPEC-002 A2, §4.2).

Metadata assertions, so they hold without a database. The *enforcement* of the
partial unique index is a Postgres behaviour and is tested in
tests/integration/test_rls.py once the baseline exists; here we prove the index is
declared with the right predicate, which is what makes that enforcement possible.
"""

import pytest
from sqlalchemy import DateTime, String

from mihomes.models import Base
from mihomes.models.account import Account
from mihomes.models.membership import Membership, MembershipPropertyScope
from mihomes.models.session import Session as SessionRow
from mihomes.models.user import User


def test_identity_models_are_registered():
    for table in (
        "accounts",
        "users",
        "memberships",
        "membership_property_scopes",
        "invites",
        "sessions",
    ):
        assert table in Base.metadata.tables, f"{table} missing from metadata"


def test_one_owner_partial_index():
    """A2 — exactly one ACTIVE owner per account (D4).

    A plain `UNIQUE (account_id, role)` would be wrong twice over: it would forbid
    two admins, and it would let a *revoked* owner block appointing a new one. The
    predicate is what makes it correct, so the test asserts the predicate rather
    than merely the index's existence.
    """
    indexes = {ix.name: ix for ix in Membership.__table__.indexes}
    assert "uq_membership_one_owner" in indexes, "the one-owner index must be declared"

    index = indexes["uq_membership_one_owner"]
    assert index.unique is True

    predicate = str(index.dialect_options["postgresql"]["where"])
    assert "owner" in predicate, "the index must be scoped to role = 'owner'"
    assert "active" in predicate, (
        "the index must be scoped to status = 'active' — a revoked owner must not "
        "block appointing a new one"
    )

    assert [c.name for c in index.columns] == ["account_id"]


def test_membership_is_unique_per_account_and_user():
    """One membership row per (account, user) — a person joins an account once."""
    constraints = {
        c.name: c
        for c in Membership.__table__.constraints
        if getattr(c, "name", None)
    }
    assert "uq_membership_account_user" in constraints

    cols = [c.name for c in constraints["uq_membership_account_user"].columns]
    assert set(cols) == {"account_id", "user_id"}


def test_membership_status_has_no_invited_value():
    """N7 — do NOT add 'invited' to memberships.status (D6: active | revoked only).

    An invitee has no `user_id` yet, which the column requires as NOT NULL. Pending
    invitations live in `invites`. Encoding them here would mean either a nullable
    user_id (breaking the unique constraint above) or a fake user row.
    """
    status = Membership.__table__.c.status
    assert status.nullable is False
    assert "active" in str(status.server_default.arg)

    # No CHECK constraint or Enum should admit 'invited'.
    rendered = " ".join(
        str(c.sqltext) for c in Membership.__table__.constraints
        if hasattr(c, "sqltext")
    )
    assert "invited" not in rendered.lower()


def test_user_is_global():
    """D3 — `users` is GLOBAL: no account_id, no tenant RLS.

    A person exists independent of any account, and the row is read *before*
    account context exists.

    **`google_sub` became nullable in SPEC-010 (D6), and this assertion inverted with it.**
    It asserted NOT NULL, correctly, for as long as every user arrived through Google. A
    password user has no Google subject, so requiring one would make email/password sign-up
    impossible — the column is now the Google *identity* rather than a universal key.

    The `unique` half is unchanged and must stay: a nullable unique column is fine in Postgres,
    because NULLs do not collide. Every password user carries `google_sub IS NULL` while Google
    users stay unique among themselves.
    """
    assert "account_id" not in User.__table__.columns
    assert User.__table__.c.google_sub.unique is True
    assert User.__table__.c.google_sub.nullable is True, (
        "google_sub must be nullable — a password user has no Google subject (SPEC-010 D6)"
    )


def test_user_email_is_not_the_identity_key():
    """google_sub is the identity; email is display-only and may change.

    Keying on email would break the moment someone changes their Google address,
    silently orphaning their memberships.

    **Unchanged by SPEC-010, and that is the point.** Email/password auth does key a password
    user on their address — but it enforces that through a PARTIAL index covering only rows
    with a password (`uq_users_email_password`), never on the column. Making the column unique
    table-wide would satisfy the password requirement and break Google identity: `test_auth.py`
    asserts that the same address under two different subjects is two different people, which
    is correct, because an address can be reassigned and a subject cannot.

    So if this test ever seems to need editing to make password auth work, the design has gone
    wrong — the fix belongs in the index, not here.
    """
    email = User.__table__.c.email
    assert email.unique is not True, "email must NOT be the unique identity key"
    assert email.index is True
    assert isinstance(email.type, String)


def test_sessions_is_global():
    """D3 — `sessions` is GLOBAL. This one is load-bearing.

    The auth middleware reads it *before* account context exists. A tenant policy
    here would return zero rows and lock every user out of the product.
    """
    assert "account_id" not in SessionRow.__table__.columns
    assert "current_account_id" in SessionRow.__table__.columns, (
        "the session records which account is selected, without being scoped by it"
    )
    assert SessionRow.__table__.c.current_account_id.nullable is True, (
        "a freshly signed-in user has not picked an account yet"
    )


def test_session_stores_only_a_hash():
    """The raw session id goes to the cookie and never to the database.

    Same discipline as SPEC-001's confirm token and the invite tokens in
    ONBOARDING §10: a database leak must not yield usable credentials.
    """
    cols = set(SessionRow.__table__.columns.keys())
    assert "session_id_hash" in cols
    assert "session_id" not in cols, "never store the raw session id"
    assert SessionRow.__table__.c.session_id_hash.unique is True


def test_account_has_no_owner_user_id():
    """§4.2 — ownership is the partial index on memberships, not a column here.

    Two sources of truth for ownership is how they drift apart. A3 reconciled this.
    """
    assert "owner_user_id" not in Account.__table__.columns


def test_account_slug_is_globally_unique():
    """Accounts are addressed by slug in URLs and the CLI's --account flag."""
    slug = Account.__table__.c.slug
    assert slug.unique is True
    assert slug.nullable is False


@pytest.mark.parametrize(
    "column,default",
    [("type", "household"), ("plan", "free")],
)
def test_account_defaults(column, default):
    """A3: `type` defaults to household; plan starts free."""
    col = Account.__table__.c[column]
    assert col.nullable is False
    assert default in str(col.server_default.arg)


def test_phase3_billing_columns_ship_now_but_nullable():
    """DEFERRED (Phase 3) — the columns ship now so Phase 3 needs no migration on a
    live table. All nullable, none written by Phase 1.

    Note `subscription_status`, NOT `billing_status` (A3 reconciled the name).
    """
    for name in (
        "stripe_customer_id",
        "stripe_subscription_id",
        "subscription_status",
        "current_period_end",
        "trial_ends_at",
        "trial_used_at",
    ):
        assert name in Account.__table__.columns, f"{name} should ship in Phase 1"
        assert Account.__table__.c[name].nullable is True

    assert "billing_status" not in Account.__table__.columns, (
        "A3 reconciled this to subscription_status"
    )


def test_invite_is_tenant_owned():
    """Invites belong to an account, and a pending row CONSUMES A SEAT."""
    from mihomes.models.invite import Invite

    assert "account_id" in Invite.__table__.columns
    cols = set(Invite.__table__.columns.keys())
    assert "token_hash" in cols
    assert "token" not in cols, "hash only, never the raw invite token"
    assert "expires_at" in cols


def test_membership_property_scope_is_a_whitelist():
    """A4/D5 — property, not "home" (N8). Zero rows = zero properties visible.

    Fail closed: a staff member with no scope rows sees nothing, rather than
    everything.
    """
    cols = set(MembershipPropertyScope.__table__.columns.keys())
    assert "account_id" in cols
    assert "membership_id" in cols
    assert "property_id" in cols
    assert "home_id" not in cols, "N8: a home is a properties row"


def test_timestamps_are_timezone_aware():
    """Postgres timestamptz throughout — M7 is the old tree's naive-datetime hazard."""
    for model in (Account, User, Membership):
        for col in model.__table__.columns:
            if isinstance(col.type, DateTime):
                assert col.type.timezone is True, (
                    f"{model.__tablename__}.{col.name} must be tz-aware"
                )
