"""Playbook model — an account's own operational playbooks (2026-10-21).

Playbooks were markdown files in `knowledge/playbooks/`, read by every account: one estate's
internal procedures (hiring, offboarding, emergency) were visible to every signup. They are now
tenant rows like everything else, written in the app by owners, admins and staff the owner
grants (`Membership.can_edit_playbooks`). New accounts start empty and copy from the generic
template gallery (`mihomes/playbook_templates/`), which is package data, not tenant data.

`content` stays markdown with `- [ ] step` checklist lines: that is what the editor writes, what
the detail page renders, and what "Run" turns into tasks — one format end to end.
"""

import uuid

from sqlalchemy import String, Text, UniqueConstraint
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
