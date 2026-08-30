"""Gateway link tokens — hashing, and (at G3) the refusal matrix, single-use, cascade.

G1.2 lands `test_token_hashed_only` (A4). The rest of this file arrives with Step 3.

**A4 is a negative assertion, so it is paired with a positive one** (harness §0.5b). "The raw
code never reaches the table or a log record" is trivially satisfied by a function that returns
`""` and writes nothing at all — so every arm below also asserts the code was really minted and
really stored, in hashed form. Without that pairing the test would keep passing through a
regression that broke linking entirely.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text

from mihomes.models.gateway_link_token import GatewayLinkToken
from mihomes.models.membership import Membership
from mihomes.models.user import User
from mihomes.services.gateways.linking import (
    LinkingError,
    generate_code,
    issue_link_token,
)
from mihomes.services.invite_service import hash_token


@pytest.fixture
def membership_id(session, account_a):
    """A real membership to bind a code to.

    Built through the ORM models, following `test_membership.py::_member` — `users.google_sub`
    is NOT NULL, so a hand-rolled INSERT that omits it fails. The FK is the point: a code that
    could name a nonexistent membership would make G3.3's cascade meaningless.
    """
    user = User(
        id=uuid.uuid4(),
        google_sub=f"sub-{uuid.uuid4().hex[:12]}",
        email=f"linker-{uuid.uuid4().hex[:8]}@example.com",
    )
    session.add(user)
    session.flush()

    membership = Membership(
        id=uuid.uuid4(),
        account_id=account_a,
        user_id=user.id,
        role="admin",
        status="active",
    )
    session.add(membership)
    session.flush()
    return membership.id


def test_token_hashed_only(session, account_a, membership_id, caplog):
    """**A4** — a raw link token reaches neither the table nor a log record.

    The three assertions are ordered deliberately. The first two are POSITIVE: a code was
    minted, and a row exists carrying its hash. Only then does the negative arm mean anything —
    without them, `issue_link_token` returning `""` and writing nothing would sail through the
    "raw code is absent" check while linking was completely broken.

    The log arm reads every record's `getMessage()` **and** its raw `args`, because `%s`-style
    lazy formatting keeps the interpolated value out of `.message` until something renders it:
    a code smuggled in as an argument would be invisible to a `.message`-only scan and fully
    visible in the log file.
    """
    with caplog.at_level(logging.DEBUG):
        raw = issue_link_token(
            session, account_a, membership_id, gateway="telegram"
        )

    # --- positive: a real code was minted -------------------------------------------------
    assert raw, "issue_link_token returned nothing — the negative arms below would be vacuous"
    assert len(raw) >= 6, f"a {len(raw)}-character code is guessable within its lifetime"

    # --- positive: it was stored, hashed --------------------------------------------------
    row = session.execute(select(GatewayLinkToken)).scalar_one()
    assert row.token_hash == hash_token(raw), (
        "the stored hash does not match sha256(raw) — redemption could never find this row"
    )
    assert row.gateway == "telegram"
    assert row.membership_id == membership_id
    assert row.redeemed_at is None, "a freshly issued code must not be pre-redeemed"

    # --- negative: the raw code is nowhere ------------------------------------------------
    assert row.token_hash != raw, "the raw code is stored verbatim in token_hash"

    # Every text column on the row, checked by iterating the table rather than by naming
    # columns: a future column that quietly persists the raw code is exactly what a
    # hand-listed check would miss.
    stored = session.execute(
        text("SELECT * FROM gateway_link_tokens")
    ).mappings().one()
    leaked = [k for k, v in stored.items() if isinstance(v, str) and raw in v]
    assert not leaked, f"the raw code appears in columns: {leaked}"

    records = [
        f"{r.getMessage()} {r.args!r}" for r in caplog.records
    ]
    assert not [r for r in records if raw in r], (
        "the raw link code reached a log record. A link code is a bearer credential that "
        "grants write access to an estate; a leaked log line must be unusable"
    )
    # And the positive half of the log arm: something WAS logged, so the assertion above is
    # scanning real records rather than an empty list.
    assert any("link code issued" in r for r in records), (
        "no issuance was logged at all — the leak check above had nothing to scan"
    )


def test_generate_code_is_unguessable_and_readable():
    """The code is read aloud, retyped, and must still resist an online guess.

    Both halves matter and they pull against each other. Ambiguous glyphs are excluded so a
    transcription error does not become an unexplained refusal; the alphabet stays large enough
    that an 8-character code is not walkable inside its 15-minute, single-use life.
    """
    codes = {generate_code() for _ in range(200)}
    assert len(codes) > 190, "generate_code is not drawing from a wide enough space"

    forbidden = set("O0I1L")
    for code in codes:
        assert not (set(code) & forbidden), (
            f"{code!r} contains a glyph that is misread when spoken or retyped"
        )


def test_refusal_matrix(session, account_a, account_b, membership_id):
    """**A8 · G-refusals** — four refusals, four *distinct* messages, not one generic error.

    The gate is not "each case is refused" — a `redeem_link_token` that raised
    `LinkingError("nope")` for everything would satisfy that and be useless. It is that the four
    are **distinguishable**, because each asks the sender for a different next action: expired →
    ask for a new code; replayed → you are already linked or someone beat you to it;
    wrong-gateway → use the right app; unknown → check what you typed. Collapsing them turns
    every one into a support conversation.

    So this asserts three things a "four paths into one error" implementation fails: the
    exception types are pairwise distinct, the human-readable messages are pairwise distinct,
    and — the arm that catches the subtlest version — **a valid code still redeems**, so the
    refusals are not simply everything failing.
    """
    from mihomes.services.gateways.linking import (
        AlreadyRedeemed,
        ExpiredCode,
        UnknownCode,
        WrongGateway,
        redeem_link_token,
    )

    outcomes: dict[str, Exception] = {}

    # --- expired -------------------------------------------------------------------------
    expired_raw = issue_link_token(
        session, account_a, membership_id, gateway="telegram", ttl_minutes=1
    )
    expired = session.execute(
        select(GatewayLinkToken).where(
            GatewayLinkToken.token_hash == hash_token(expired_raw)
        )
    ).scalar_one()
    expired.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    session.flush()
    with pytest.raises(ExpiredCode) as e:
        redeem_link_token(
            session, gateway="telegram", sender_id="500000001", raw_token=expired_raw
        )
    outcomes["expired"] = e.value

    # --- replayed ------------------------------------------------------------------------
    replay_raw = issue_link_token(session, account_a, membership_id, gateway="telegram")
    redeem_link_token(
        session, gateway="telegram", sender_id="500000002", raw_token=replay_raw
    )
    with pytest.raises(AlreadyRedeemed) as e:
        redeem_link_token(
            session, gateway="telegram", sender_id="500000003", raw_token=replay_raw
        )
    outcomes["replayed"] = e.value

    # --- wrong gateway -------------------------------------------------------------------
    wa_raw = issue_link_token(session, account_a, membership_id, gateway="whatsapp")
    with pytest.raises(WrongGateway) as e:
        redeem_link_token(
            session, gateway="telegram", sender_id="500000004", raw_token=wa_raw
        )
    outcomes["wrong_gateway"] = e.value

    # --- cross-account / unknown ---------------------------------------------------------
    # A code that exists but belongs to nothing this sender may redeem. It must be
    # INDISTINGUISHABLE from "no such code": saying otherwise confirms a real code's existence
    # to someone not entitled to know it.
    with pytest.raises(UnknownCode) as e:
        redeem_link_token(
            session, gateway="telegram", sender_id="500000005", raw_token="NOTACODE"
        )
    outcomes["unknown"] = e.value

    # --- the gate itself: four distinct types AND four distinct messages ------------------
    types = {type(x) for x in outcomes.values()}
    assert len(types) == 4, (
        f"the four refusals collapse into {len(types)} exception type(s): "
        f"{sorted(t.__name__ for t in types)}"
    )
    messages = {str(x) for x in outcomes.values()}
    assert len(messages) == 4, (
        f"distinct types with duplicate text is the same defect one layer down: {messages}"
    )
    for label, exc in outcomes.items():
        assert str(exc).strip(), f"{label} refused with an empty message"

    # --- and the arm that stops all of the above being 'everything fails' -----------------
    good_raw = issue_link_token(session, account_a, membership_id, gateway="telegram")
    resolved = redeem_link_token(
        session, gateway="telegram", sender_id="500000006", raw_token=good_raw
    )
    assert resolved.account_id == account_a


def test_single_use(session, account_a, membership_id):
    """**A9** — a second redemption is refused rather than rebinding.

    The hijack this prevents: someone forwards a code *after* using it, and a rebinding
    implementation silently moves the link to whoever presents it second — the original person
    keeps talking to the bot and quietly stops being who the bot thinks they are.

    So the assertion is not merely "the second call raises". It is that the **first** sender
    still holds the link afterwards, which is what distinguishes refusing from rebinding.
    """
    from mihomes.services.gateways.linking import AlreadyRedeemed, redeem_link_token

    raw = issue_link_token(session, account_a, membership_id, gateway="telegram")

    first = redeem_link_token(
        session, gateway="telegram", sender_id="600000001", raw_token=raw
    )
    assert first.membership_id == membership_id

    with pytest.raises(AlreadyRedeemed):
        redeem_link_token(
            session, gateway="telegram", sender_id="600000002", raw_token=raw
        )

    # The link still belongs to the FIRST sender — the anti-hijack assertion.
    from mihomes.models.telegram_link import TelegramLink

    links = session.execute(
        select(TelegramLink).where(TelegramLink.account_id == account_a)
    ).scalars().all()
    assert [link.telegram_user_id for link in links] == [600000001], (
        "the second redemption rebound the link instead of being refused — a forwarded code "
        "just hijacked an existing link"
    )

    # And the token is marked used, by the sender who actually used it.
    token = session.execute(
        select(GatewayLinkToken).where(GatewayLinkToken.token_hash == hash_token(raw))
    ).scalar_one()
    assert token.redeemed_at is not None
    assert token.redeemed_by_sender == "600000001"


def test_cascade_revocation(session, account_a, membership_id):
    """**A10** — revoking a membership removes its gateway link, with no extra code.

    `ondelete=CASCADE` (shipped in `0007` for `telegram_links`, and in `0016` for the token
    table) is what makes TELEGRAM_PRD:158's *"revoking a membership implicitly revokes the
    link"* **structural** rather than something a code path has to remember. A promise kept by
    application code is a promise that survives exactly as long as nobody writes a second
    deletion path.

    Paired, as always: the link is asserted present before the delete, so a fixture that never
    created one could not pass this by having nothing to remove.
    """
    from mihomes.models.telegram_link import TelegramLink
    from mihomes.services.gateways.linking import redeem_link_token

    raw = issue_link_token(session, account_a, membership_id, gateway="telegram")
    redeem_link_token(
        session, gateway="telegram", sender_id="700000001", raw_token=raw
    )

    # --- positive: the link and a second, unredeemed token both exist ---------------------
    pending_raw = issue_link_token(session, account_a, membership_id, gateway="telegram")
    assert session.execute(
        select(TelegramLink).where(TelegramLink.membership_id == membership_id)
    ).scalars().all(), "no link to revoke — the assertion below would be vacuous"
    assert session.execute(
        select(GatewayLinkToken).where(
            GatewayLinkToken.token_hash == hash_token(pending_raw)
        )
    ).scalar_one_or_none() is not None

    # --- delete the membership; touch nothing else ----------------------------------------
    membership = session.get(Membership, membership_id)
    session.delete(membership)
    session.flush()

    # --- the link is gone, and so is the pending code -------------------------------------
    assert session.execute(
        select(TelegramLink).where(TelegramLink.membership_id == membership_id)
    ).scalars().all() == [], (
        "the gateway link outlived the membership it was keyed on — revoking access no longer "
        "closes the bot (TELEGRAM_PRD:158, A10)"
    )
    assert session.execute(
        select(GatewayLinkToken).where(
            GatewayLinkToken.token_hash == hash_token(pending_raw)
        )
    ).scalar_one_or_none() is None, (
        "an unredeemed code outlived its membership — redeeming it would resurrect access "
        "that was deliberately revoked"
    )


def test_an_unknown_gateway_is_refused(session, account_a, membership_id):
    """A code must name a channel this system actually serves.

    The sender-id namespaces are unrelated across gateways, so a code minted for a channel
    nothing redeems on is a credential that can only ever fail — better to refuse at issue
    time, where an operator sees it, than at redemption, where the sender does.
    """
    with pytest.raises(LinkingError, match="Unknown gateway"):
        issue_link_token(session, account_a, membership_id, gateway="carrier-pigeon")

    assert session.execute(select(GatewayLinkToken)).all() == [], (
        "a refused issue must write no row"
    )
