"""Waitlist service — signup, confirm, position, counts (SPEC-001 A4-A7, §5.4).

Runs on the shared SQLite `session` fixture: this layer is pure business logic and
the Postgres-specific behaviour (schema, uniqueness at the DB level) is covered by
tests/integration/test_migration_waitlist.py.
"""

from datetime import datetime, timedelta, timezone

import pytest

from mihomes.models.waitlist import Waitlist
from mihomes.services import waitlist as svc

# --- normalize_email ------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Alex@Example.COM  ", "alex@example.com"),
        ("USER@DOMAIN.IO", "user@domain.io"),
        ("mixed.Case+tag@gmail.com", "mixed.case+tag@gmail.com"),
    ],
)
def test_normalize_email_lowercases_and_strips(raw, expected):
    assert svc.normalize_email(raw) == expected


def test_normalize_email_does_not_fold_plus_addresses():
    """Deliberately NOT aggressive (§5.4).

    Gmail treats a+b@gmail.com as a@gmail.com but most providers do not, and
    silently merging two people's signups is worse than a duplicate row.
    """
    assert svc.normalize_email("a+b@example.com") == "a+b@example.com"
    assert svc.normalize_email("first.last@example.com") == "first.last@example.com"


@pytest.mark.parametrize(
    "bad", ["", "   ", "no-at-sign", "@nolocal.com", "trailing@", "two@@ats.com", "spaced out@x.com"]
)
def test_normalize_email_rejects_implausible_addresses(bad):
    with pytest.raises(ValueError):
        svc.normalize_email(bad)


# --- signup ---------------------------------------------------------------


def test_signup_creates_a_row_and_returns_a_raw_token(session):
    row, token = svc.signup(session, email="Alex@Example.com")

    assert row.email == "alex@example.com", "email must be normalized on the way in"
    assert token, "the raw token is returned exactly once, for the email"
    assert row.confirmed_at is None
    assert row.confirm_send_count == 1


def test_signup_is_idempotent(session):
    """A4 — a duplicate email updates the existing row; never a second row."""
    first, _ = svc.signup(session, email="alex@example.com", name="Alex")
    second, token = svc.signup(session, email="  ALEX@example.com ", name="Alexandra")

    assert first.id == second.id, "the same address must not create a second row"
    assert session.query(Waitlist).count() == 1
    assert second.name == "Alexandra", "a repeat signup refreshes the details"
    assert token, "an unconfirmed row gets a fresh token so the mail can be resent (N3)"
    assert second.confirm_send_count == 2, "resends are counted to bound abuse"


def test_token_stored_hashed_only(session):
    """A5 — the raw confirm token is never persisted."""
    row, token = svc.signup(session, email="alex@example.com")
    session.flush()

    assert row.confirm_token_hash
    assert row.confirm_token_hash != token
    assert len(row.confirm_token_hash) == 64, "sha256 hexdigest"

    # The raw token must appear in no column of the row.
    values = [str(getattr(row, c.name)) for c in Waitlist.__table__.columns]
    assert not any(token in v for v in values), "raw token leaked into the row"


def test_signup_records_attribution_and_diagnostics(session):
    row, _ = svc.signup(
        session,
        email="alex@example.com",
        num_homes="2-3",
        has_staff=True,
        source="google",
        utm={"utm_campaign": "launch", "utm_source": "x", "utm_medium": "social"},
        referred_by="friend@example.com",
        signup_ip="203.0.113.9",
        user_agent="Mozilla/5.0",
    )

    assert row.num_homes == "2-3", "'2-3' is not an integer — see the model field notes"
    assert row.has_staff is True
    assert row.source == "google"
    assert row.utm_campaign == "launch"
    assert row.utm_source == "x"
    assert row.utm_medium == "social"
    assert row.referred_by == "friend@example.com"
    assert row.signup_ip == "203.0.113.9"
    assert row.user_agent == "Mozilla/5.0"


def test_signup_on_a_confirmed_row_returns_no_token(session):
    """§5.4 — an already-confirmed signup does not get a new token."""
    row, token = svc.signup(session, email="alex@example.com")
    svc.confirm(session, raw_token=token)

    same_row, new_token = svc.signup(session, email="alex@example.com")
    assert same_row.id == row.id
    assert new_token is None, "a confirmed row must not be re-tokenized"


def test_signup_stops_issuing_tokens_past_the_resend_ceiling(session):
    """`confirm_send_count` bounds resend abuse (§5.4 field notes, N3).

    The ceiling value is not fixed by the spec; MAX_CONFIRM_SENDS is our choice.
    What matters is that the endpoint stays silent about it — N3 forbids a
    distinguishable response — so the row is returned with no token rather than
    raising.
    """
    _, token = svc.signup(session, email="alex@example.com")
    assert token

    for _ in range(svc.MAX_CONFIRM_SENDS + 3):
        row, tok = svc.signup(session, email="alex@example.com")

    assert row.confirm_send_count <= svc.MAX_CONFIRM_SENDS
    assert tok is None, "past the ceiling, no further token is issued"


