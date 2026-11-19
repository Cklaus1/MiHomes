"""Waitlist model shape — columns, nullability, constraints (SPEC-001 §4.2).

These assertions read the SQLAlchemy metadata rather than round-tripping rows, so
they hold without a database. The Postgres round-trip lives in
tests/integration/test_migration_waitlist.py (A3).
"""

import uuid

import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, Text

from mihomes.models import Base
from mihomes.models.waitlist import Waitlist


def test_registered_on_metadata():
    """The model must be importable from mihomes.models and present on Base.metadata."""
    import mihomes.models as models

    assert hasattr(models, "Waitlist"), "Waitlist must be re-exported from mihomes.models"
    assert "waitlist" in Base.metadata.tables


def test_is_global_no_account_id():
    """D4 — the waitlist is GLOBAL: it ships before `accounts` exists.

    PRD_REVIEW A5 classes it as bootstrap alongside `sessions` and
    `processed_webhook_events`. A naive tenant policy on this table would break
    Phase 0 signup outright, so the absence of account_id is load-bearing, not an
    oversight to be "fixed" in Phase 1.
    """
    cols = set(Waitlist.__table__.columns.keys())
    assert "account_id" not in cols


def test_primary_key_has_no_server_default():
    """D5 — ids are generated app-side; no DB-side default.

    gen_random_uuid() emits v4, which would mix versions in one column and
    destroy the index locality that is the entire reason to choose v7.

    Asserts the default's *behaviour*, not its identity: `new_id` is bound at
    import to `uuid.uuid7` on 3.14+, and an identity check against the imported
    alias is brittle — it compares function objects that need not be the same
    object across import paths.
    """
    pk = Waitlist.__table__.c.id
    assert pk.primary_key
    assert pk.server_default is None, "no DB-side default — see D5"
    assert pk.default is not None, "id must have an app-side default"

    generated = pk.default.arg(None)
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7, "app-side default must produce UUIDv7, not v4"


@pytest.mark.parametrize(
    "name,type_,nullable",
    [
        ("email", String, False),
        ("name", String, True),
        ("num_homes", String, True),
        ("has_staff", Boolean, True),
        ("source", String, True),
        ("utm_campaign", String, True),
        ("utm_source", String, True),
        ("utm_medium", String, True),
        ("referred_by", String, True),
        ("confirm_token_hash", String, True),
        ("confirm_sent_at", DateTime, True),
        ("confirmed_at", DateTime, True),
        ("confirm_send_count", Integer, False),
        ("signup_ip", String, True),
        ("user_agent", Text, True),
        ("created_at", DateTime, False),
        ("updated_at", DateTime, False),
    ],
)
def test_column_types_and_nullability(name, type_, nullable):
    col = Waitlist.__table__.c[name]
    assert isinstance(col.type, type_), f"{name} should be {type_.__name__}"
    assert col.nullable is nullable, f"{name} nullable should be {nullable}"


@pytest.mark.parametrize(
    "name,length",
    [
        ("email", 320),        # RFC 5321 maximum
        ("name", 200),
        ("num_homes", 10),
        ("confirm_token_hash", 64),   # sha256 hexdigest
        ("signup_ip", 45),            # INET6 maximum
    ],
)
def test_string_lengths(name, length):
    assert Waitlist.__table__.c[name].type.length == length


def test_email_is_unique_and_indexed():
    """GTM:206 — one row per email; upsert on repeat."""
    email = Waitlist.__table__.c.email
    assert email.unique is True
    assert email.index is True


def test_num_homes_is_a_string_not_an_integer():
    """The form offers `1 / 2-3 / 4+` (GTM:202). '2-3' is not an integer.

    Called out explicitly because Integer looks like the obvious "fix" and would
    silently break the middle option. The spec's field notes say: do not.
    """
    assert isinstance(Waitlist.__table__.c.num_homes.type, String)


def test_has_staff_is_three_state():
    """Nullable Boolean — yes / no / didn't answer.

    GTM:202 makes the question optional, so False and "unanswered" must stay
    distinguishable. A NOT NULL default would collapse them.
    """
    col = Waitlist.__table__.c.has_staff
    assert col.nullable is True
    assert col.server_default is None
    assert col.default is None


def test_timestamps_are_timezone_aware():
    """Postgres timestamptz, not naive.

    M7 in opportunities.md is the existing tree's naive-DateTime hazard; new
    tables should not add to it.
    """
    for name in ("confirm_sent_at", "confirmed_at", "created_at", "updated_at"):
        assert Waitlist.__table__.c[name].type.timezone is True, f"{name} must be tz-aware"


def test_confirm_send_count_defaults_to_zero():
    """Bounds resend abuse (§5.4) — must be NOT NULL with a server-side 0."""
    col = Waitlist.__table__.c.confirm_send_count
    assert col.nullable is False
    assert col.server_default is not None
    assert "0" in str(col.server_default.arg)


def test_no_raw_token_column():
    """confirm_token_hash stores a hash; the raw token exists only in the email.

    Same discipline as invite tokens (ONBOARDING_AUTH_RBAC §10). A column named
    for the raw token would be the mistake this guards against — A5 proves the
    behaviour, this proves the shape.
    """
    cols = set(Waitlist.__table__.columns.keys())
    assert "confirm_token" not in cols
    assert "token" not in cols
    assert "confirm_token_hash" in cols
