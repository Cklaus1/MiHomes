"""Task and TaskSchedule models."""

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    property_id: Mapped[int] = mapped_column(Integer, ForeignKey("properties.id"), nullable=False)
    assignee_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("staff.id"), nullable=True)
    priority: Mapped[TaskPriority] = mapped_column(Enum(TaskPriority), default=TaskPriority.MEDIUM)
    status: Mapped[TaskStatus] = mapped_column(Enum(TaskStatus), default=TaskStatus.PENDING)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completion_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    estimated_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    gcal_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    zone_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("zones.id"), index=True, nullable=True)
    category: Mapped[TaskCategory | None] = mapped_column(Enum(TaskCategory), nullable=True, index=True)

    property = relationship("Property")
    assignee = relationship("Staff")
    zone = relationship("Zone", back_populates="tasks")
    schedule = relationship("TaskSchedule", back_populates="task", uselist=False, cascade="all, delete-orphan")


class TaskSchedule(Base, TimestampMixin, TenantOwned):
    __tablename__ = "task_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id"), unique=True, nullable=False)
    frequency: Mapped[RecurrenceFrequency] = mapped_column(Enum(RecurrenceFrequency), nullable=False)
    custom_cron: Mapped[str | None] = mapped_column(String(100), nullable=True)
    season_spec: Mapped[str | None] = mapped_column(String(100), nullable=True)
    next_due: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_generated: Mapped[date | None] = mapped_column(Date, nullable=True)

    task = relationship("Task", back_populates="schedule")
