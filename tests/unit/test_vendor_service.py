"""Tests for vendor service (filling coverage gaps)."""

import pytest

from mihomes.models.vendor import Vendor
from mihomes.services.vendor import (
    create_vendor,
    delete_vendor,
    get_vendor,
    get_vendor_ratings,
    list_vendors,
    rate_vendor,
    update_vendor,
)
from mihomes.services.slug import EntityNotFoundError


class TestCreateVendor:
    def test_create_minimal(self, session):
        v = create_vendor(session, "ABC Plumbing")
        assert v.id is not None
        assert v.company_name == "ABC Plumbing"
        assert v.slug == "abc-plumbing"

    def test_create_with_all_fields(self, session):
        v = create_vendor(
            session, "HVAC Co",
            contact_name="Bob", phone="770-555-0100",
            email="bob@hvac.com", service_categories=["hvac"],
            service_areas=["belle-estate"], notes="Fast response",
        )
        assert v.contact_name == "Bob"
        assert v.phone == "770-555-0100"
        assert v.service_categories == ["hvac"]
        assert v.service_areas == ["belle-estate"]

    def test_slug_uniqueness(self, session):
        v1 = create_vendor(session, "ABC Plumbing")
        v2 = create_vendor(session, "ABC Plumbing")
        assert v1.slug != v2.slug

    def test_audit_record_created(self, session):
        from mihomes.models.audit_log import AuditLog
        create_vendor(session, "Test Vendor")
        log = session.query(AuditLog).filter_by(entity_type="vendor", action="create").first()
        assert log is not None


class TestListVendors:
    def test_list_all_active(self, session):
        create_vendor(session, "Vendor A")
        create_vendor(session, "Vendor B")
        vendors = list_vendors(session)
        assert len(vendors) == 2

    def test_inactive_excluded_by_default(self, session):
        v = create_vendor(session, "Inactive Co")
        v.active = False
        session.flush()
        vendors = list_vendors(session)
        assert len(vendors) == 0

    def test_inactive_included_when_requested(self, session):
        v = create_vendor(session, "Inactive Co")
        v.active = False
        session.flush()
        vendors = list_vendors(session, active_only=False)
        assert len(vendors) == 1

    def test_filter_by_category(self, session):
        create_vendor(session, "Plumber", service_categories=["plumbing"])
        create_vendor(session, "HVAC Guy", service_categories=["hvac"])
        vendors = list_vendors(session, category="plumbing")
        assert len(vendors) == 1
        assert vendors[0].company_name == "Plumber"

    def test_category_filter_case_insensitive(self, session):
        create_vendor(session, "Plumber", service_categories=["Plumbing"])
        vendors = list_vendors(session, category="plumbing")
        assert len(vendors) == 1

    def test_empty_returns_empty_list(self, session):
        assert list_vendors(session) == []


class TestUpdateVendor:
    def test_update_phone(self, session):
        v = create_vendor(session, "Plumber", phone="770-555-0100")
        updated = update_vendor(session, v.slug, phone="770-555-0199")
        assert updated.phone == "770-555-0199"

    def test_update_company_name_regenerates_slug(self, session):
        v = create_vendor(session, "Old Name")
        updated = update_vendor(session, v.slug, company_name="New Name")
        assert updated.company_name == "New Name"
        assert updated.slug == "new-name"

    def test_update_notes(self, session):
        v = create_vendor(session, "Vendor", notes="original")
        updated = update_vendor(session, v.slug, notes="updated notes")
        assert updated.notes == "updated notes"

    def test_audit_recorded_on_change(self, session):
        from mihomes.models.audit_log import AuditLog
        v = create_vendor(session, "Vendor")
        update_vendor(session, v.slug, phone="555-1234")
        log = session.query(AuditLog).filter_by(entity_type="vendor", action="update").first()
        assert log is not None


class TestRateVendor:
    def test_rate_with_required_fields(self, session):
        v = create_vendor(session, "Plumber")
        rating = rate_vendor(session, v.slug, quality=4, reliability=5)
        assert rating.quality_score == 4
        assert rating.reliability_score == 5
        assert rating.overall_score == 4.5

    def test_rate_with_all_scores(self, session):
        v = create_vendor(session, "Plumber")
        rating = rate_vendor(session, v.slug, quality=4, reliability=4, cost=4, communication=4)
        assert rating.overall_score == 4.0

    def test_overall_is_average_of_provided(self, session):
        v = create_vendor(session, "Plumber")
        rating = rate_vendor(session, v.slug, quality=5, reliability=3)
        assert rating.overall_score == 4.0

    def test_invalid_quality_raises(self, session):
        v = create_vendor(session, "Plumber")
        with pytest.raises(ValueError, match="quality"):
            rate_vendor(session, v.slug, quality=6, reliability=3)

    def test_invalid_reliability_raises(self, session):
        v = create_vendor(session, "Plumber")
        with pytest.raises(ValueError, match="reliability"):
            rate_vendor(session, v.slug, quality=3, reliability=0)

    def test_invalid_cost_raises(self, session):
        v = create_vendor(session, "Plumber")
        with pytest.raises(ValueError, match="cost"):
            rate_vendor(session, v.slug, quality=3, reliability=3, cost=6)

    def test_invalid_communication_raises(self, session):
        v = create_vendor(session, "Plumber")
        with pytest.raises(ValueError, match="communication"):
            rate_vendor(session, v.slug, quality=3, reliability=3, communication=0)


class TestGetVendorRatings:
    def test_no_ratings_returns_empty(self, session):
        v = create_vendor(session, "Plumber")
        result = get_vendor_ratings(session, v.slug)
        assert result["ratings"] == []
        assert result["averages"] is None

    def test_averages_calculated(self, session):
        v = create_vendor(session, "Plumber")
        rate_vendor(session, v.slug, quality=4, reliability=4)
        rate_vendor(session, v.slug, quality=2, reliability=2)
        result = get_vendor_ratings(session, v.slug)
        assert result["averages"]["quality"] == 3.0
        assert result["averages"]["reliability"] == 3.0
        assert result["averages"]["count"] == 2

    def test_returns_vendor(self, session):
        v = create_vendor(session, "Plumber")
        rate_vendor(session, v.slug, quality=5, reliability=5)
        result = get_vendor_ratings(session, v.slug)
        assert result["vendor"].id == v.id


class TestDeleteVendor:
    def test_delete_removes_vendor(self, session):
        v = create_vendor(session, "Plumber")
        delete_vendor(session, v.slug)
        assert session.get(Vendor, v.id) is None

    def test_delete_returns_name(self, session):
        v = create_vendor(session, "Plumber")
        name = delete_vendor(session, v.slug)
        assert name == "Plumber"

    def test_delete_not_found_raises(self, session):
        with pytest.raises(EntityNotFoundError):
            delete_vendor(session, "nonexistent-vendor")

    def test_delete_records_audit(self, session):
        from mihomes.models.audit_log import AuditLog
        v = create_vendor(session, "Plumber")
        delete_vendor(session, v.slug)
        log = session.query(AuditLog).filter_by(entity_type="vendor", action="delete").first()
        assert log is not None
