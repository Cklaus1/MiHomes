"""Staff model — household employees."""

import enum
import uuid

from sqlalchemy import Boolean, Column, Enum, ForeignKey, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from mihomes.ids import new_id
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
    Column("staff_id", PGUUID(as_uuid=True), ForeignKey("staff.id"), primary_key=True),
    Column("property_id", PGUUID(as_uuid=True), ForeignKey("properties.id"), primary_key=True),
    Column(
        "account_id",
        PGUUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        # A Python-side default, because the ORM's before_flush listener cannot see
        # these rows: appending to `staff.properties` emits a Core INSERT with no
        # instance in session.new. A column default DOES fire for Core inserts.
        # Lazy import — mihomes.tenancy imports the models.
        default=lambda: _association_account_default(),
    ),
)


def _association_account_default():
    from mihomes.tenancy.session import association_account_default

    return association_account_default()


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
    __table_args__ = (
        UniqueConstraint("account_id", "slug", name="uq_staff_account_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=new_id
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole), default=StaffRole.OTHER)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The MiHomes login this HR record belongs to, if the person has one — SPEC-003 U6.
    #:
    #: **This is what makes §4.1's `PERSONNEL` rule enforceable.** *"Staff may see their own
    #: record; never others'"* needs a hard answer to "which row is mine", and `email` cannot give
    #: one: it is nullable (NULL would match NULL), two rows may share an address, and an HR
    #: contact address is often not the address someone signs in with. `authz/query_scope.py`
    #: filters `PERSONNEL` on this column and on nothing else.
    #:
    #: Nullable because most staff have no login at all — a gardener, a contractor's crew — and
    #: `ON DELETE SET NULL` because deleting a *person's login* must not delete their *employment
    #: record*. No `index=True`: see the migration's note on Step 3's leading-column rule.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    whatsapp_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    certifications: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    properties = relationship("Property", secondary=staff_property_association, backref="staff_members")
