"""Tests for document service — CRUD, entity linking, expiry, path validation."""

from datetime import date, timedelta

import pytest

from mihomes.models.document import Document, DocumentType
from mihomes.services.document import (
    create_document,
    delete_document,
    get_document,
    list_documents,
    list_expiring,
    update_document,
)


class TestCreateDocument:
    def test_basic_create(self, session):
        doc = create_document(session, "Lease Agreement", "/docs/lease.pdf", DocumentType.CONTRACT)
        assert doc.id is not None
        assert doc.title == "Lease Agreement"
        assert doc.slug == "lease-agreement"
        assert doc.document_type == DocumentType.CONTRACT

    def test_custom_slug(self, session):
        doc = create_document(session, "Insurance Policy", "/docs/ins.pdf",
                               DocumentType.INSURANCE, slug="my-insurance")
        assert doc.slug == "my-insurance"

    def test_with_expiry(self, session):
        expires = date.today() + timedelta(days=60)
        doc = create_document(session, "Cert", "/docs/cert.pdf",
                               DocumentType.PERMIT, expires_at=expires)
        assert doc.expires_at == expires

    def test_with_entity_link(self, session):
        doc = create_document(session, "Vendor Contract", "/docs/vc.pdf",
                               DocumentType.CONTRACT, entity_type="vendor", entity_id=1)
        assert doc.entity_type == "vendor"
        assert doc.entity_id == 1

    def test_path_traversal_rejected(self, session):
        with pytest.raises(ValueError, match="traversal"):
            create_document(session, "Bad Doc", "../../../etc/passwd", DocumentType.OTHER)

    def test_entity_type_without_id_raises(self, session):
        with pytest.raises(ValueError, match="entity_id is required"):
            create_document(session, "Doc", "/docs/d.pdf", DocumentType.OTHER,
                             entity_type="property")

    def test_entity_id_without_type_raises(self, session):
        with pytest.raises(ValueError, match="entity_type is required"):
            create_document(session, "Doc", "/docs/d.pdf", DocumentType.OTHER,
                             entity_id=1)

    def test_invalid_entity_type_raises(self, session):
        with pytest.raises(ValueError, match="Invalid entity_type"):
            create_document(session, "Doc", "/docs/d.pdf", DocumentType.OTHER,
                             entity_type="banana", entity_id=1)

    def test_creates_audit_log(self, session):
        from mihomes.models.audit_log import AuditLog
        before = session.query(AuditLog).count()
        create_document(session, "Audit Doc", "/docs/a.pdf", DocumentType.OTHER)
        assert session.query(AuditLog).count() > before

    def test_slug_uniqueness(self, session):
        create_document(session, "Same Title", "/docs/1.pdf", DocumentType.OTHER)
        doc2 = create_document(session, "Same Title", "/docs/2.pdf", DocumentType.OTHER)
        assert doc2.slug != "same-title"  # gets a suffix


class TestGetDocument:
    def test_get_by_id(self, session):
        doc = create_document(session, "Get By ID", "/docs/g.pdf", DocumentType.OTHER)
        fetched = get_document(session, str(doc.id))
        assert fetched.id == doc.id

    def test_get_by_slug(self, session):
        doc = create_document(session, "Get By Slug", "/docs/gs.pdf", DocumentType.OTHER)
        fetched = get_document(session, doc.slug)
        assert fetched.id == doc.id

    def test_not_found_raises(self, session):
        from mihomes.services.slug import EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            get_document(session, "nonexistent-doc")


