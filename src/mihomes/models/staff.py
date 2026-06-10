"""Staff model — household employees."""

import enum

from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TimestampMixin

staff_property_association = Table(
    "staff_properties",
    Base.metadata,
    Column("staff_id", Integer, ForeignKey("staff.id"), primary_key=True),
    Column("property_id", Integer, ForeignKey("properties.id"), primary_key=True),
)


class StaffRole(str, enum.Enum):
    # Staff (household employee) roles
    HOUSEKEEPER = "housekeeper"
    GROUNDSKEEPER = "groundskeeper"
    PROPERTY_MANAGER = "property-manager"
    DRIVER = "driver"
    CHEF = "chef"
    SECURITY = "security"
    PERSONAL_ASSISTANT = "personal-assistant"
    OTHER = "other"
    # Non-staff people types (residents, owner family, associates)
    RESIDENT = "resident"
    OWNER = "owner"
    FAMILY_MEMBER = "family-member"
    ASSOCIATE = "associate"


# Directory category derived from a person's role. Any role not listed here
# (i.e. the household-employee roles) falls under "Staff".
ROLE_CATEGORY: dict[StaffRole, str] = {
    StaffRole.RESIDENT: "Resident",
    StaffRole.OWNER: "Family / Owner",
    StaffRole.FAMILY_MEMBER: "Family / Owner",
    StaffRole.ASSOCIATE: "Associate",
}

CATEGORY_ORDER = ["Staff", "Resident", "Associate", "Family / Owner"]


def category_for_role(role: StaffRole) -> str:
    """Map a role to its directory category."""
    return ROLE_CATEGORY.get(role, "Staff")


def is_staff_role(role: StaffRole) -> bool:
    """True for actual household-employee roles (not residents/owners/associates)."""
    return category_for_role(role) == "Staff"


class Staff(Base, TimestampMixin, SlugMixin):
    __tablename__ = "staff"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole), default=StaffRole.OTHER)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    whatsapp_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    properties = relationship("Property", secondary=staff_property_association, backref="staff_members")
