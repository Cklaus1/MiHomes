"""Tests for playbook service."""

from pathlib import Path
from unittest.mock import patch

import pytest

from mihomes.models.property import Property, PropertyType
from mihomes.services.playbook import (
    _extract_checklist_items,
    _parse_frontmatter,
    _slug_to_name,
    get_playbook,
    kb_path,
    knowledge_search,
    list_playbooks,
    playbooks_dir,
    run_playbook,
)


# ── kb_path ───────────────────────────────────────────────────────────────────

class TestKbPath:
    def test_returns_path_object(self):
        result = kb_path()
        assert isinstance(result, Path)

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        result = kb_path()
        assert result == tmp_path

    def test_default_path_is_knowledge_dir(self, monkeypatch):
        monkeypatch.delenv("MIHOMES_KB", raising=False)
        result = kb_path()
        assert result.name == "knowledge"


# ── _slug_to_name ─────────────────────────────────────────────────────────────

class TestSlugToName:
    def test_converts_hyphens_to_spaces(self):
        assert _slug_to_name("spring-opening") == "Spring Opening"

    def test_capitalizes_words(self):
        assert _slug_to_name("guest-turnover") == "Guest Turnover"

    def test_single_word(self):
        assert _slug_to_name("maintenance") == "Maintenance"


# ── _parse_frontmatter ────────────────────────────────────────────────────────

