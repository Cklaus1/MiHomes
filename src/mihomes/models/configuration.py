"""Configuration model — key-value settings."""

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.models import Base


class Configuration(Base):
    __tablename__ = "configurations"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
