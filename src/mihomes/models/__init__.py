"""SQLAlchemy models — Base class and shared mixins."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, func
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
        default=None,
        onupdate=lambda: datetime.now(timezone.utc),
    )


class SlugMixin:
    """Adds a slug column for human-friendly identification."""

    slug: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )


# Import all models so Alembic and Base.metadata.create_all() see them.
# Add new model imports here as they are created.
from mihomes.models.audit_log import AuditLog  # noqa: E402, F401
from mihomes.models.property import Property  # noqa: E402, F401
from mihomes.models.space import Space  # noqa: E402, F401
from mihomes.models.staff import Staff  # noqa: E402, F401
from mihomes.models.vendor import Vendor  # noqa: E402, F401
from mihomes.models.task import Task, TaskSchedule  # noqa: E402, F401
from mihomes.models.issue import Issue  # noqa: E402, F401
from mihomes.models.budget import Budget, Transaction  # noqa: E402, F401
from mihomes.models.note import Note  # noqa: E402, F401
from mihomes.models.configuration import Configuration  # noqa: E402, F401
from mihomes.models.alert import Alert  # noqa: E402, F401
