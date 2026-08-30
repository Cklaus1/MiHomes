"""Gateway link tokens — SPEC-006 §5.2, Steps 1 and 3.

`issue_link_token` mints a short-lived code an owner/admin reads out to someone, and
`redeem_link_token` (Step 3, G3) binds that person's chat identity to the membership the code
was issued for.

**Only the hash is ever stored** (A4). The raw code is returned to the caller exactly once, for
display, and never written to the table or to a log record. That is the same discipline
`invite_service.hash_token` already applies to invite tokens, and this module reuses that
helper rather than growing a second hashing convention: a link code is the same kind of thing —
a bearer credential that grants write access to an estate.

The code is short enough to read aloud over the phone but drawn from `secrets`, so it is not
guessable within its 15-minute life. A6's fail-closed rule is what makes that lifetime safe:
an unlinked sender is refused, never defaulted into an account, so a code that expires unused
costs a re-issue rather than a silent cross-tenant write.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from mihomes.models.gateway_link_token import GatewayLinkToken
from mihomes.models.membership import Membership
from mihomes.models.telegram_link import TelegramLink
from mihomes.services.gateways.identity import ResolvedSender
from mihomes.services.invite_service import hash_token

logger = logging.getLogger(__name__)

#: Channels a code may be issued for. A code issued for one must not redeem on another: the
#: sender-id namespaces are unrelated and a collision would bind the wrong human (G3.1).
SUPPORTED_GATEWAYS = ("telegram", "whatsapp")

#: The human-readable alphabet. No `O`/`0`, `I`/`1`/`l` — a code is read aloud or retyped, and
#: a transcription error costs a re-issue and an unexplained refusal.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LENGTH = 8

DEFAULT_TTL_MINUTES = 15


class LinkingError(Exception):
    """A link code could not be issued or redeemed. Carries a caller-safe message.

    Caller-safe matters here: the refusal text is sent back over the very gateway the sender
    reached us on, so it must never carry an exception's raw detail — the same rule M27 already
    enforces for dispatch errors.
    """


class ExpiredCode(LinkingError):
    """The code was valid but its 15 minutes are up. Ask the issuer for a fresh one."""


class AlreadyRedeemed(LinkingError):
    """Single-use, and this one is used (A9).

    **Refused rather than rebinding**, which is the whole point: a code forwarded after
    redemption must not silently move an existing link to whoever presents it second.
    """


class WrongGateway(LinkingError):
    """A code minted for one channel, presented on another.

    The sender-id namespaces are unrelated across gateways, so honouring this would bind the
    wrong human on a numeric collision.
    """


class UnknownCode(LinkingError):
    """No such code — mistyped, already consumed long ago, or never existed.

    Deliberately **cannot** distinguish "never existed" from "belongs to another account": the
    lookup is by hash across all accounts (§4.2's carve-out), and telling a stranger that their
    guess matched a real code somewhere else would confirm the code's existence to someone who
    is not entitled to know it.
    """


class AlreadyLinked(LinkingError):
    """This sender already holds a link in that account (D5's unique constraint).

    Refused with a clear message rather than surfacing the `IntegrityError` the constraint
    would raise anyway (§5.2), because the sender sees this text.
    """


def generate_code() -> str:
    """A fresh raw link code. Never stored — the caller displays it and drops it.

    `secrets.choice` over a 31-character alphabet at length 8 is ~39 bits. That is weak for a
    long-lived credential and ample for one that dies in 15 minutes and is single-use, where an
    online guess must also hit the right gateway and an unredeemed miss leaves no link at all.
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LENGTH))