class TestListDocuments:
    def test_list_all(self, session):
        create_document(session, "Doc One", "/docs/1.pdf", DocumentType.CONTRACT)
        create_document(session, "Doc Two", "/docs/2.pdf", DocumentType.INSURANCE)
        docs = list_documents(session)
        assert len(docs) >= 2

    def test_filter_by_type(self, session):
        create_document(session, "Contract Doc", "/docs/c.pdf", DocumentType.CONTRACT)
        create_document(session, "Insurance Doc", "/docs/i.pdf", DocumentType.INSURANCE)
        contracts = list_documents(session, document_type=DocumentType.CONTRACT)
        assert all(d.document_type == DocumentType.CONTRACT for d in contracts)

    def test_filter_by_entity_type(self, session):
        create_document(session, "Vendor Doc", "/docs/v.pdf", DocumentType.CONTRACT,
                         entity_type="vendor", entity_id=1)
        create_document(session, "Property Doc", "/docs/p.pdf", DocumentType.CONTRACT,
                         entity_type="property", entity_id=1)
        vendor_docs = list_documents(session, entity_type="vendor")
        assert all(d.entity_type == "vendor" for d in vendor_docs)

    def test_filter_by_entity_id(self, session):
        create_document(session, "Entity 1 Doc", "/docs/e1.pdf", DocumentType.OTHER,
                         entity_type="vendor", entity_id=1)
        create_document(session, "Entity 2 Doc", "/docs/e2.pdf", DocumentType.OTHER,
                         entity_type="vendor", entity_id=2)
        docs = list_documents(session, entity_type="vendor", entity_id=1)
        assert all(d.entity_id == 1 for d in docs)


class TestUpdateDocument:
    def test_update_title_regenerates_slug(self, session):
        doc = create_document(session, "Old Title", "/docs/old.pdf", DocumentType.OTHER)
        updated = update_document(session, doc.slug, title="New Title")
        assert updated.title == "New Title"
        assert updated.slug == "new-title"

    def test_update_notes(self, session):
        doc = create_document(session, "Note Doc", "/docs/n.pdf", DocumentType.OTHER)
        update_document(session, doc.slug, notes="Important document")
        session.expire(doc)
        assert doc.notes == "Important document"

    def test_update_entity_link(self, session):
        doc = create_document(session, "Link Doc", "/docs/l.pdf", DocumentType.OTHER)
        update_document(session, doc.slug, entity_type="vendor", entity_id=5)
        session.expire(doc)
        assert doc.entity_type == "vendor"
        assert doc.entity_id == 5

    def test_update_invalid_entity_raises(self, session):
        doc = create_document(session, "Bad Link", "/docs/b.pdf", DocumentType.OTHER)
        with pytest.raises(ValueError):
            update_document(session, doc.slug, entity_type="invalid_type", entity_id=1)


class TestDeleteDocument:
    def test_delete_returns_title(self, session):
        doc = create_document(session, "Delete Me", "/docs/del.pdf", DocumentType.OTHER)
        slug = doc.slug
        name = delete_document(session, slug)
        assert name == "Delete Me"
        assert session.query(Document).filter(Document.slug == slug).first() is None

    def test_delete_nonexistent_raises(self, session):
        from mihomes.services.slug import EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            delete_document(session, "does-not-exist")


class TestListExpiring:
    def test_finds_expiring_soon(self, session):
        expires_soon = date.today() + timedelta(days=10)
        create_document(session, "Expiring Doc", "/docs/exp.pdf",
                         DocumentType.PERMIT, expires_at=expires_soon)
        results = list_expiring(session, days=30)
        slugs = [d.slug for d in results]
        assert "expiring-doc" in slugs

    def test_excludes_already_expired(self, session):
        already_expired = date.today() - timedelta(days=5)
        create_document(session, "Already Expired", "/docs/past.pdf",
                         DocumentType.PERMIT, expires_at=already_expired)
        results = list_expiring(session, days=30)
        slugs = [d.slug for d in results]
        assert "already-expired" not in slugs

    def test_excludes_far_future(self, session):
        far_future = date.today() + timedelta(days=365)
        create_document(session, "Far Future Doc", "/docs/far.pdf",
                         DocumentType.PERMIT, expires_at=far_future)
        results = list_expiring(session, days=30)
        slugs = [d.slug for d in results]
        assert "far-future-doc" not in slugs

    def test_excludes_no_expiry(self, session):
        create_document(session, "No Expiry Doc", "/docs/noexp.pdf", DocumentType.OTHER)
        results = list_expiring(session, days=30)
        slugs = [d.slug for d in results]
        assert "no-expiry-doc" not in slugs

    def test_sorted_by_expiry_date(self, session):
        create_document(session, "Expires Later", "/docs/later.pdf",
                         DocumentType.PERMIT, expires_at=date.today() + timedelta(days=20))
        create_document(session, "Expires Sooner", "/docs/sooner.pdf",
                         DocumentType.PERMIT, expires_at=date.today() + timedelta(days=5))
        results = list_expiring(session, days=30)
        dates = [d.expires_at for d in results if d.expires_at]
        assert dates == sorted(dates)
