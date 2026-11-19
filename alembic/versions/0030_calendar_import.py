"""Google → MiHomes calendar import (2026-11-11).

memberships.can_import_calendar; calendar_connections.sync_token, default_property_id,
import_note; calendar_event_links.origin, etag + a lookup index by Google event id.
Columns only on existing tenant tables, so no new RLS policies. default_property_id is a
tenant-to-tenant foreign key, so it gets its own drift-guard trigger.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030_calendar_import"
down_revision: Union[str, None] = "0029_calendar_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column("memberships", sa.Column("can_import_calendar", sa.Boolean(),
                                           server_default=sa.false(), nullable=False))
    op.add_column("calendar_connections", sa.Column("sync_token", sa.Text(), nullable=True))
    op.add_column("calendar_connections", sa.Column(
        "default_property_id", UUID, sa.ForeignKey("properties.id", ondelete="SET NULL"),
        nullable=True))
    op.add_column("calendar_connections", sa.Column("import_note", sa.Text(), nullable=True))
    op.add_column("calendar_event_links", sa.Column("origin", sa.String(10),
                                                    server_default="mihomes", nullable=False))
    op.add_column("calendar_event_links", sa.Column("etag", sa.String(200), nullable=True))
    op.create_index("ix_calendar_event_links_google", "calendar_event_links",
                    ["account_id", "connection_id", "google_event_id"])

    if op.get_bind().dialect.name == "postgresql":
        # Only the NEW link: the table's existing membership_id guard is already installed, and
        # re-emitting the table's full set would collide with it. Written out, not derived from
        # live metadata, so this revision stays a fixed point.
        op.execute(
            "CREATE TRIGGER trg_calendar_connections_default_property_id_account "
            "BEFORE INSERT OR UPDATE OF account_id, default_property_id ON calendar_connections "
            "FOR EACH ROW EXECUTE FUNCTION "
            "mihomes_assert_account_matches_parent('properties', 'default_property_id')"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS trg_calendar_connections_default_property_id_account "
                   "ON calendar_connections")
    op.drop_index("ix_calendar_event_links_google", table_name="calendar_event_links")
    op.drop_column("calendar_event_links", "etag")
    op.drop_column("calendar_event_links", "origin")
    op.drop_column("calendar_connections", "import_note")
    op.drop_column("calendar_connections", "default_property_id")
    op.drop_column("calendar_connections", "sync_token")
    op.drop_column("memberships", "can_import_calendar")
