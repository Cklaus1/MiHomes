"""Playbook models — an account's own operational playbooks (2026-10-21).

Playbooks were markdown files in `knowledge/playbooks/`, read by every account: one estate's
internal procedures (hiring, offboarding, emergency) were visible to every signup. They are now
tenant rows like everything else, written in the app by owners, admins and staff the owner
grants (`Membership.can_edit_playbooks`). New accounts start empty and copy from the generic
template gallery (`mihomes/playbook_templates/`), which is package data, not tenant data.

`content` is markdown. A `- [ ] step` line renders as a tick box people can tick while they read
the playbook (2026-10-22) — a playbook is a document, not a task template, so nothing here
creates tasks. Who has ticked what is `PlaybookCheck`: one shared checklist per playbook.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class Playbook(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "playbooks"
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="uq_playbooks_account_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # one-off | recurring | emergency — free text so an account can use its own words.
    kind: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Who runs it ("Housekeeper", "All staff") — a description, not a role grant.
    audience: Mapped[str | None] = mapped_column(String(200), nullable=True)
    estimated_time: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")


class PlaybookCheck(Base, TenantOwned):
    """One ticked step of a playbook. A row means "ticked"; no row means "not ticked".

    **Shared by the whole account** (user decision 2026-10-22): the playbook is one live
    checklist, like a clipboard on the wall — when the housekeeper ticks a step, the manager sees
    it, with who and when. A row per tick rather than a JSON column on `playbooks`, so two people
    ticking different steps at the same moment cannot overwrite each other, and ticking never
    bumps the playbook's own `updated_at`.

    `item_key` identifies a step by its text (see `services.playbook.step_keys`), not its
    position, so reordering keeps the ticks and editing a step's wording drops that one tick.
    """

    __tablename__ = "playbook_checks"
    __table_args__ = (
        UniqueConstraint("playbook_id", "item_key", name="uq_playbook_checks_playbook_item"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    # No separate index: `uq_playbook_checks_playbook_item (playbook_id, item_key)` already leads
    # with `playbook_id`, and the tenant-index rule forbids a non-`account_id`-leading index.
    playbook_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("playbooks.id", ondelete="CASCADE"), nullable=False,
    )
    item_key: Mapped[str] = mapped_column(String(40), nullable=False)
    checked_by: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
