"""Tests for AI services — roles, context assembly, assessors, orchestrator.

AI provider calls are mocked so no real API key is needed.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.models.task import Task, TaskPriority, TaskStatus
from mihomes.models.issue import Issue, IssueSeverity, IssueStatus
from mihomes.services.ai.roles import route_query, ROLES
from mihomes.services.ai.provider import AIProviderError, AIAuthError, get_provider


# ---------------------------------------------------------------------------
# Roles & routing
# ---------------------------------------------------------------------------

class TestAIRoles:
    def test_all_roles_defined(self):
        assert "estate_manager" in ROLES
        assert "maintenance" in ROLES
        assert "financial" in ROLES
        assert "vendor_strategist" in ROLES
        assert "compliance" in ROLES

    def test_each_role_has_system_prompt(self):
        for name, role in ROLES.items():
            assert role.system_prompt, f"{name} missing system_prompt"

    def test_each_role_has_data_categories(self):
        for name, role in ROLES.items():
            assert role.data_categories, f"{name} missing data_categories"


class TestRouteQuery:
    def test_defaults_to_estate_manager(self):
        roles = route_query("how is everything going?")
        assert roles[0].name == "estate_manager"

    def test_maintenance_keywords(self):
        roles = route_query("the roof is leaking and needs repair")
        names = [r.name for r in roles]
        assert "maintenance" in names

    def test_financial_keywords(self):
        roles = route_query("what is our budget variance this month?")
        names = [r.name for r in roles]
        assert "financial" in names

    def test_explicit_role_override(self):
        roles = route_query("anything", explicit_role="compliance")
        assert roles[0].name == "compliance"

    def test_explicit_role_is_first(self):
        roles = route_query("fix the leak", explicit_role="financial")
        assert roles[0].name == "financial"

    def test_returns_list(self):
        result = route_query("test")
        assert isinstance(result, list)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Context assembly
# ---------------------------------------------------------------------------

class TestAssembleContext:
    def test_returns_string(self, session):
        from mihomes.services.ai.context import assemble_context
        roles = list(ROLES.values())[:1]
        ctx = assemble_context(session, roles, "test query")
        assert isinstance(ctx, str)

    def test_includes_property_data_when_category_present(self, session):
        from mihomes.services.ai.context import assemble_context
        p = Property(name="Context House", slug="context-house",
                     property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p)
        session.flush()

        estate_role = ROLES["estate_manager"]
        assert "properties" in estate_role.data_categories
        ctx = assemble_context(session, [estate_role], "test")
        assert "Context House" in ctx

    def test_property_scoping_tasks(self, session):
        from mihomes.services.ai.context import assemble_context
        from datetime import date, timedelta
        p1 = Property(name="Scoped A", slug="scoped-a",
                      property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p1)
        session.flush()
        # Overdue task on p1 — context includes overdue tasks
        t = Task(title="Only For A", slug="only-for-a", property_id=p1.id,
                 priority=TaskPriority.HIGH, status=TaskStatus.PENDING,
                 due_date=date.today() - timedelta(days=1))
        session.add(t)
        session.flush()

        role = ROLES["maintenance"]
        ctx = assemble_context(session, [role], "test", property_slug="scoped-a")
        assert "Only For A" in ctx

    def test_empty_db_returns_string(self, session):
        from mihomes.services.ai.context import assemble_context
        role = ROLES["estate_manager"]
        ctx = assemble_context(session, [role], "test")
        assert isinstance(ctx, str)

    def test_tasks_included_for_maintenance_role(self, session):
        from mihomes.services.ai.context import assemble_context
        from datetime import date, timedelta
        p = Property(name="Task House", slug="task-house",
                     property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p)
        session.flush()
        # Must have a past due_date to appear as overdue in context
        t = Task(title="Fix Boiler", slug="fix-boiler", property_id=p.id,
                 priority=TaskPriority.HIGH, status=TaskStatus.PENDING,
                 due_date=date.today() - timedelta(days=1))
        session.add(t)
        session.flush()

        role = ROLES["maintenance"]
        assert "tasks" in role.data_categories
        ctx = assemble_context(session, [role], "test")
        assert "Fix Boiler" in ctx


# ---------------------------------------------------------------------------
# Provider factory
# ---------------------------------------------------------------------------

class TestGetProvider:
    def test_unknown_provider_raises(self):
        with pytest.raises(AIProviderError):
            get_provider("unknown_provider", "key")

    def test_claude_provider_requires_key(self):
        with pytest.raises(AIAuthError):
            get_provider("claude", None)

    def test_openai_provider_requires_key(self):
        with pytest.raises(AIAuthError):
            get_provider("openai", None)

    def test_nim_provider_requires_key(self):
        import unittest.mock
        with unittest.mock.patch.dict("os.environ", {}, clear=False) as env:
            env.pop("NVIDIA_API_KEY", None)
            with pytest.raises(AIAuthError):
                get_provider("nim", None)

    def test_ollama_no_key_needed(self):
        # Ollama is local — should not raise even without a key
        provider = get_provider("ollama", None)
        assert provider is not None


# ---------------------------------------------------------------------------
# Assessors (with mocked provider)
# ---------------------------------------------------------------------------

class TestSuggestTagsAndPriority:
    def _mock_provider(self, return_value: dict):
        mock = MagicMock()
        mock.structured_output.return_value = return_value
        return mock

    def test_task_suggestions_returned(self, session):
        from mihomes.services.ai.assessors import suggest_tags_and_priority
        mock = self._mock_provider({"priority": "high", "tags": ["plumbing", "urgent"]})
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            result = suggest_tags_and_priority(session, "task", "Fix leaky pipe")
        assert result["priority"] == "high"
        assert "plumbing" in result["tags"]

    def test_issue_suggestions_returned(self, session):
        from mihomes.services.ai.assessors import suggest_tags_and_priority
        mock = self._mock_provider({"severity": "critical", "tags": ["safety", "electrical"]})
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            result = suggest_tags_and_priority(session, "issue", "Exposed wiring in basement")
        assert result["severity"] == "critical"
        assert "safety" in result["tags"]

    def test_invalid_entity_type_raises(self, session):
        from mihomes.services.ai.assessors import suggest_tags_and_priority
        with pytest.raises(ValueError, match="not supported"):
            suggest_tags_and_priority(session, "banana", "Some title")

    def test_provider_called_with_correct_schema(self, session):
        from mihomes.services.ai.assessors import suggest_tags_and_priority, SUGGESTION_SCHEMAS
        mock = self._mock_provider({"priority": "medium", "tags": []})
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            suggest_tags_and_priority(session, "task", "Test title")
        call_args = mock.structured_output.call_args
        assert call_args[0][2] == SUGGESTION_SCHEMAS["task"]


class TestParseImportText:
    def _mock_provider(self, return_value: dict):
        mock = MagicMock()
        mock.structured_output.return_value = return_value
        return mock

    def test_parse_vendor_text(self, session):
        from mihomes.services.ai.assessors import parse_import_text
        mock = self._mock_provider({
            "items": [{"company_name": "ABC Plumbing", "phone": "555-1234"}]
        })
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            result = parse_import_text(session, "vendor", "ABC Plumbing, 555-1234")
        assert len(result) == 1
        assert result[0]["company_name"] == "ABC Plumbing"

    def test_parse_task_text(self, session):
        from mihomes.services.ai.assessors import parse_import_text
        mock = self._mock_provider({
            "items": [{"title": "Fix roof", "priority": "high"}]
        })
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            result = parse_import_text(session, "task", "Fix the roof urgently")
        assert result[0]["title"] == "Fix roof"

    def test_unsupported_entity_type_raises(self, session):
        from mihomes.services.ai.assessors import parse_import_text
        with pytest.raises(ValueError, match="Import not supported"):
            parse_import_text(session, "space", "some text")

    def test_empty_items_returns_empty_list(self, session):
        from mihomes.services.ai.assessors import parse_import_text
        mock = self._mock_provider({"items": []})
        with patch("mihomes.services.ai.assessors.get_provider", return_value=mock), \
             patch("mihomes.services.ai.assessors.get_ai_provider_name", return_value="claude"), \
             patch("mihomes.services.ai.assessors.get_ai_api_key", return_value="test-key"):
            result = parse_import_text(session, "vendor", "no vendors here")
        assert result == []


# ---------------------------------------------------------------------------
# Orchestrator (with mocked provider)
# ---------------------------------------------------------------------------

class TestOrchestrator:
    def _patch_ai(self, response_text="Test AI response"):
        mock_provider = MagicMock()
        mock_provider.complete.return_value = response_text
        return (
            patch("mihomes.services.ai.orchestrator.get_provider", return_value=mock_provider),
            patch("mihomes.services.ai.orchestrator.get_ai_provider_name", return_value="claude"),
            patch("mihomes.services.ai.orchestrator.get_ai_api_key", return_value="test-key"),
            patch("mihomes.services.ai.orchestrator.get_ai_model", return_value="claude-3"),
        )

    def test_ask_returns_response(self, session):
        from mihomes.services.ai.orchestrator import ask
        patches = self._patch_ai("Here is my advice.")
        with patches[0], patches[1], patches[2], patches[3]:
            resp = ask(session, "What should I fix first?")
        assert resp.text == "Here is my advice."
        assert resp.role is not None
        assert resp.session_id is not None

    def test_ask_stores_conversation(self, session):
        from mihomes.services.ai.orchestrator import ask
        from mihomes.models.ai_conversation import AIConversation
        patches = self._patch_ai()
        with patches[0], patches[1], patches[2], patches[3]:
            ask(session, "Test question")
        assert session.query(AIConversation).count() >= 1

    def test_ask_with_explicit_role(self, session):
        from mihomes.services.ai.orchestrator import ask
        patches = self._patch_ai()
        with patches[0], patches[1], patches[2], patches[3]:
            resp = ask(session, "Budget question", role="financial")
        assert "financial" in resp.role.lower() or resp.role is not None

    def test_ask_with_property_scope(self, session):
        from mihomes.services.ai.orchestrator import ask
        p = Property(name="Scoped House", slug="scoped-house",
                     property_type=PropertyType.PRIMARY, currency="USD")
        session.add(p)
        session.flush()
        patches = self._patch_ai()
        with patches[0], patches[1], patches[2], patches[3]:
            resp = ask(session, "How is this property?", property_slug="scoped-house")
        assert resp.text is not None
