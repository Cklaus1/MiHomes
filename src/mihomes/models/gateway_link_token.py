"""GatewayLinkToken — SPEC-006 §4.1, Step 1 (A3, A4).

A short-lived code an owner/admin issues so a sender can bind their chat identity to a
membership (TELEGRAM_PRD's `/link <code>` flow).

**Hashed, never stored raw** — the same discipline as SPEC-001 N7's confirmation tokens and
SPEC-003's invite tokens. A link code is a bearer credential that grants write access to an
estate; a leaked log line must not be usable. A4 asserts the raw code reaches neither this
table nor a log record.

**Keyed on `memberships`, never on `Staff`** — the same reasoning `telegram_link.py` states at
length (D19/N6): `memberships.role` is the capability matrix's `owner`/`admin`/`staff`
vocabulary, while `StaffRole` is a *job* enum with its own `OWNER`. Binding through `Staff` and
then applying a matrix decision would silently cross the two. The membership is also chosen at
*issue* time, so redeeming cannot escalate to a different role than the issuer intended (D3).

`ondelete="CASCADE"` on that FK is what makes G3.3's A10 — *"revoking a membership removes its
gateway link with no extra code"* — structural rather than something a code path has to
remember, exactly as `TelegramLink` already does it.

Three deviations from §4.1 as written, each measured rather than assumed:

1. **`PGUUID(as_uuid=True)` PKs and FKs, not `String(36)`.** The harness's §0.6 C8 calls this
   out: every SPEC-003+ model in this repo uses `PGUUID`, and `memberships.id` is a `PGUUID`,
   so a `String(36)` FK would not even build. Following the shipped pattern (N9's spirit).
2. **The `membership_id` FK is declared here; §4.2's DDL omits it entirely.** Without the FK
   there is no CASCADE, and A10 would need application code to hold a promise the schema is
   supposed to keep.
3. **No `EXPECTED_NON_LEADING` entry for `uq_gateway_link_token_hash`, which C8 predicted.**
   Measured: `UniqueConstraint` in `__table_args__` emits a *constraint*, not an index, so
   `test_tenant_indexes._tenant_indexes()` — which iterates `table.indexes` — never sees it,
   and the entry would be stale on arrival and fail
   `test_every_declared_exception_still_exists`. The invite precedent needed one only because
   `invite.py:43` declares `unique=True, index=True`, which *does* emit an index. The unique is
   still on `token_hash` alone, for C8's stated reason: redemption looks a token up before any
   account is known (§4.2's carve-out), so a composite `(account_id, token_hash)` would leave
   the only query this table exists to serve unindexed — and would let two accounts mint the
   same hash.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class GatewayLinkToken(Base, TenantOwned):
    """A hashed, single-use, short-lived code binding a chat sender to a membership.

    `TenantOwned` because the token belongs to the account issuing it: an operator listing one
    account's outstanding codes must not see another's. `account_id` arrives from the mixin
    already carrying `index=True`, so `ix_gateway_link_tokens_account_id` leads with
    `account_id` and needs nothing added here.
    """

    __tablename__ = "gateway_link_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )

    # The membership this code binds to — chosen at issue time so redemption cannot escalate
    # to a role the issuer did not intend (D3). CASCADE makes A10 structural.
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False,
    )

    # "telegram" | "whatsapp". A code issued for one channel must not redeem on another: the
    # sender-id namespaces are unrelated and a collision would bind the wrong human. G3.1's
    # refusal matrix asserts a wrong-gateway code fails with its own distinct message.
    gateway: Mapped[str] = mapped_column(String(20), nullable=False)

    # SHA-256 hex. Never the raw code (A4).
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Single-use. Set on redemption; a second attempt is refused rather than rebinding, so a
    # forwarded code cannot hijack an existing link (A9).
    redeemed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    redeemed_by_sender: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        # On `token_hash` alone — see deviation 3 in the module docstring.
        UniqueConstraint("token_hash", name="uq_gateway_link_token_hash"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        # Deliberately prints neither the hash nor anything derived from the raw code.
        return f"<GatewayLinkToken gateway={self.gateway} redeemed={self.redeemed_at is not None}>"
