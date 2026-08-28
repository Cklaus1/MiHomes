"""G11 · §6 Step 11 — the drip machinery (A25).

**Mechanism only.** The content and cadence are O1 — a founder decision about what a stranger
reads in their first week — so every test here asserts against whatever `SEQUENCES` says rather
than against fixed copy. Replacing the templates and intervals must break nothing.

Step 11's verify also cites **A22**, whose declared test is `test_suppression.py::test_idempotent`
— suppressing an address twice is a no-op, which has nothing to do with drips and is already
green from G2. The clause it means is *"a suppressed address receives nothing"*, which is **A1**'s
territory and enforced at the choke point. Asserted here under its own name, because a drip is the
canonical lifecycle message and the one D13 was written about.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa

from mihomes.models.email_campaign import CampaignEnrolment
from mihomes.models.email_outbox import EmailOutbox
from mihomes.services.email import EmailService
from mihomes.services.email.campaigns import (
    ONBOARDING,
    SEQUENCES,
    due_sends,
    enrol,
    send_due_steps,
    sequence_length,
)
from mihomes.services.email.provider import EmailResult
from mihomes.tenancy.context import account_context

NOW = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


class RecordingProvider:
    provider_name = "recording"

    def __init__(self):
        self.sent = []

    def send(self, to, subject, html, *, text=None, reply_to=None, headers=None):
        self.sent.append({"subject": subject, "headers": headers})
        return EmailResult(provider_message_id="rec-1", provider=self.provider_name)


@pytest.fixture
def owner(session, account_a):
    """An owner membership, so the drip has somewhere to send.

    `_recipient` reuses billing's resolver — one definition of "the account's email", so a drip
    and an invoice cannot disagree about who the customer is.
    """
    user_id = uuid.uuid4()
    session.execute(
        sa.text(
            "INSERT INTO users (id, google_sub, email, name, created_at) "
            "VALUES (:id, :sub, :email, 'Drip Owner', now())"
        ),
        {"id": user_id, "sub": str(user_id), "email": f"{user_id}@example.com"},
    )
    session.execute(
        sa.text(
            "INSERT INTO memberships (id, account_id, user_id, role, status, created_at) "
            "VALUES (:id, :a, :u, 'owner', 'active', now())"
        ),
        {"id": uuid.uuid4(), "a": account_a, "u": user_id},
    )
    session.flush()
    return f"{user_id}@example.com"


def _service(session):
    provider = RecordingProvider()
    return provider, EmailService(provider, session=session)


def _queued(session, account_id):
    return list(
        session.execute(
            sa.select(EmailOutbox).where(EmailOutbox.account_id == account_id)
        ).scalars()
    )


# --- enrolment -------------------------------------------------------------------------------


def test_enrol_creates_one_row(session, account_a):
    with account_context(account_a):
        row = enrol(session, account_a, "onboarding", now=NOW)

    assert row is not None
    assert row.campaign == "onboarding"
    assert row.step == 0
    assert row.completed_at is None


def test_enrolling_twice_is_a_no_op(session, account_a):
    """§5.3: *"Idempotent per (account, campaign) — the unique constraint is the guard."*

    Insert-first, so two concurrent enrolments resolve on the violation rather than both
    inserting. G6's `test_the_enrolment_uniqueness_survives_the_migration` keeps that constraint
    in the shipped DDL, which is what this relies on.
    """
    with account_context(account_a):
        first = enrol(session, account_a, "onboarding", now=NOW)
        second = enrol(session, account_a, "onboarding", now=NOW)

    assert first is not None
    assert second is None
    rows = session.execute(
        sa.select(CampaignEnrolment).where(CampaignEnrolment.account_id == account_a)
    ).scalars().all()
    assert len(rows) == 1


def test_an_unknown_campaign_is_refused(session, account_a):
    """A typo would otherwise create an enrolment that can never come due — a row that looks
    live and is silently inert."""
    with account_context(account_a), pytest.raises(ValueError, match="unknown campaign"):
        enrol(session, account_a, "onbaording", now=NOW)


# --- A25: each step once, and never twice -----------------------------------------------------


def test_no_duplicate_steps(session, account_a, owner):
    """**A25** — a drip sends each step once and never twice.

    **The clock advances between runs**, which is the part that matters. Calling `due_sends`
    twice at the same `now` proves only that the query filters; the real risk is step 1 firing
    again a day later. So this walks the whole sequence, asserting the step index moves and each
    template appears exactly once.
    """
    provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)

        for index, (_template, delay) in enumerate(ONBOARDING):
            # Two runs at each point in time: the second must find nothing.
            assert send_due_steps(session, service, now=NOW + delay) == 1
            assert send_due_steps(session, service, now=NOW + delay) == 0

            row = session.execute(sa.select(CampaignEnrolment)).scalars().one()
            assert row.step == index + 1

        service.drain(now=NOW + timedelta(days=365))

    templates = [row.template for row in _queued(session, account_a)]
    assert templates == [template for template, _ in ONBOARDING]
    assert len(set(templates)) == len(templates), f"a step was sent twice: {templates}"


def test_a_step_is_not_sent_before_it_is_due(session, account_a, owner):
    """The half that makes the schedule mean something.

    Without it, a `due_sends` that returned every open enrolment would satisfy "each step once"
    while sending the whole sequence in the first minute.
    """
    _provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)
        assert send_due_steps(session, service, now=NOW) == 0
        assert send_due_steps(session, service, now=NOW + ONBOARDING[0][1]) == 1


def test_a_finished_enrolment_is_completed_and_skipped_forever(session, account_a, owner):
    """`completed_at` non-NULL means the scheduler never looks again.

    Without it the job re-sends the last step every night — the failure mode that turns a
    three-email sequence into an indefinite one.
    """
    _provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)
        for _template, delay in ONBOARDING:
            send_due_steps(session, service, now=NOW + delay)

        row = session.execute(sa.select(CampaignEnrolment)).scalars().one()
        assert row.completed_at is not None

        assert due_sends(session, now=NOW + timedelta(days=365)) == []
        assert send_due_steps(session, service, now=NOW + timedelta(days=365)) == 0


# --- §4.2: a shortened sequence completes, it does not error ----------------------------------


def test_shortening_a_sequence_completes_in_flight_enrolments(
    session, account_a, owner, monkeypatch
):
    """§4.2, and the reason is operational: **O1 may shorten a sequence after enrolments exist.**

    An enrolment whose `step` is past the new end has had everything there is to send, so it
    completes. Raising instead would turn a content edit into a nightly job that fails for
    exactly the accounts furthest along — the worst possible selection, since those are the
    customers who have been around longest.
    """
    _provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)
        # Advance to step 2 of 3.
        send_due_steps(session, service, now=NOW + ONBOARDING[0][1])
        send_due_steps(session, service, now=NOW + ONBOARDING[1][1])
        row = session.execute(sa.select(CampaignEnrolment)).scalars().one()
        assert row.step == 2

        # O1 shortens the sequence to one step — the enrolment is now past its end.
        monkeypatch.setitem(SEQUENCES, "onboarding", ONBOARDING[:1])

        due = due_sends(session, now=NOW + timedelta(days=365))

    assert due == [], "an enrolment past the new end is not due, it is finished"
    row = session.execute(sa.select(CampaignEnrolment)).scalars().one()
    assert row.completed_at is not None, "it must be completed, not left open forever"


def test_lengthening_a_sequence_resumes_a_completed_enrolment(
    session, account_a, owner, monkeypatch
):
    """The mirror case, which the completion rule must not break.

    An enrolment completed under a three-step sequence has `completed_at` set, so adding a
    fourth step does **not** revive it. That is the correct behaviour — reviving would mail a
    customer who finished the sequence months ago — and it is worth pinning, because the
    obvious reading of "step < length" would resume them.
    """
    _provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)
        for _template, delay in ONBOARDING:
            send_due_steps(session, service, now=NOW + delay)

        monkeypatch.setitem(
            SEQUENCES, "onboarding", ONBOARDING + (("drip_onboarding_4", timedelta(days=20)),)
        )

        assert send_due_steps(session, service, now=NOW + timedelta(days=365)) == 0


def test_sequence_length_is_read_live(monkeypatch):
    """Never cached on the row — that is what makes §4.2's rule expressible at all."""
    assert sequence_length("onboarding") == len(ONBOARDING)
    monkeypatch.setitem(SEQUENCES, "onboarding", ONBOARDING[:1])
    assert sequence_length("onboarding") == 1


