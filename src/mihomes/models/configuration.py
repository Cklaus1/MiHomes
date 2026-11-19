"""Configuration model — key-value settings."""

from sqlalchemy import PrimaryKeyConstraint, String, Text
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from mihomes.models import Base, TenantOwned


class Configuration(Base, TenantOwned):
    """Per-account key/value settings.

    A **natural** primary key, not a surrogate UUID — D2's "uuid PK" governs
    surrogate ids, and a config row is identified by what it configures.

    But the key alone is no longer sufficient under multitenancy: two accounts must
    each be able to set `ai.provider`. So the PK is the composite
    `(account_id, key)`, which is also the shape RLS wants — `account_id` leads, so
    the PK index doubles as the tenant index.
    """

    __tablename__ = "configurations"

    # No primary_key=True here: the composite PK is declared in __table_args__
    # below. Declaring both makes SQLAlchemy warn that the column-level and
    # table-level definitions disagree ("may become an exception in a future
    # release"), and the table-level one is the real definition.
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)

    @declared_attr.directive
    def __table_args__(cls):
        # account_id is contributed by TenantOwned, so it can only be named in a
        # declared_attr — at class-body time the column does not exist yet.
        return (PrimaryKeyConstraint("account_id", "key", name="pk_configurations"),)
