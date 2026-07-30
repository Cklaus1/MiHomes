"""Tests for AI role routing."""

from datetime import date
from unittest import mock

from mihomes.services.ai.roles import route_query, ROLES


class TestRouteQuery:
    def test_maintenance_keywords(self):
        roles = route_query("the roof is leaking")
        assert roles[0].name == "maintenance"

    def test_financial_keywords(self):
        roles = route_query("we're over budget this quarter")
        assert roles[0].name == "financial"

    def test_vendor_keywords(self):
        roles = route_query("should we replace our plumbing contractor")
        assert roles[0].name == "vendor_strategist"

    def test_compliance_keywords(self):
        roles = route_query("when does the insurance policy expire")
        assert roles[0].name == "compliance"

    def test_fallback_to_estate_manager(self):
        roles = route_query("what's the general status of things")
        assert roles[0].name == "estate_manager"

    def test_explicit_role(self):
        roles = route_query("anything", explicit_role="financial")
        assert roles[0].name == "financial"

    def test_explicit_role_by_display_name(self):
        roles = route_query("anything", explicit_role="Maintenance")
        assert roles[0].name == "maintenance"

    def test_unknown_explicit_role_falls_back(self):
        roles = route_query("anything", explicit_role="nonexistent")
        # Falls through to keyword routing, then estate_manager fallback
        assert roles[0].name == "estate_manager"

    def test_all_roles_have_system_prompts(self):
        for role in ROLES.values():
            assert len(role.system_prompt) > 100
            assert "SPACE" in role.system_prompt


class TestSystemPromptDate:
    """H12 — the system prompt's 'Current date' must be rendered per access,
    not frozen at import, so a long-running server never tells the model a
    stale date."""

    def test_current_date_is_today(self):
        role = ROLES["estate_manager"]
        assert f"Current date: {date.today().isoformat()}" in role.system_prompt

    def test_date_not_frozen_across_days(self):
        role = ROLES["maintenance"]

        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2999, 1, 2)

        with mock.patch("mihomes.services.ai.roles.date", FakeDate):
            assert "Current date: 2999-01-02" in role.system_prompt
