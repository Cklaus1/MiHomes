"""Task and TaskSchedule models."""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class TaskPriority(str, enum.Enum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RecurrenceFrequency(str, enum.Enum):
    ONCE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    SEASONAL = "seasonal"
    ANNUAL = "annual"


class TaskCategory(str, enum.Enum):
    # Mechanical / Infrastructure
    PLUMBING = "plumbing"
    HVAC = "hvac"
    ELECTRICAL = "electrical"
    FUEL_GAS = "fuel-gas"
    # Safety
    FIRE_SAFETY = "fire-safety"
    SECURITY = "security"
    HEALTH_ENV = "health-environmental"
    # Exterior
    EXTERIOR = "exterior"
    LANDSCAPING = "landscaping"
    # Interior
    INTERIOR = "interior"
    # Appliances & Equipment
    APPLIANCES = "appliances"
    # Operations
    OPERATIONS = "operations"
    # Strategic / Planning
    STRATEGIC = "strategic"
    # Catch-all
    GENERAL = "general"


class Task(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="uq_tasks_account_slug"),
        Index("ix_tasks_account_due", 'account_id', 'due_date'),
        Index("ix_tasks_account_zone", 'account_id', 'zone_id'),
        Index("ix_tasks_account_category", 'account_id', 'category'),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id"), nullable=True)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    gcal_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    zone_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("zones.id"), nullable=True)
    category: Mapped[TaskCategory | None] = mapped_column(Enum(TaskCategory), nullable=True)
    # A completed ONE-TIME task the user cleared off the list (0020). Hidden from task views but
    # kept, so history and reports are unaffected. Repeating tasks are never archived — completing
    # one resets it in place (services/task.complete_task).
    archived_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    property = relationship("Property")
    assignee = relationship("Staff")
    zone = relationship("Zone", back_populates="tasks")
    schedule = relationship("TaskSchedule", back_populates="task", uselist=False, cascade="all, delete-orphan")
    completions = relationship(
        "TaskCompletion", back_populates="task", cascade="all, delete-orphan",
        order_by="TaskCompletion.completed_at.desc()",
    )


class TaskCompletion(Base, TenantOwned):
    """One time a task was done (0020).

    A repeating task is a single row that resets in place when completed, so this log is what
    remembers each completion — for reports, staff stats and "last done". `property_id` is copied
    from the task so the table is PROPERTY_SCOPED and filterable for staff on its own, rather than
    a child table only protected when loaded through its parent (query_scope's stated gap).
    """

    __tablename__ = "task_completions"
    __table_args__ = (
        Index("ix_task_completions_account_task", "account_id", "task_id"),
        Index("ix_task_completions_account_completed", "account_id", "completed_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    completed_by_staff_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # The due date this completion rolled a repeating task TO; None for one-time tasks. Lets a
    # repeat "done" for the same occurrence be recognised and ignored (complete_task).
    rolled_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    task = relationship("Task", back_populates="completions")
    completed_by = relationship("Staff")

    def __repr__(self) -> str:
        return f"<TaskCompletion task={self.task_id} at={self.completed_at}>"


class TaskSchedule(Base, TimestampMixin, TenantOwned):
    __tablename__ = "task_schedules"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tasks.id"), unique=True, nullable=False)
    frequency: Mapped[RecurrenceFrequency] = mapped_column(Enum(RecurrenceFrequency), nullable=False)
    custom_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    season_spec: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)

    task = relationship("Task", back_populates="schedule")
