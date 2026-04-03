"""Tests for vendor rating service — create ratings, get scores, compare vendors."""

from datetime import date

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.vendor import Vendor
from mihomes.services.vendor_rating import compare_vendors, create_rating, get_vendor_scores


@pytest.fixture
def prop(session):
    p = Property(name="Rating House", slug="rating-house", property_type=PropertyType.PRIMARY, currency="USD")
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def vendor_a(session):
    v = Vendor(company_name="Alpha Plumbing", slug="alpha-plumbing")
    session.add(v)
    session.flush()
    return v


@pytest.fixture
def vendor_b(session):
    v = Vendor(company_name="Beta Electric", slug="beta-electric")
    session.add(v)
    session.flush()
    return v


class TestCreateRating:
    def test_basic_rating(self, session, vendor_a):
        rating = create_rating(session, str(vendor_a.id), 4, 3, 4, 3)
        assert rating.id is not None
        assert rating.quality_score == 4
        assert rating.overall_score == pytest.approx(3.5)

    def test_overall_score_all_fours(self, session, vendor_a):
        rating = create_rating(session, vendor_a.slug, 4, 4, 4, 4)
        assert rating.overall_score == 4.0

    def test_overall_score_mixed(self, session, vendor_a):
        rating = create_rating(session, vendor_a.slug, 1, 2, 3, 4)
        assert rating.overall_score == pytest.approx(2.5)

    def test_with_property(self, session, vendor_a, prop):
        rating = create_rating(session, vendor_a.slug, 3, 3, 3, 3, property_id_or_slug=prop.slug)
        assert rating.property_id == prop.id

    def test_with_notes(self, session, vendor_a):
        rating = create_rating(session, vendor_a.slug, 4, 4, 3, 4, notes="Great work")
        assert rating.notes == "Great work"

    def test_custom_rated_date(self, session, vendor_a):
        custom_date = date(2025, 6, 15)
        rating = create_rating(session, vendor_a.slug, 3, 3, 3, 3, rated_date=custom_date)
        assert rating.rated_date == custom_date

    def test_defaults_to_today(self, session, vendor_a):
        rating = create_rating(session, vendor_a.slug, 3, 3, 3, 3)
        assert rating.rated_date == date.today()

    def test_creates_audit_log(self, session, vendor_a):
        from mihomes.models.audit_log import AuditLog
        before = session.query(AuditLog).count()
        create_rating(session, vendor_a.slug, 4, 4, 4, 4)
        assert session.query(AuditLog).count() > before

    def test_invalid_vendor_raises(self, session):
        from mihomes.services.slug import EntityNotFoundError
        with pytest.raises(EntityNotFoundError):
            create_rating(session, "nonexistent-vendor", 3, 3, 3, 3)


class TestGetVendorScores:
    def test_no_ratings_returns_none_scores(self, session, vendor_a):
        scores = get_vendor_scores(session, vendor_a.slug)
        assert scores["rating_count"] == 0
        assert scores["overall"] is None

    def test_single_rating(self, session, vendor_a):
        create_rating(session, vendor_a.slug, 4, 3, 2, 3)
        scores = get_vendor_scores(session, vendor_a.slug)
        assert scores["rating_count"] == 1
        assert scores["quality"] == 4.0
        assert scores["reliability"] == 3.0

    def test_multiple_ratings_averaged(self, session, vendor_a):
        create_rating(session, vendor_a.slug, 4, 4, 4, 4)
        create_rating(session, vendor_a.slug, 2, 2, 2, 2)
        scores = get_vendor_scores(session, vendor_a.slug)
        assert scores["rating_count"] == 2
        assert scores["overall"] == pytest.approx(3.0)

    def test_returns_vendor_info(self, session, vendor_a):
        scores = get_vendor_scores(session, vendor_a.slug)
        assert scores["vendor"] == "Alpha Plumbing"
        assert scores["vendor_slug"] == "alpha-plumbing"


class TestCompareVendors:
    def test_sorted_by_overall_descending(self, session, vendor_a, vendor_b):
        create_rating(session, vendor_a.slug, 4, 4, 4, 4)
        create_rating(session, vendor_b.slug, 2, 2, 2, 2)
        results = compare_vendors(session, [vendor_a.slug, vendor_b.slug])
        assert results[0]["vendor"] == "Alpha Plumbing"
        assert results[1]["vendor"] == "Beta Electric"

    def test_unrated_vendor_sorts_last(self, session, vendor_a, vendor_b):
        create_rating(session, vendor_a.slug, 3, 3, 3, 3)
        results = compare_vendors(session, [vendor_a.slug, vendor_b.slug])
        assert results[0]["vendor"] == "Alpha Plumbing"
        assert results[-1]["overall"] is None

    def test_empty_list(self, session):
        assert compare_vendors(session, []) == []
