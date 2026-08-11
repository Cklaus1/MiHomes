"""SQLAlchemy models — Base class and shared mixins."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    """Adds created_at and updated_at columns to any model."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )


class TenantOwned:
    """Marks a model as belonging to exactly one account (SPEC-002 §4.1).

    `account_id` is denormalized onto child tables too, not just aggregate roots:
    RLS is enforced per-table and cannot cheaply join to a parent to discover the
    owner. That denormalization is what G4's drift guard then has to protect —
    a child whose account_id diverges from its parent's is the cost of this design.

    **Subclassing this is not sufficient on its own.** Two association tables
    (`staff_properties`, `vendor_properties`) are Core `Table` objects with no
    declarative class, so a `@declared_attr` mixin cannot reach them. The tenancy
    registry in `mihomes.tenancy.registry` enumerates them explicitly — see the
    note there, because A1/A21 iterating `__subclasses__()` alone would report
    green over a real cross-tenant leak.
    """

    @declared_attr
    def account_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(
            PGUUID(as_uuid=True),
            ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )


class SlugMixin:
    """Adds a slug column for human-friendly identification.

    NOTE: `unique=True` is deliberately absent. Under multitenancy uniqueness is
    per-account, enforced per-table via __table_args__:

        UniqueConstraint("account_id", "slug", name="uq_<table>_account_slug")

    It cannot live on the mixin column — a mixin cannot see account_id's table.
    A global unique here would mean the *second* account to create a "main-house"
    property gets an IntegrityError (SPEC-002 §4.1).
    """

    slug: Mapped[str] = mapped_column(String(100), nullable=False, index=True)


# Import all models so Alembic and Base.metadata.create_all() see them.
# Add new model imports here as they are created.
# Identity and tenancy (SPEC-002 §4.2). `accounts` first: every TenantOwned model
# carries a ForeignKey to it, so it must be defined before they are configured.
# `users` and `sessions` are GLOBAL (D3) — read before account context exists.
from mihomes.models.account import Account  # noqa: E402, F401
from mihomes.models.ai_conversation import AIConversation  # noqa: E402, F401
from mihomes.models.alert import Alert  # noqa: E402, F401
from mihomes.models.appointment import Appointment  # noqa: E402, F401
from mihomes.models.asset import Asset, PriceEntry  # noqa: E402, F401
from mihomes.models.audit_log import AuditLog  # noqa: E402, F401
from mihomes.models.book import Book  # noqa: E402, F401
from mihomes.models.budget import Budget, Transaction  # noqa: E402, F401
from mihomes.models.configuration import Configuration  # noqa: E402, F401
from mihomes.models.consumable import Consumable, ConsumablePriceEntry  # noqa: E402, F401
from mihomes.models.contract import Contract  # noqa: E402, F401
from mihomes.models.document import Document  # noqa: E402, F401
from mihomes.models.event import Event, EventGuest, Guest  # noqa: E402, F401
from mihomes.models.insurance import InsurancePolicy  # noqa: E402, F401
from mihomes.models.invite import Invite  # noqa: E402, F401
from mihomes.models.issue import Issue  # noqa: E402, F401
from mihomes.models.membership import (  # noqa: E402, F401
    Membership,
    MembershipPropertyScope,
)
from mihomes.models.note import Note  # noqa: E402, F401
from mihomes.models.property import Property  # noqa: E402, F401
from mihomes.models.recurring_expense import RecurringExpense  # noqa: E402, F401
from mihomes.models.session import Session  # noqa: E402, F401
from mihomes.models.space import Space  # noqa: E402, F401
from mihomes.models.staff import Staff  # noqa: E402, F401
from mihomes.models.staff_pto import StaffPTORequest  # noqa: E402, F401
from mihomes.models.tag import Tag, TagAssignment  # noqa: E402, F401
from mihomes.models.task import Task, TaskSchedule  # noqa: E402, F401
from mihomes.models.template import Template, TemplateItem  # noqa: E402, F401
from mihomes.models.user import User  # noqa: E402, F401
from mihomes.models.vendor import Vendor  # noqa: E402, F401
from mihomes.models.vendor_rating import VendorRating  # noqa: E402, F401

# GLOBAL table, Phase 0 only (SPEC-001 D4) — owned by the alembic_landing/ tree, not
# alembic/. It is listed in alembic/env.py's _UNMANAGED_TABLES so the single-user
# product's autogenerate ignores a table it does not own.
from mihomes.models.waitlist import Waitlist  # noqa: E402, F401
from mihomes.models.work_order import WorkOrder  # noqa: E402, F401
from mihomes.models.zone import Zone  # noqa: E402, F401