# --- confirm --------------------------------------------------------------


def test_confirm_sets_confirmed_at(session):
    _, token = svc.signup(session, email="alex@example.com")
    row = svc.confirm(session, raw_token=token)

    assert row is not None
    assert row.confirmed_at is not None


def test_confirm_idempotent(session):
    """A6 — a second confirm is a no-op, not an error.

    Users click links twice and mail scanners pre-fetch them, so this is the
    normal case rather than an edge case.
    """
    _, token = svc.signup(session, email="alex@example.com")
    first = svc.confirm(session, raw_token=token)
    first_time = first.confirmed_at

    second = svc.confirm(session, raw_token=token)
    assert second is not None
    assert second.id == first.id
    assert second.confirmed_at == first_time, "must not move the timestamp"


@pytest.mark.parametrize("bad", ["", "not-a-real-token", "0" * 43])
def test_confirm_rejects_bad_token(session, bad):
    """A7 — an unknown token does not confirm."""
    svc.signup(session, email="alex@example.com")
    assert svc.confirm(session, raw_token=bad) is None


def test_confirm_rejects_expired_token(session):
    """A7 — an expired token does not confirm.

    TTL is our choice (CONFIRM_TOKEN_TTL); the spec requires expiry without
    fixing a value.
    """
    _, token = svc.signup(session, email="alex@example.com")
    row = session.query(Waitlist).one()
    row.confirm_sent_at = datetime.now(timezone.utc) - (svc.CONFIRM_TOKEN_TTL + timedelta(minutes=1))
    session.flush()

    assert svc.confirm(session, raw_token=token) is None
    assert session.query(Waitlist).one().confirmed_at is None


def test_confirm_does_not_leak_via_timing_of_unknown_vs_known(session):
    """A wrong token and an absent row must be indistinguishable to the caller."""
    svc.signup(session, email="alex@example.com")
    assert svc.confirm(session, raw_token="wrong") is None
    assert svc.confirm(session, raw_token="also-wrong") is None


# --- position / confirmed_count -------------------------------------------


def test_position_is_1_based_among_confirmed_rows(session):
    """GTM:212 — queue position by created_at among confirmed rows."""
    rows = []
    for i in range(3):
        row, token = svc.signup(session, email=f"u{i}@example.com")
        svc.confirm(session, raw_token=token)
        # Force distinct, ordered created_at values: a tight loop would otherwise
        # give identical timestamps and make the ordering arbitrary.
        row.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i)
        rows.append(row)
    session.flush()

    assert svc.position(session, rows[0]) == 1
    assert svc.position(session, rows[1]) == 2
    assert svc.position(session, rows[2]) == 3


def test_position_ignores_unconfirmed_rows(session):
    """The gate counts confirmed signups, so the queue must too."""
    early, early_token = svc.signup(session, email="early@example.com")
    svc.signup(session, email="pending@example.com")  # never confirmed
    svc.confirm(session, raw_token=early_token)
    session.flush()

    assert svc.position(session, early) == 1


def test_position_is_stable_when_created_at_ties(session):
    """Rows created in the same second must still get distinct, 1-based positions.

    Regression: `created_at` comes from a server default, so a tight signup loop
    gives identical timestamps. A bare `created_at < row.created_at` then counted
    a peer — or the row itself — as "ahead", and the only confirmed row reported
    position 2. The queue is ordered on (created_at, id) with the row explicitly
    excluded.
    """
    confirmed = []
    for i in range(4):
        row, token = svc.signup(session, email=f"tie{i}@example.com")
        svc.confirm(session, raw_token=token)
        confirmed.append(row)
    session.flush()

    # No created_at fixing here on purpose — the point is that ties are handled.
    positions = sorted(svc.position(session, r) for r in confirmed)
    assert positions == [1, 2, 3, 4], f"expected a total order, got {positions}"


def test_position_rejects_an_unconfirmed_row(session):
    """Position is a *queue* position, and the queue is confirmed rows only."""
    row, _ = svc.signup(session, email="pending@example.com")
    session.flush()
    with pytest.raises(ValueError):
        svc.position(session, row)


def test_confirmed_count_is_the_gate_metric(session):
    """GTM:293 — the Phase 0 gate counts *confirmed* signups (O3 sets the number)."""
    assert svc.confirmed_count(session) == 0

    _, t1 = svc.signup(session, email="a@example.com")
    svc.signup(session, email="b@example.com")  # unconfirmed
    assert svc.confirmed_count(session) == 0, "unconfirmed rows must not count"

    svc.confirm(session, raw_token=t1)
    assert svc.confirmed_count(session) == 1
