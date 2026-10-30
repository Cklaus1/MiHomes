"""Per-person Google Calendar sync (2026-10-30) — one connection per membership.

A person connects *their own* Google account from Settings; MiHomes then keeps a calendar it
created in that account ("MiHomes — <estate>") in step with the appointments and tasks that
person can see. One-way: nothing read from Google ever changes estate records.

Keyed on the **membership**, not the user, for the reason `telegram_links` is: revoking a
membership CASCADEs the connection away, so a removed member stops receiving estate events
structurally rather than by remembering to check. A person in two estates connects each one
separately and gets one calendar per estate.

**The refresh token is Fernet-encrypted** (`crypto.encrypt`, the same key as the config
table). It is a standing credential to someone's Google account; a `pg_dump` must not hand it
over. The short-lived access token is never stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, TenantOwned


class CalendarConnection(Base, TenantOwned):
    """One membership's link to one Google calendar."""

    __tablename__ = "calendar_connections"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memberships.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False, default="google")
    # The Google account it is connected to — shown in Settings so a person can tell which of
    # their Google accounts it is. Display only; not an identity.
    google_email: Mapped[str] = mapped_column(String(320), nullable=False)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)  # enc:v1:… (crypto.encrypt)
    calendar_id: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # active | needs_reconnect (Google refused the refresh token: revoked or expired)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Concurrency, without holding a lock across HTTP calls: a run claims `syncing_until` (a
    # lease that expires if the process dies), and anyone who asks for a sync while it runs sets
    # `needs_sync_at`, which the running sync re-checks before letting go.
    syncing_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    needs_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())

    __table_args__ = (
        Index("uq_calendar_connections_member", "account_id", "membership_id", unique=True),
    )


class CalendarEventLink(Base, TenantOwned):
    """Which Google event an appointment or task became, for one connection.

    `fingerprint` hashes the event body last written, so a sync can tell "unchanged" from
    "edited" without asking Google — whatever path changed the task (complete, roll forward,
    import, Telegram), the next sync notices.
    """

    __tablename__ = "calendar_event_links"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=new_id)
    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("calendar_connections.id", ondelete="CASCADE"),
        nullable=False)
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # appointment | task
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    google_event_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("account_id", "connection_id", "entity_type", "entity_id",
                         name="uq_calendar_event_links_entity"),
    )
