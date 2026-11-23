"""EmailPreference — one member's on/off switch for one optional email (2026-11-23).

**No row means the default**, which comes from the household's team settings
(`services/email/preferences.py`), so a new member needs no rows written for them. A row exists
only once someone flips a switch on the Emails page.

The chat digest is the one optional email whose switch is NOT stored here: it predates this
table and lives in `chat_notify_prefs.email_digest`, next to the push switch it shares a job
with. `preferences.py` reads and writes both, so nothing outside it needs to know.

ACCOUNT_SHARED, like `chat_notify_prefs`: every role edits their own row; the route enforces
`membership_id == principal's`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class EmailPreference(Base, TenantOwned):
    __tablename__ = "email_preferences"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False)
    email_type: Mapped[str] = mapped_column(String(40), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("uq_email_preferences_member_type", "account_id", "membership_id", "email_type",
              unique=True),
    )
