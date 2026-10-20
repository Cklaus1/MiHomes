"""Migration 0021 — dropping the SOP document type converts existing SOP rows to OTHER.

Own scratch database for the reason `test_pg_baseline.py` gives: the suite's schema comes from
`create_all`, which would test the migration against a schema it did not build.
"""

import os
import uuid

import pytest
from sqlalchemy import create_engine, text

from alembic import command
from tests.integration.test_migration_password_auth import scratch_db  # noqa: F401 — fixture
from tests.integration.test_pg_baseline import _config

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="needs a reachable Postgres; a skip here means 0021 was NOT verified",
)


def _enum_values(conn) -> list[str]:
    return list(
        conn.execute(
            text(
                "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
                "WHERE t.typname = 'documenttype' ORDER BY e.enumsortorder"
            )
        ).scalars()
    )


def test_sop_rows_become_other_and_the_value_is_gone(scratch_db):  # noqa: F811
    command.upgrade(_config(scratch_db), "0020_task_completions")
    engine = create_engine(scratch_db, future=True)
    doc_id = uuid.uuid4()
    try:
        with engine.begin() as conn:
            # Bypass FK and guard triggers: this test is about the enum, not about accounts.
            conn.execute(text("SET session_replication_role = replica"))
            conn.execute(
                text(
                    "INSERT INTO documents (id, account_id, title, slug, file_path, document_type, "
                    "staff_visible, created_at, updated_at) VALUES "
                    "(:id, :acct, 'Pool SOP', 'pool-sop', '—', 'SOP', false, now(), now())"
                ),
                {"id": doc_id, "acct": uuid.uuid4()},
            )

        command.upgrade(_config(scratch_db), "0021_drop_sop_document_type")
        with engine.connect() as conn:
            assert "SOP" not in _enum_values(conn)
            assert conn.execute(
                text("SELECT document_type::text FROM documents WHERE id = :id"), {"id": doc_id}
            ).scalar_one() == "OTHER"

        command.downgrade(_config(scratch_db), "0020_task_completions")
        with engine.connect() as conn:
            assert "SOP" in _enum_values(conn)
    finally:
        engine.dispose()
