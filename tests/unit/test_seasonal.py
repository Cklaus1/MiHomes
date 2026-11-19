"""Tests for seasonal service — templates and recommendations."""

from datetime import date
from unittest.mock import patch

from mihomes.models.property import Property, PropertyType
from mihomes.models.template import Template
from mihomes.services.seasonal import (
    BUILTIN_TEMPLATES,
    recommend_seasonal,
    seed_templates,
)


def _make_property(session, name="Belle Estate", slug="belle-estate", climate_zone=None):
    prop = Property(
        name=name, slug=slug,
        property_type=PropertyType.PRIMARY,
        climate_zone=climate_zone,
    )
    session.add(prop)
    session.flush()
    return prop


class TestSeedTemplates:
    def test_seeds_all_builtin_templates(self, session):
        created = seed_templates(session)
        assert len(created) == len(BUILTIN_TEMPLATES)

    def test_does_not_duplicate_on_second_call(self, session):
        seed_templates(session)
        created_again = seed_templates(session)
        assert created_again == []

    def test_creates_correct_slugs(self, session):
        seed_templates(session)
        spring = session.query(Template).filter_by(slug="spring-opening").first()
        assert spring is not None

    def test_creates_correct_names(self, session):
        seed_templates(session)
        fall = session.query(Template).filter_by(slug="fall-closing").first()
        assert fall.name == "Fall Property Closing"

    def test_steps_stored(self, session):
        seed_templates(session)
        tmpl = session.query(Template).filter_by(slug="annual-inspection").first()
        assert len(tmpl.items) == len(BUILTIN_TEMPLATES["annual-inspection"]["steps"])


class TestRecommendSeasonal:
    def test_spring_recommendation_march(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 3, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "spring-opening" in templates

    def test_spring_recommendation_april(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 4, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "spring-opening" in templates

    def test_fall_recommendation_october(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 10, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "fall-closing" in templates

    def test_summer_recommendation_july(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 7, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "summer-maintenance" in templates

    def test_winter_recommendation_january(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 1, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "winter-check" in templates

    def test_annual_inspection_always_included(self, session):
        prop = _make_property(session)
        for month in range(1, 13):
            with patch("mihomes.services.seasonal.local_today", return_value=date(2026, month, 15)):
                recs = recommend_seasonal(session, prop.slug)
            templates = [r["template"] for r in recs]
            assert "annual-inspection" in templates, f"Missing annual-inspection for month {month}"

    def test_hurricane_prep_for_coastal_zone_in_summer(self, session):
        prop = _make_property(session, climate_zone="coastal")
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 7, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "hurricane-prep" in templates

    def test_hurricane_prep_not_included_for_non_coastal(self, session):
        prop = _make_property(session, climate_zone="northeast")
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 7, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "hurricane-prep" not in templates

    def test_hurricane_prep_for_tropical_zone(self, session):
        prop = _make_property(session, climate_zone="tropical")
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 8, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "hurricane-prep" in templates

    def test_fire_safety_in_dry_months(self, session):
        prop = _make_property(session)
        for month in (5, 6, 7, 8, 9):
            with patch("mihomes.services.seasonal.local_today", return_value=date(2026, month, 15)):
                recs = recommend_seasonal(session, prop.slug)
            templates = [r["template"] for r in recs]
            assert "fire-safety" in templates, f"Missing fire-safety for month {month}"

    def test_fire_safety_not_in_winter(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 1, 15)):
            recs = recommend_seasonal(session, prop.slug)
        templates = [r["template"] for r in recs]
        assert "fire-safety" not in templates

    def test_all_recs_have_reason(self, session):
        prop = _make_property(session)
        with patch("mihomes.services.seasonal.local_today", return_value=date(2026, 7, 15)):
            recs = recommend_seasonal(session, prop.slug)
        for rec in recs:
            assert rec.get("reason"), f"Missing reason for {rec['template']}"
