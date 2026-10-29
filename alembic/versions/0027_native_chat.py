"""native in-app chat (SPEC-012) — seven tenant tables, RLS + drift guard per table (0026 pattern).

Mirrors `models/chat_native.py` column for column; `test_pg_baseline::test_baseline_matches_metadata`
fails on any drift between the two.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0027_native_chat"
down_revision: Union[str, None] = "0026_chat_messages"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)
# Creation order = FK order.
TABLES = ("chat_conversations", "chat_participants", "chat_posts", "chat_reactions",
          "chat_attachments", "chat_mentions", "chat_events")


def _id():
    return sa.Column("id", UUID, primary_key=True)


def _tenant():
    return sa.Column("account_id", UUID, sa.ForeignKey("accounts.id", ondelete="CASCADE"),
                     nullable=False)


def _now(name: str = "created_at"):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now())


def upgrade() -> None:
    op.create_table(
        "chat_conversations", _id(), _tenant(),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("property_id", UUID, sa.ForeignKey("properties.id", ondelete="CASCADE")),
        sa.Column("dm_key", sa.String(73)),
        sa.Column("title", sa.String(200)),
        sa.Column("next_seq", sa.BigInteger, nullable=False, server_default="1"),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        _now(),
        sa.CheckConstraint("kind IN ('estate', 'property', 'dm')", name="ck_chat_conversations_kind"),
        sa.CheckConstraint("(kind = 'property') = (property_id IS NOT NULL)",
                           name="ck_chat_conversations_property"),
        sa.CheckConstraint("(kind = 'dm') = (dm_key IS NOT NULL)",
                           name="ck_chat_conversations_dm_key"),
    )
    for kind, cols in (("estate", []), ("property", ["property_id"]), ("dm", ["dm_key"])):
        op.create_index(f"uq_chat_conversations_{kind}", "chat_conversations",
                        ["account_id", *cols], unique=True,
                        postgresql_where=sa.text(f"kind = '{kind}'"))

    op.create_table(
        "chat_participants", _tenant(),
        sa.Column("conversation_id", UUID,
                  sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("last_read_seq", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("muted", sa.Boolean, nullable=False, server_default="false"),
        _now("joined_at"),
    )

    op.create_table(
        "chat_posts", _id(), _tenant(),
        sa.Column("conversation_id", UUID,
                  sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.BigInteger, nullable=False),
        sa.Column("author_membership_id", UUID, sa.ForeignKey("memberships.id")),
        sa.Column("author_kind", sa.String(10), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("reply_to_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="SET NULL")),
        sa.Column("client_token", UUID),
        sa.Column("edited_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        _now(),
        sa.Column("search_tsv", postgresql.TSVECTOR,
                  sa.Computed("to_tsvector('simple', coalesce(body, ''))", persisted=True)),
        sa.CheckConstraint("author_kind IN ('member', 'bot')", name="ck_chat_posts_author_kind"),
        sa.CheckConstraint("(author_kind = 'bot') = (author_membership_id IS NULL)",
                           name="ck_chat_posts_author"),
        sa.CheckConstraint("author_kind = 'bot' OR char_length(body) <= 4000",
                           name="ck_chat_posts_body_len"),
        sa.CheckConstraint("deleted_at IS NULL OR body = ''", name="ck_chat_posts_deleted_blank"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_chat_posts_seq"),
    )
    op.create_index("ix_chat_posts_search_tsv", "chat_posts", ["search_tsv"],
                    postgresql_using="gin")
    op.create_index("uq_chat_posts_client_token", "chat_posts",
                    ["account_id", "conversation_id", "client_token"],
                    unique=True, postgresql_where=sa.text("client_token IS NOT NULL"))

    op.create_table(
        "chat_reactions", _id(), _tenant(),
        sa.Column("post_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("emoji", sa.String(16), nullable=False),
        _now(),
        sa.UniqueConstraint("post_id", "membership_id", "emoji", name="uq_chat_reactions"),
    )

    op.create_table(
        "chat_attachments", _id(), _tenant(),
        sa.Column("post_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False, unique=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        _now(),
        sa.CheckConstraint("size_bytes >= 0", name="ck_chat_attachments_size"),
    )

    op.create_table(
        "chat_mentions", _id(), _tenant(),
        sa.Column("post_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("notified_at", sa.DateTime(timezone=True)),
        _now(),
        sa.UniqueConstraint("post_id", "membership_id", name="uq_chat_mentions"),
    )

    op.create_table(
        "chat_events", _id(), _tenant(),
        sa.Column("cursor", sa.BigInteger, sa.Identity(always=True), nullable=False, unique=True),
        sa.Column("conversation_id", UUID,
                  sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("post_id", UUID, sa.ForeignKey("chat_posts.id", ondelete="CASCADE")),
        sa.Column("actor_membership_id", UUID, sa.ForeignKey("memberships.id", ondelete="CASCADE")),
        _now(),
    )

    # Secondary indexes lead with account_id: under RLS every query carries the account predicate
    # (`test_tenant_indexes.py`). Mirrors `chat_native.CHAT_ACCOUNT_INDEXES` — inlined, because a
    # migration must not change when a model later does.
    for table in TABLES:
        op.create_index(op.f(f"ix_{table}_account_id"), table, ["account_id"])
    for table, cols in {
        "chat_conversations": ("property_id",),
        "chat_participants": ("membership_id",),
        "chat_posts": ("conversation_id", "author_membership_id", "reply_to_id"),
        "chat_reactions": ("post_id", "membership_id"),
        "chat_attachments": ("post_id",),
        "chat_mentions": ("post_id", "membership_id"),
        "chat_events": ("conversation_id", "post_id", "actor_membership_id", "created_at"),
    }.items():
        for col in cols:
            op.create_index(f"ix_{table}_account_{col}", table, ["account_id", col])

    if op.get_bind().dialect.name == "postgresql":
        from mihomes.models import Base
        from mihomes.tenancy.drift_guard import trigger_ddl_statements
        from mihomes.tenancy.rls import policy_statements

        for table in TABLES:
            for stmt in policy_statements(table):
                op.execute(stmt)
        for stmt in trigger_ddl_statements(Base.metadata, only_tables=set(TABLES)):
            op.execute(stmt)


def downgrade() -> None:
    # Indexes, policies and triggers drop with their table.
    for table in reversed(TABLES):
        op.drop_table(table)
