"""Template and TemplateItem models — reusable checklists."""

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin


class Template(Base, TimestampMixin, SlugMixin, TenantOwned):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    items = relationship("TemplateItem", back_populates="template", cascade="all, delete-orphan",
                         order_by="TemplateItem.order")


class TemplateItem(Base, TimestampMixin, TenantOwned):
    __tablename__ = "template_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(Integer, ForeignKey("templates.id"), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    order: Mapped[int] = mapped_column(Integer, default=0)
    estimated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    default_assignee_role: Mapped[str | None] = mapped_column(String(50), nullable=True)

    template = relationship("Template", back_populates="items")
