"""The suppression list and unsubscribe tokens (SPEC-005 Step 2, A11/A22).

Four concerns, and A1/A2 are here because **§8 declares them here** rather than because this
module owns the mechanism: D13 puts the choke point at `EmailService._send`, and those tests reach
across to call it. Condition E runs every criterion by the node id §8 names, so a test that is
correct in the wrong file is a criterion with no gate.
"""

import logging
import uuid

import pytest
from sqlalchemy import select, text

from mihomes.crypto import EncryptionUnavailable
from mihomes.models.email_suppression import EmailSuppression
from mihomes.services.email import EmailService
from mihomes.services.email.provider import EmailResult
from mihomes.services.email.suppression import (
    InvalidUnsubscribeToken,
    is_suppressed,
    suppress,
    unsubscribe_token,
    verify_unsubscribe_token,
)


class RecordingProvider:
    """Records sends. Accepts the widened Protocol (D11) so it cannot mask a drop."""

    provider_name = "recording"

    def __init__(self):
        self.calls = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.calls.append({"to": to, "subject": subject, "headers": headers})
        return EmailResult(provider_message_id="rec-1", provider=self.provider_name)


def _addr() -> str:
    """A unique address per test — the list is global and never rolled back between them."""
    return f"user-{uuid.uuid4().hex[:12]}@example.com"


# --- A22: suppression is idempotent ------------------------------------------------------


def test_idempotent(session):
    """A22 — suppressing an address twice is a no-op, not an error.

    Bounce and complaint webhooks for one address arrive more than once, and concurrently.
    An error on the second would turn an ordinary retry into a 500, which Stripe and every
    other provider answer by retrying again.
    """
    address = _addr()

    first = suppress(session, address, reason="hard_bounce")
    second = suppress(session, address, reason="complaint")

    assert first is not None
    assert second is None, "the second suppression must report 'already suppressed'"

    rows = session.execute(
        select(EmailSuppression).where(EmailSuppression.address == address)
    ).scalars().all()
    assert len(rows) == 1
    # The FIRST reason wins: both mean "do not send", and the earliest record is the one that
    # explains why the address stopped receiving mail.
    assert rows[0].reason == "hard_bounce"


def test_the_second_suppression_leaves_the_caller_transaction_usable(session):
    """The swallowed `IntegrityError` must not poison the surrounding transaction.

    `suppress` usually runs inside a caller's work — a webhook handler, an unsubscribe request
    — and a bare rollback would discard their changes along with the duplicate insert. The
    savepoint is what makes this survivable, and without this test a `session.rollback()` in
    the except branch would pass every other assertion in this file.
    """
    address = _addr()
    suppress(session, address, reason="unsubscribe")

    suppress(session, address, reason="complaint")

    # The session still works: a query after the swallowed violation must not raise.
    assert is_suppressed(session, address) is True
    assert session.execute(text("SELECT 1")).scalar() == 1


def test_is_suppressed_is_false_for_an_unknown_address(session):
    assert is_suppressed(session, _addr()) is False


def test_casing_and_whitespace_do_not_create_a_second_entry(session):
    """`Someone@Example.com` unsubscribing must suppress `someone@example.com`.

    Otherwise the next send re-mails a complainer on a casing difference — and the address in
    a mail client's unsubscribe POST is not guaranteed to match the casing we stored.
    """
    address = _addr()

    suppress(session, address, reason="unsubscribe")

    assert is_suppressed(session, address.upper()) is True
    assert is_suppressed(session, f"  {address}  ") is True
    assert suppress(session, address.upper(), reason="complaint") is None


def test_an_unknown_reason_is_refused(session):
    """The reason is a closed set; a typo must not be recorded as fact."""
    with pytest.raises(ValueError, match="unknown suppression reason"):
        suppress(session, _addr(), reason="bounced")


# --- A11: the unsubscribe token is unforgeable -------------------------------------------


def test_token_hmac(monkeypatch):
    """A11 — an unsubscribe token is unforgeable.

    A bare `/unsubscribe?email=x` lets anyone unsubscribe anyone, including a competitor
    unsubscribing a customer from their own billing mail. The token is an HMAC over the address
    under the app secret: self-authenticating, with no token table to expire or clean up.
    """
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "k" * 43 + "=")

    token = unsubscribe_token("alex@example.com")

    # Deterministic for one address, and different for another — a token that did not vary by
    # address would let one valid unsubscribe link unsubscribe every recipient.
    assert token == unsubscribe_token("alex@example.com")
    assert token != unsubscribe_token("sam@example.com")

    # The address itself must not be recoverable from the token.
    assert "alex" not in token
    assert "@" not in token

    verify_unsubscribe_token("alex@example.com", token)

    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token("sam@example.com", token)
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token("alex@example.com", "deadbeef")
    with pytest.raises(InvalidUnsubscribeToken):
        verify_unsubscribe_token("alex@example.com", "")


