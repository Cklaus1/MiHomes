"""Document model — file and document tracking with polymorphic entity linking."""

import enum
from datetime import date

from sqlalchemy import Date, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base, SlugMixin, TimestampMixin


class DocumentType(str, enum.Enum):
    WARRANTY = "warranty"
    CONTRACT = "contract"
    INVOICE = "invoice"
    MANUAL = "manual"
    INSURANCE = "insurance"
    PERMIT = "permit"
    REGULATION = "regulation"
    SOP = "sop"
    REPORT = "report"
    PHOTO = "photo"
    OTHER = "other"


class Document(Base, TimestampMixin, SlugMixin):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
