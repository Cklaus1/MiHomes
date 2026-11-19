"""G2 · §6 Step 2 — sender identity, the tenancy boundary (A5, A6, A7).

**A6 is the whole stake of the phase.** A gateway without tenancy does not fail closed, it fails
into the wrong account — so "an unlinked sender is never defaulted" is the assertion everything
else rests on.

It is also a **negative** assertion, and §0.5b's rule applies with unusual force here: "never
defaulted" is vacuously true of a `resolve_sender` that raises unconditionally, or that cannot
resolve anybody at all. Every negative arm below is therefore paired with a positive one **in
the same test**, so a regression that breaks resolution entirely turns the test red instead of
greener.
"""

from __future__ import annotations

import uuid

import pytest

from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink
from mihomes.models.user import User
from mihomes.services.gateways.identity import (
    AmbiguousSender,
    ResolvedSender,
    UnlinkedSender,
    resolve_sender,
)


def _user(session) -> User:
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"sender-{uuid.uuid4().hex[:8]}@example.com",
    )
    session.add(user)
    session.flush()
    return user


def _link(session, account_id, telegram_user_id, *, role="staff", status="active", user=None):
    """A sender linked into `account_id`, with the membership behind it.

    Returns the membership so a test can revoke it — A5's docstring promises revocation closes
    the bot the same way it closes the web app, and that is only checkable if the caller can
    reach the row.
    """
    user = user or _user(session)
    membership = Membership(
        id=uuid.uuid4(),
        account_id=account_id,
        user_id=user.id,
        role=role,
        status=status,
    )
    session.add(membership)
    session.flush()

    session.add(
        TelegramLink(
            id=uuid.uuid4(),
            account_id=account_id,
            membership_id=membership.id,
            telegram_user_id=telegram_user_id,
        )
    )
    session.flush()
    return membership


def test_resolves_single_account(session, account_a):
    """**A5** — a linked sender resolves to exactly one account.

    Asserts the `role` comes from `memberships.role` and not from `StaffRole` (D3/N7). The two
    vocabularies both contain "owner", so a test that only checked *some* role resolved would
    pass while the bot handed a housekeeping `StaffRole.OWNER` the account owner's powers.
    """
    membership = _link(session, account_a, 111222333, role="admin")

    resolved = resolve_sender(session, gateway="telegram", sender_id="111222333")

    assert isinstance(resolved, ResolvedSender)
    assert resolved.account_id == account_a
    assert resolved.membership_id == membership.id
    assert resolved.role == "admin", (
        "role must come from memberships.role — the capability matrix's vocabulary (D3/N7)"
    )


def test_unlinked_fails_closed(session, account_a):
    """**A6** — an unlinked sender raises and is never defaulted to an account.

    **The negative and the positive are in the same test deliberately** (§0.5b). The positive
    arm runs first: a linked sender in `account_a` resolves. Without it, a `resolve_sender` that
    raised for *everyone* — the total-outage regression — would satisfy the negative arm
    perfectly and this test would report the phase's definition of done as green.
    """
    linked = _link(session, account_a, 444555666)

    # --- positive: resolution genuinely works ---------------------------------------------
    resolved = resolve_sender(session, gateway="telegram", sender_id="444555666")
    assert resolved.account_id == account_a
    assert resolved.membership_id == linked.id

    # --- negative: a stranger is refused, not defaulted ------------------------------------
    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="telegram", sender_id="999888777")


def test_an_unlinked_sender_is_refused_even_when_exactly_one_account_exists(
    session, account_a
):
    """The tempting default, refused explicitly (D12/N2).

    One account in the database makes "just use that one" look obviously right, and it is the
    exact shape of the bug: correct for one tenant, a silent cross-account write for many. The
    failure never surfaces as an error — the sender gets a normal confirmation.

    This is the arm a `resolve_default_property`-style fallback would break, and nothing else
    here would notice.
    """
    _link(session, account_a, 121212121)

    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="telegram", sender_id="343434343")


def test_a_revoked_membership_no_longer_resolves(session, account_a):
    """Revoking access closes the bot, the same way it closes the web app.

    Paired again: the sender resolves *before* revocation and not after, so the assertion is
    about the revocation rather than about the fixture never having worked.

    `ondelete=CASCADE` covers deletion; this covers the status change, which the cascade cannot
    see. Both are needed — a revoked member whose row still exists would otherwise keep a
    working bot.
    """
    membership = _link(session, account_a, 565656565)

    assert resolve_sender(
        session, gateway="telegram", sender_id="565656565"
    ).account_id == account_a

    membership.status = "revoked"
    session.flush()

    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="telegram", sender_id="565656565")