def issue_link_token(
    session: Session,
    account_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    gateway: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> str:
    """Mint a code for `membership_id` and return the RAW value, once (§5.2).

    The membership is chosen **here**, at issue time, so redemption cannot escalate to a
    different role than the issuer intended (D3). Authorization for this call is the caller's
    job — `can(account, "gateway.link.issue")`, owner/admin only.

    Returns the raw code. The row stores `sha256(raw)` and nothing else derived from it, which
    is what A4 asserts: the raw string must appear in neither the table nor a log record.
    """
    if gateway not in SUPPORTED_GATEWAYS:
        raise LinkingError(
            f"Unknown gateway {gateway!r}. Expected one of: {', '.join(SUPPORTED_GATEWAYS)}."
        )
    if ttl_minutes <= 0:
        raise LinkingError("A link code's lifetime must be positive.")

    raw = generate_code()
    token = GatewayLinkToken(
        account_id=account_id,
        membership_id=membership_id,
        gateway=gateway,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )
    session.add(token)
    session.flush()

    # Deliberately logs the membership and gateway and NOT the code, nor its hash. A4's log
    # assertion is the reason this line names what it names: an operator debugging a failed
    # link needs to know a code was issued and for whom, never what it was.
    logger.info(
        "gateway link code issued: gateway=%s membership=%s expires=%s",
        gateway,
        membership_id,
        token.expires_at.isoformat(),
    )
    return raw


def redeem_link_token(
    session: Session,
    *,
    gateway: str,
    sender_id: str,
    raw_token: str,
) -> ResolvedSender:
    """Bind `sender_id` to the token's membership. Single-use, expiry-checked (§5.2).

    **The lookup is by hash across all accounts, on an unscoped session** — the same carve-out
    `identity.resolve_sender` documents, and for the same reason: redemption runs *before* an
    account is known, because discovering it is what redemption does. That is why the unique
    is on `token_hash` alone (§4.2).

    **Four refusals, four distinct exceptions** (A8). They are separate types rather than one
    generic error because each asks the sender for a different next action: `ExpiredCode` means
    ask for a new code, `AlreadyRedeemed` means you are already linked or someone beat you to
    it, `WrongGateway` means use the right app, and `UnknownCode` means check what you typed.
    Collapsing them into "linking failed" makes every one of those a support conversation.

    `UnknownCode` deliberately does not distinguish "no such code" from "a code belonging to an
    account you have nothing to do with": both are a hash that this sender may not redeem, and
    saying which would confirm a real code's existence to someone not entitled to know it.

    Raises:
        UnknownCode, ExpiredCode, AlreadyRedeemed, WrongGateway, AlreadyLinked
    """
    if gateway not in SUPPORTED_GATEWAYS:
        raise LinkingError(
            f"Unknown gateway {gateway!r}. Expected one of: {', '.join(SUPPORTED_GATEWAYS)}."
        )

    token = session.execute(
        select(GatewayLinkToken).where(
            GatewayLinkToken.token_hash == hash_token(raw_token or "")
        )
    ).scalar_one_or_none()

    if token is None:
        raise UnknownCode("That code is not valid. Check it and try again.")

    # Gateway is checked BEFORE expiry and redemption, deliberately: a code presented on the
    # wrong channel is a category error about the code itself, and answering "expired" would
    # send the sender to ask for a replacement that would fail exactly the same way.
    if token.gateway != gateway:
        raise WrongGateway(
            f"That code was issued for {token.gateway}, not {gateway}."
        )

    if token.redeemed_at is not None:
        raise AlreadyRedeemed("That code has already been used.")

    expires_at = token.expires_at
    if expires_at.tzinfo is None:  # a naive column read on some backends
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        raise ExpiredCode("That code has expired. Ask for a new one.")

    membership = session.get(Membership, token.membership_id)
    if membership is None or membership.status != "active":
        # The membership was revoked between issue and redemption. CASCADE covers deletion;
        # this covers the status change, exactly as `identity.resolve_sender` does.
        raise UnknownCode("That code is not valid. Check it and try again.")

    if gateway == "telegram":
        try:
            telegram_user_id = int(sender_id)
        except (TypeError, ValueError):
            raise LinkingError("That sender id is not valid for Telegram.") from None

        existing = session.execute(
            select(TelegramLink).where(
                TelegramLink.account_id == token.account_id,
                TelegramLink.telegram_user_id == telegram_user_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            # D5's unique constraint would raise anyway; refusing here gives the sender a
            # sentence they can act on instead of an IntegrityError.
            raise AlreadyLinked("You are already linked in that account.")

        session.add(
            TelegramLink(
                account_id=token.account_id,
                membership_id=token.membership_id,
                telegram_user_id=telegram_user_id,
            )
        )
    else:  # pragma: no cover - WhatsApp links arrive with the Cloud API at Step 7
        raise LinkingError(f"Linking is not yet available for {gateway}.")

    # Mark used BEFORE returning, in the same transaction as the link insert: single-use is a
    # property of the pair, and a crash between them would leave a live code and a live link.
    token.redeemed_at = datetime.now(timezone.utc)
    token.redeemed_by_sender = str(sender_id)[:100]
    session.flush()

    logger.info(
        "gateway link redeemed: gateway=%s membership=%s account=%s",
        gateway,
        token.membership_id,
        token.account_id,
    )
    return ResolvedSender(
        account_id=token.account_id,
        membership_id=token.membership_id,
        role=membership.role,
    )
