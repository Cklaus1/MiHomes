"""HaEntity — maps a Home Assistant entity_id to a MiHomes property and zone."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, TimestampMixin


class HaEntity(Base, TimestampMixin):
    """Persistent mapping of an HA entity to estate context.

    The source-of-truth for *state* lives in Home Assistant. This table only
    stores the property/zone assignment and any MiHomes-specific metadata so
    that entity state data can be contextualised per property/room.
    """

    __tablename__ = "ha_entities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    entity_id: Mapped[str] = mapped_column(
        String(200), unique=True, nullable=False, index=True
    )
    friendly_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    device_class: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit_of_measurement: Mapped[str | None] = mapped_column(String(50), nullable=True)
    property_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("properties.id", ondelete="SET NULL"), nullable=True
    )
    zone_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("zones.id", ondelete="SET NULL"), nullable=True
    )
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    property = relationship("Property")
    zone = relationship("Zone")