class TestParseFrontmatter:
    def test_parses_name_and_type(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text(
            "# Spring Opening\n\n**Type**: seasonal\n**Owner**: Lead Housekeeper\n---\n\n## Step 1\n",
            encoding="utf-8",
        )
        meta = _parse_frontmatter(f)
        assert meta["type"] == "seasonal"
        assert meta["owner"] == "Lead Housekeeper"

    def test_stops_at_subheading(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\n**Type**: daily\n\n## Section\n**Owner**: Not Here\n", encoding="utf-8")
        meta = _parse_frontmatter(f)
        assert "owner" not in meta

    def test_stops_at_horizontal_rule(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\n**Type**: monthly\n---\n**Owner**: After Rule\n", encoding="utf-8")
        meta = _parse_frontmatter(f)
        assert "owner" not in meta

    def test_returns_empty_dict_for_no_meta(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Just a title\n\nSome text here.", encoding="utf-8")
        meta = _parse_frontmatter(f)
        assert meta == {}

    def test_estimated_time_parsed(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("# Title\n\n**Estimated Time**: 2 hours\n---\n", encoding="utf-8")
        meta = _parse_frontmatter(f)
        assert meta["estimated time"] == "2 hours"


# ── _extract_checklist_items ──────────────────────────────────────────────────

class TestExtractChecklistItems:
    def test_extracts_simple_checklist(self):
        content = "## Section\n\n- [ ] Task one\n- [ ] Task two\n"
        items = _extract_checklist_items(content)
        assert len(items) == 2
        assert items[0]["title"] == "Task one"
        assert items[1]["title"] == "Task two"

    def test_tracks_section_headers(self):
        content = "## Morning\n\n- [ ] Make bed\n\n## Evening\n\n- [ ] Lock doors\n"
        items = _extract_checklist_items(content)
        assert items[0]["section"] == "Morning"
        assert items[1]["section"] == "Evening"

    def test_day_offset_increments_per_section(self):
        content = "## Day 1\n\n- [ ] Task A\n\n## Day 2\n\n- [ ] Task B\n"
        items = _extract_checklist_items(content)
        assert items[0]["day_offset"] == 0
        assert items[1]["day_offset"] == 1

    def test_ignores_completed_checkboxes(self):
        content = "## Section\n\n- [ ] Open task\n- [x] Done task\n"
        items = _extract_checklist_items(content)
        # Only unchecked items match the pattern
        assert all("[ ]" not in item["title"] for item in items)

    def test_empty_content_returns_empty(self):
        assert _extract_checklist_items("# Title\n\nNo checklist here.") == []

    def test_subsection_header_does_not_increment_offset(self):
        content = "## Section One\n\n### Subsection\n\n- [ ] Task\n"
        items = _extract_checklist_items(content)
        assert items[0]["day_offset"] == 0


# ── list_playbooks ────────────────────────────────────────────────────────────

class TestListPlaybooks:
    def test_returns_empty_when_no_playbooks_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        result = list_playbooks()
        assert result == []

    def test_lists_markdown_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        (pb_dir / "daily-checklist.md").write_text(
            "# Daily Checklist\n\n**Type**: daily\n**Owner**: Housekeeper\n---\n\n## Morning\n\n- [ ] Sweep\n",
            encoding="utf-8",
        )
        (pb_dir / "weekly-review.md").write_text(
            "# Weekly Review\n\n**Type**: weekly\n---\n",
            encoding="utf-8",
        )
        result = list_playbooks()
        slugs = [r["slug"] for r in result]
        assert "daily-checklist" in slugs
        assert "weekly-review" in slugs

    def test_ignores_non_md_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        (pb_dir / "ignore.txt").write_text("text file")
        result = list_playbooks()
        assert result == []

    def test_playbook_entry_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        (pb_dir / "test-playbook.md").write_text("# Test\n\n**Type**: daily\n---\n")
        result = list_playbooks()
        assert len(result) == 1
        entry = result[0]
        assert "slug" in entry
        assert "name" in entry
        assert "type" in entry
        assert "file" in entry


# ── get_playbook ──────────────────────────────────────────────────────────────

class TestGetPlaybook:
    def _write_playbook(self, pb_dir, slug, content):
        (pb_dir / f"{slug}.md").write_text(content, encoding="utf-8")

    def test_returns_none_for_missing_playbook(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        (tmp_path / "playbooks").mkdir()
        result = get_playbook("nonexistent")
        assert result is None

    def test_returns_playbook_dict(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(
            pb_dir, "spring-opening",
            "# Spring Opening\n\n**Type**: seasonal\n**Owner**: Lead Housekeeper\n---\n\n## Prepare\n\n- [ ] Turn on water\n- [ ] Check HVAC\n",
        )
        result = get_playbook("spring-opening")
        assert result is not None
        assert result["slug"] == "spring-opening"
        assert result["type"] == "seasonal"
        assert len(result["checklist"]) == 2

    def test_partial_match_works(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(pb_dir, "spring-opening", "# Spring Opening\n\n- [ ] Task\n")
        result = get_playbook("spring")
        assert result is not None

    def test_content_returned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(pb_dir, "test-pb", "# Test\n\n- [ ] Do something\n")
        result = get_playbook("test-pb")
        assert "Do something" in result["content"]


# ── run_playbook ──────────────────────────────────────────────────────────────

class TestRunPlaybook:
    def _write_playbook(self, pb_dir, slug, content):
        (pb_dir / f"{slug}.md").write_text(content, encoding="utf-8")

    @pytest.fixture
    def prop(self, session):
        p = Property(name="Belle Estate", slug="belle-estate",
                     property_type=PropertyType.PRIMARY)
        session.add(p)
        session.flush()
        return p

    def test_creates_tasks_from_checklist(self, session, tmp_path, monkeypatch, prop):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(
            pb_dir, "spring-check",
            "# Spring Check\n\n**Type**: seasonal\n---\n\n## Prep\n\n- [ ] Turn on water\n- [ ] Check roof\n",
        )
        tasks = run_playbook(session, "spring-check", prop.slug)
        assert len(tasks) == 2
        titles = [t.title for t in tasks]
        assert "Turn on water" in titles
        assert "Check roof" in titles

    def test_raises_for_unknown_playbook(self, session, tmp_path, monkeypatch, prop):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        (tmp_path / "playbooks").mkdir()
        with pytest.raises(ValueError, match="not found"):
            run_playbook(session, "nonexistent-pb", prop.slug)

    def test_raises_for_empty_checklist(self, session, tmp_path, monkeypatch, prop):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(pb_dir, "empty-pb", "# Empty Playbook\n\nNo tasks here.\n")
        with pytest.raises(ValueError, match="no checklist items"):
            run_playbook(session, "empty-pb", prop.slug)

    def test_day_offset_applied(self, session, tmp_path, monkeypatch, prop):
        from datetime import date, timedelta
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        pb_dir = tmp_path / "playbooks"
        pb_dir.mkdir()
        self._write_playbook(
            pb_dir, "multi-day",
            "# Multi Day\n\n## Day 1\n\n- [ ] Task A\n\n## Day 2\n\n- [ ] Task B\n",
        )
        today = date.today()
        tasks = run_playbook(session, "multi-day", prop.slug, start_date=today)
        task_a = next(t for t in tasks if t.title == "Task A")
        task_b = next(t for t in tasks if t.title == "Task B")
        assert task_a.due_date == today
        assert task_b.due_date == today + timedelta(days=1)


# ── knowledge_search ──────────────────────────────────────────────────────────

class TestKnowledgeSearch:
    def test_returns_empty_when_no_kb(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path / "nonexistent"))
        results = knowledge_search("HVAC")
        assert results == []

    def test_finds_matches_in_markdown_files(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        f = tmp_path / "notes.md"
        f.write_text("Check the HVAC system every spring.\nReplace filters quarterly.", encoding="utf-8")
        results = knowledge_search("HVAC")
        assert len(results) == 1
        assert results[0]["file"] == "notes.md"

    def test_case_insensitive_search(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        f = tmp_path / "guide.md"
        f.write_text("Pool maintenance is important.\nCheck pool weekly.", encoding="utf-8")
        results = knowledge_search("POOL")
        assert len(results) == 1

    def test_returns_empty_for_no_match(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        f = tmp_path / "info.md"
        f.write_text("This file is about gardening.", encoding="utf-8")
        results = knowledge_search("plumbing")
        assert results == []

    def test_caps_at_5_match_lines_per_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        lines = ["HVAC system check\n"] * 10
        f = tmp_path / "hvac.md"
        f.write_text("".join(lines), encoding="utf-8")
        results = knowledge_search("HVAC")
        assert len(results[0]["matches"]) <= 5

    def test_result_has_expected_keys(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MIHOMES_KB", str(tmp_path))
        f = tmp_path / "test.md"
        f.write_text("Irrigation system maintenance.", encoding="utf-8")
        results = knowledge_search("irrigation")
        assert "file" in results[0]
        assert "title" in results[0]
        assert "matches" in results[0]
