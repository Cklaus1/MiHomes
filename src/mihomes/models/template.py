"""Template and TemplateItem models — reusable checklists."""

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class Template(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "templates"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    items = relationship("TemplateItem", back_populates="template", cascade="all, delete-orphan",
                         order_by="TemplateItem.order")


class TemplateItem(Base, TimestampMixin, TenantOwned):
    __tablename__ = "template_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("templates.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_assignee_role: Mapped[str | None] = mapped_column(String(50), nullable=True)

    template = relationship("Template", back_populates="items")