# --- the clause Step 11 cites as A22 -----------------------------------------------------------


def test_a_suppressed_address_receives_nothing(session, account_a, owner):
    """Step 11's *"a suppressed address receives nothing"*.

    Cited as A22 in §6, whose declared test is about `suppress` being idempotent — a different
    claim entirely. The behaviour meant here is A1's, enforced at `EmailService._send`, and a
    drip is the canonical case: it **is** marketing, which is the half of D13 that makes the
    transactional/lifecycle split coherent.
    """
    from mihomes.services.email.suppression import suppress

    provider, service = _service(session)

    with account_context(account_a):
        suppress(session, owner, reason="unsubscribe")
        enrol(session, account_a, "onboarding", now=NOW)
        send_due_steps(session, service, now=NOW + ONBOARDING[0][1])
        service.drain(now=NOW + timedelta(days=365))

    assert provider.sent == [], "a suppressed address must receive no drip"


def test_a_drip_is_lifecycle_and_carries_the_unsubscribe_headers(
    session, account_a, owner, monkeypatch
):
    """The other side of the same decision (A18).

    A drip is exactly what `List-Unsubscribe` is for. If these went out transactional, an
    unsubscribed customer would keep receiving marketing — which is the failure the whole
    suppression mechanism exists to prevent.
    """
    # The tokens are HMACs over the app secret, so the key must be present — without it
    # `unsubscribe_headers` raises, the drain's guard logs and continues, and the message goes
    # out with no headers. That guard is correct (a missing key must not strand every drip) and
    # it is exactly what would make this assertion silently untestable.
    monkeypatch.setenv("MIHOMES_SECRET_KEY", "k" * 43 + "=")

    provider, service = _service(session)

    with account_context(account_a):
        enrol(session, account_a, "onboarding", now=NOW)
        send_due_steps(session, service, now=NOW + ONBOARDING[0][1])
        rows = _queued(session, account_a)
        assert {row.klass for row in rows} == {"lifecycle"}

        # Drained well past real "now", not past the fixture's `NOW`. `_send` stamps
        # `next_attempt_at` from the **real** clock — the campaign schedule is injected, the
        # enqueue is not — so a drain at a NOW in the past selects nothing and this test would
        # assert on an empty list. The other drains in this file already run at +365d.
        service.drain(now=datetime.now(UTC) + timedelta(days=1))

    assert provider.sent
    assert provider.sent[0]["headers"], "a drip must carry RFC 8058 headers"


# --- the templates the placeholder sequence names ----------------------------------------------


@pytest.mark.parametrize("template", [t for t, _ in ONBOARDING])
def test_every_step_has_both_templates(template):
    """A sequence naming a template that does not exist fails at drain time, per account, in a
    nightly job — which is a bad place to discover a typo."""
    from mihomes.services.email.render import render_template

    subject, html, text = render_template(template, {"campaign": "onboarding", "step": 1})
    assert subject and html and text
