"""SQLAlchemy models — Base class and shared mixins."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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


class SlugMixin:
    """Adds a slug column for human-friendly identification."""

    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )


# Import all models so Alembic and Base.metadata.create_all() see them.
# Add new model imports here as they are created.
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
from mihomes.models.issue import Issue  # noqa: E402, F401
from mihomes.models.note import Note  # noqa: E402, F401
from mihomes.models.property import Property  # noqa: E402, F401
from mihomes.models.recurring_expense import RecurringExpense  # noqa: E402, F401
from mihomes.models.space import Space  # noqa: E402, F401
from mihomes.models.staff import Staff  # noqa: E402, F401
from mihomes.models.staff_pto import StaffPTORequest  # noqa: E402, F401
from mihomes.models.tag import Tag, TagAssignment  # noqa: E402, F401
from mihomes.models.task import Task, TaskSchedule  # noqa: E402, F401
from mihomes.models.template import Template, TemplateItem  # noqa: E402, F401
from mihomes.models.vendor import Vendor  # noqa: E402, F401
from mihomes.models.vendor_rating import VendorRating  # noqa: E402, F401

# GLOBAL table, Phase 0 only (SPEC-001 D4) — owned by the alembic_landing/ tree, not
# alembic/. It is listed in alembic/env.py's _UNMANAGED_TABLES so the single-user
# product's autogenerate ignores a table it does not own.
from mihomes.models.waitlist import Waitlist  # noqa: E402, F401
from mihomes.models.work_order import WorkOrder  # noqa: E402, F401
from mihomes.models.zone import Zone  # noqa: E402, F401
