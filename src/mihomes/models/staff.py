"""Staff model — household employees."""

import enum

from sqlalchemy import Boolean, Column, Enum, ForeignKey, Integer, String, Table, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.models import Base, SlugMixin, TenantOwned, TimestampMixin

# A Core Table, so `account_id` is declared BY HAND: TenantOwned is a
# @declared_attr mixin and cannot reach a table with no declarative class. Without
# this column the table would get no RLS policy (§4.3 derives its list from the
# mixin's subclasses) and no coverage from A1 or A21 — a readable and writable
# cross-tenant surface while A21, "the phase's definition of done", reported green.
#
# It is registered explicitly in mihomes.tenancy.registry.TENANT_TABLES for the same
# reason. See that module's docstring.
staff_property_association = Table(
    "staff_properties",
    Base.metadata,
    Column("staff_id", Integer, ForeignKey("staff.id"), primary_key=True),
    Column("property_id", Integer, ForeignKey("properties.id"), primary_key=True),
    Column(
        "account_id",
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    ),
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


class Staff(Base, TimestampMixin, SlugMixin, TenantOwned):
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
