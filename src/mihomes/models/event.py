"""Event, Guest, and EventGuest models — event and hospitality management."""

import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin
from mihomes.type.money import Money


class EventStatus(str, enum.Enum):
    PLANNING = "planning"
    CONFIRMED = "confirmed"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class Event(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    property_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("properties.id"), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expected_guests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget: Mapped[float | None] = mapped_column(Money, nullable=True)
    currency: Mapped[str] = mapped_column(String(10), default="USD")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[EventStatus] = mapped_column(Enum(EventStatus), default=EventStatus.PLANNING)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    guests = relationship("EventGuest", back_populates="event", cascade="all, delete-orphan")


class Guest(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "guests"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    dietary_preferences: Mapped[str | None] = mapped_column(String(300), nullable=True)
    room_preference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventGuest(Base, TimestampMixin, TenantOwned):
    __tablename__ = "event_guests"
    __table_args__ = (
        UniqueConstraint("event_id", "guest_id", name="uq_event_guest"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("events.id"), nullable=False)
    guest_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("guests.id"), nullable=False)
    rsvp_status: Mapped[str] = mapped_column(String(50), default="invited")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    event = relationship("Event", back_populates="guests")
    guest = relationship("Guest")
