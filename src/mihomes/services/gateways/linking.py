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

from sqlalchemy.orm import Session

from mihomes.models.gateway_link_token import GatewayLinkToken
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