def test_multi_account_sender(session, account_a, account_b):
    """**A7** — linked in two accounts: resolves by chat, refuses a DM as ambiguous.

    Legitimate under D5 — the same person may be a member of two estates and reach the bot in
    both. The setup is asserted before the refusal is: "refused as ambiguous" would be vacuous
    if the two-account fixture had silently produced one link, or none.
    """
    sender_id = 787878787
    user = _user(session)
    in_a = _link(session, account_a, sender_id, role="owner", user=user)
    in_b = _link(session, account_b, sender_id, role="staff", user=user)

    # --- positive: both links really exist, in different accounts -------------------------
    assert in_a.account_id == account_a
    assert in_b.account_id == account_b
    assert in_a.id != in_b.id

    # --- positive: a group message resolves BY CHAT, to each account in turn ---------------
    from_a = resolve_sender(
        session, gateway="telegram", sender_id=str(sender_id), chat_account_id=account_a
    )
    assert from_a.account_id == account_a
    assert from_a.role == "owner"

    from_b = resolve_sender(
        session, gateway="telegram", sender_id=str(sender_id), chat_account_id=account_b
    )
    assert from_b.account_id == account_b
    assert from_b.role == "staff", (
        "the same human holds different roles in the two accounts, and each message must be "
        "answered with the role for the account it arrived in"
    )

    # --- negative: a DM names no account, and is refused rather than guessed ---------------
    with pytest.raises(AmbiguousSender) as excinfo:
        resolve_sender(session, gateway="telegram", sender_id=str(sender_id))

    assert set(excinfo.value.account_ids) == {account_a, account_b}, (
        "the disambiguation prompt needs to name the candidate accounts"
    )


def test_a_chat_in_an_account_the_sender_is_not_linked_to_is_refused(
    session, account_a, account_b
):
    """Linked *somewhere* is not linked *here* (D12).

    The sharp case: the sender has a real, active link — just not in this chat's account.
    Answering from one of their other accounts would be exactly the cross-account write this
    phase exists to prevent, and it would look completely normal to everyone involved.
    """
    _link(session, account_a, 909090909)

    with pytest.raises(UnlinkedSender):
        resolve_sender(
            session,
            gateway="telegram",
            sender_id="909090909",
            chat_account_id=account_b,
        )


def test_an_unknown_gateway_never_resolves_against_the_telegram_table(session, account_a):
    """A WhatsApp sender id must not be matched against Telegram links (N7's shape).

    The namespaces are unrelated, so a numeric collision would bind the wrong human — the same
    hazard the per-gateway check in `linking.py` guards at issue time. Refusing is the
    fail-closed answer until Step 7 brings the Cloud API's own link store.
    """
    _link(session, account_a, 131313131)

    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="whatsapp", sender_id="131313131")


def test_the_fail_closed_arms_would_catch_a_defaulting_resolver(session, account_a):
    """**A permanent mutation check**: prove the A6 arms have teeth, not just green ticks.

    Every assertion above passed on the first run. For a security boundary that is a warning
    rather than a reassurance — SPEC-002 had two of four security arms with no teeth until each
    was mutated — so this pins down *why* they pass.

    Mutating `identity.py` in place and re-running would prove it once, in a working tree, and
    leave nothing behind. Instead the defect is written out as a function: `_defaulting_resolve`
    is `resolve_sender` with exactly the D12 bug — an unlinked sender silently falls back to the
    only account in the database. The test asserts the *contract* rejects it.

    If someone later relaxes `resolve_sender` into this shape, `test_unlinked_fails_closed` goes
    red — and this test documents, in code, precisely which shape that is.
    """
    from sqlalchemy import text

    def _defaulting_resolve(sess, *, gateway, sender_id, chat_account_id=None):
        """`resolve_sender`, but with the tempting default. This is the bug, spelled out."""
        try:
            return resolve_sender(
                sess,
                gateway=gateway,
                sender_id=sender_id,
                chat_account_id=chat_account_id,
            )
        except UnlinkedSender:
            row = sess.execute(text("SELECT id FROM accounts LIMIT 1")).first()
            return ResolvedSender(
                account_id=row.id, membership_id=uuid.uuid4(), role="staff"
            )

    from _pytest.outcomes import Failed

    _link(session, account_a, 246813579)

    # The real function refuses a stranger...
    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="telegram", sender_id="111111111")

    # ...while the defaulting variant hands them somebody's estate, with no error anywhere.
    # Setup check: the mutation must genuinely produce an account, or the assertion below
    # would be testing nothing.
    leaked = _defaulting_resolve(session, gateway="telegram", sender_id="111111111")
    assert leaked.account_id is not None

    # **The teeth claim itself.** Not "the mutation works" — that is the setup — but "the
    # assertion `test_unlinked_fails_closed` makes would FAIL against it". Without this, the
    # test would pass even if `test_unlinked_fails_closed` were deleted outright.
    with pytest.raises(Failed):
        with pytest.raises(UnlinkedSender):
            _defaulting_resolve(session, gateway="telegram", sender_id="111111111")


def test_a_malformed_sender_id_is_refused_not_crashed(session, account_a):
    """A non-numeric Telegram id refuses like any other miss, rather than raising ValueError.

    An unhandled `ValueError` at the transport edge is an unhandled exception in the webhook
    handler, which providers retry aggressively — turning one malformed message into a hot
    loop. `UnlinkedSender` is the same answer the lookup would give, reached without a query.
    """
    with pytest.raises(UnlinkedSender):
        resolve_sender(session, gateway="telegram", sender_id="not-a-number")