def test_a_different_key_produces_a_different_token(monkeypatch):
    """The secret is what does the work — not the hash function.

    Without this, `sha256(address)` with no key at all would pass every other assertion above
    while being forgeable by anyone who can run sha256.
    """
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "a" * 43 + "=")
    with_first = unsubscribe_token("alex@example.com")

    monkeypatch.setenv("MIHOMES_SECRET_KEY", "b" * 43 + "=")
    assert unsubscribe_token("alex@example.com") != with_first


def test_tokens_are_case_insensitive_in_the_address(monkeypatch):
    """Minted and verified over the same normalized form, or every real click 403s."""
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "k" * 43 + "=")

    verify_unsubscribe_token(
        "Alex@Example.com", unsubscribe_token("alex@example.com")
    )


def test_no_key_refuses_rather_than_minting_an_unsigned_token(monkeypatch):
    """Falling back to an unsigned token would mean forgeable links in a deployment where
    nothing looks wrong — the failure mode this whole mechanism exists to prevent."""
    monkeypatch.delenv("MIHOMES_SECRET_KEY", raising=False)

    with pytest.raises(EncryptionUnavailable):
        unsubscribe_token("alex@example.com")


# --- A1/A2: the transactional/lifecycle split at the choke point -------------------------
#
# These live here because §8 declares them here, not because this is the module that owns
# the mechanism — D13 puts that at `EmailService._send`, and these tests reach across to
# call it. The first draft put them next to the choke point on exactly that reasoning, and
# `pytest tests/unit/test_suppression.py::test_lifecycle_suppressed` then did not resolve:
# condition E runs every criterion **by the node id §8 names**, so a test that is correct
# in the wrong file is a criterion with no gate.


def test_transactional_ignores_suppression(session):
    """A2 — transactional mail to a suppressed address **is** sent (N3).

    A receipt for money taken, a deletion confirmation and an export link are not marketing.
    Suppressing them is not caution; it is withholding a record the customer is owed.
    """
    suppress(session, "alex@example.com", reason="unsubscribe")
    provider = RecordingProvider()

    EmailService(provider, session=session)._send(
        "alex@example.com", "welcome",
        {"account_name": "A", "dashboard_url": "u", "name": None},
        klass="transactional",
    )

    assert len(provider.calls) == 1


def test_lifecycle_suppressed(session):
    """A1 — lifecycle mail to a suppressed address is not sent (D13)."""
    suppress(session, "alex@example.com", reason="unsubscribe")
    provider = RecordingProvider()

    EmailService(provider, session=session)._send(
        "alex@example.com", "welcome",
        {"account_name": "A", "dashboard_url": "u", "name": None},
        klass="lifecycle",
    )

    assert provider.calls == []


def test_lifecycle_to_an_unsuppressed_address_is_sent(session):
    """The other half of A1 — without this, a `_send` that dropped every lifecycle message
    would pass `test_lifecycle_suppressed` and look correct."""
    provider = RecordingProvider()

    EmailService(provider, session=session)._send(
        "someone-else@example.com", "welcome",
        {"account_name": "A", "dashboard_url": "u", "name": None},
        klass="lifecycle",
    )

    assert len(provider.calls) == 1


def test_lifecycle_without_a_session_refuses_rather_than_sending(caplog):
    """No session means no way to check the list — so the send is refused, not attempted.

    Failing open here would make every future caller that forgot to pass a session into a
    silent CAN-SPAM problem: the mail goes out, the suppression list is never consulted, and
    nothing anywhere raises. The landing app is why the session is optional at all (SPEC-001
    D1/D3 give it a separate one-table tree), and it sends only transactional mail.
    """
    provider = RecordingProvider()

    with caplog.at_level(logging.ERROR):
        EmailService(provider, session=None)._send(
            "a@b.com", "welcome",
            {"account_name": "A", "dashboard_url": "u", "name": None},
            klass="lifecycle",
        )

    assert provider.calls == []
    assert any("no session to check suppression" in r.getMessage() for r in caplog.records)
