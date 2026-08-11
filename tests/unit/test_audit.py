"""Tests for audit log service."""

import uuid
from enum import Enum

from mihomes.models.audit_log import AuditLog
from mihomes.services.audit import diff_instance, record_change, snapshot_instance

# Placeholder ids for polymorphic entity_type/entity_id pairs. Distinct
# constants because several tests rely on two ids being DIFFERENT (filter by
# one, assert the other is excluded) — a single shared UUID would make those
# tests pass for the wrong reason. Were integers before SPEC-002 D2.
_ENTITY_1 = uuid.uuid4()
_ENTITY_42 = uuid.uuid4()


class FakeEnum(Enum):
    ACTIVE = "active"
    CLOSED = "closed"


class TestDiffInstance:
    def test_no_changes(self):
        old = {"name": "Beach House", "status": "open"}
        new = {"name": "Beach House", "status": "open"}
        assert diff_instance(old, new) == {}

    def test_one_field_changed(self):
        old = {"name": "Beach House", "status": "open"}
        new = {"name": "Beach House", "status": "closed"}
        result = diff_instance(old, new)
        assert result == {"status": {"old": "open", "new": "closed"}}

    def test_multiple_changes(self):
        old = {"name": "Old Name", "status": "open", "sqft": 2000}
        new = {"name": "New Name", "status": "closed", "sqft": 2000}
        result = diff_instance(old, new)
        assert "name" in result
        assert "status" in result
        assert "sqft" not in result

    def test_new_field(self):
        old = {"name": "Beach House"}
        new = {"name": "Beach House", "notes": "something"}
        result = diff_instance(old, new)
        assert result == {"notes": {"old": None, "new": "something"}}

    def test_none_values(self):
        old = {"name": "Beach House", "notes": None}
        new = {"name": "Beach House", "notes": "added"}
        result = diff_instance(old, new)
        assert result == {"notes": {"old": None, "new": "added"}}


class TestSnapshotInstance:
    def test_snapshot_basic(self, session):
        entry = AuditLog(
            entity_type="property",
            entity_id=_ENTITY_1,
            action="create",
            changes={"name": "test"},
            actor="admin",
        )
        session.add(entry)
        session.flush()
        snap = snapshot_instance(entry)
        assert snap["entity_type"] == "property"
        assert snap["entity_id"] == str(_ENTITY_1)
        assert snap["action"] == "create"
        assert snap["actor"] == "admin"


class TestRecordChange:
    def test_creates_audit_entry(self, session):
        record_change(
            session,
            entity_type="property",
            entity_id=_ENTITY_42,
            action="create",
            changes={"name": {"old": None, "new": "Beach House"}},
            actor="admin",
        )
        session.flush()
        entries = session.query(AuditLog).all()
        assert len(entries) == 1
        assert entries[0].entity_type == "property"
        assert entries[0].entity_id == _ENTITY_42
        assert entries[0].action == "create"
        assert entries[0].changes["name"]["new"] == "Beach House"

    def test_default_actor(self, session):
        record_change(session, "task", _ENTITY_1, "update")
        session.flush()
        entry = session.query(AuditLog).first()
        assert entry.actor == "admin"

    def test_custom_actor(self, session):
        record_change(session, "issue", uuid.uuid4(), "create", actor="whatsapp:Sarah")
        session.flush()
        entry = session.query(AuditLog).first()
        assert entry.actor == "whatsapp:Sarah"
