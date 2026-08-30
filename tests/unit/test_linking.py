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
