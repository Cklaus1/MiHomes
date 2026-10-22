"""Tests for the playbook service — per-account playbooks (2026-10-21).

Playbooks used to be files in `knowledge/playbooks/`, listed for every account; they are now
`Playbook` rows. Parser and knowledge-search tests are unchanged; listing, permissions, the
template gallery, import and run are exercised against the database.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from mihomes.services import playbook as pb_svc
from mihomes.services.playbook import (
    _extract_checklist_items,
    _slug_to_name,
    kb_path,
    knowledge_search,
    parse_markdown,
    step_keys,
)

OWNER = SimpleNamespace(role="owner", can_edit_playbooks=False)
ADMIN = SimpleNamespace(role="admin", can_edit_playbooks=False)
STAFF = SimpleNamespace(role="staff", can_edit_playbooks=False)
STAFF_EDITOR = SimpleNamespace(role="staff", can_edit_playbooks=True)


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


# ── parse_markdown ────────────────────────────────────────────────────────────

class TestParseMarkdown:
    def test_parses_title_and_metadata(self):
        meta = parse_markdown(
            "# Spring Opening\n\n**Type**: seasonal\n**Owner**: Lead Housekeeper\n"
            "**Estimated Time**: 2 hours\n---\n\n## Step 1\n- [ ] Open shutters\n"
        )
        assert meta["name"] == "Spring Opening"
        assert meta["kind"] == "seasonal"
        assert meta["audience"] == "Lead Housekeeper"
        assert meta["estimated_time"] == "2 hours"
        assert "Open shutters" in meta["content"]  # the whole text is kept

    def test_stops_at_subheading(self):
        meta = parse_markdown("# Title\n\n**Type**: daily\n\n## Section\n**Owner**: Not Here\n")
        assert meta["audience"] is None

    def test_stops_at_horizontal_rule(self):
        meta = parse_markdown("# Title\n\n**Type**: monthly\n---\n**Owner**: After Rule\n")
        assert meta["audience"] is None

    def test_fallback_name_without_title(self):
        assert parse_markdown("No title here", fallback_name="Fallback")["name"] == "Fallback"


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

    def test_ignores_completed_checkboxes(self):
        content = "## Section\n\n- [ ] Open task\n- [x] Done task\n"
        items = _extract_checklist_items(content)
        # Only unchecked items match the pattern
        assert all("[ ]" not in item["title"] for item in items)

    def test_empty_content_returns_empty(self):
        assert _extract_checklist_items("# Title\n\nNo checklist here.") == []


# ── permissions ───────────────────────────────────────────────────────────────

class TestCanEdit:
    def test_owner_and_admin_always(self):
        assert pb_svc.can_edit(OWNER) and pb_svc.can_edit(ADMIN)

    def test_staff_only_with_the_grant(self):
        assert not pb_svc.can_edit(STAFF)
        assert pb_svc.can_edit(STAFF_EDITOR)

    def test_no_membership_cannot(self):
        assert not pb_svc.can_edit(None)

    def test_writes_refuse_staff_without_grant(self, session):
        with pytest.raises(pb_svc.PlaybookPermissionError):
            pb_svc.create_playbook(session, STAFF, name="Nope", content="- [ ] x")


# ── create / update / delete ──────────────────────────────────────────────────

class TestWrites:
    def test_create_list_get(self, session):
        pb = pb_svc.create_playbook(session, ADMIN, name="Guest Arrival",
                                    content="## Before\n- [ ] Air out rooms\n", kind="one-off")
        assert pb.slug == "guest-arrival"
        assert [p.slug for p in pb_svc.list_playbooks(session)] == ["guest-arrival"]
        assert pb_svc.checklist(pb_svc.get_playbook(session, "guest-arrival"))[0]["title"] == "Air out rooms"

    def test_staff_editor_can_write(self, session):
        pb = pb_svc.create_playbook(session, STAFF_EDITOR, name="Linen Rotation", content="")
        assert pb.name == "Linen Rotation"

    def test_update_renames_slug_and_content(self, session):
        pb_svc.create_playbook(session, OWNER, name="Old Name", content="- [ ] a")
        pb = pb_svc.update_playbook(session, OWNER, "old-name", name="New Name",
                                    content="- [ ] a\n- [ ] b")
        assert pb.slug == "new-name" and len(pb_svc.checklist(pb)) == 2

    def test_delete(self, session):
        pb_svc.create_playbook(session, OWNER, name="Temp", content="")
        assert pb_svc.delete_playbook(session, OWNER, "temp") == "Temp"
        assert pb_svc.get_playbook(session, "temp") is None

    def test_name_required(self, session):
        with pytest.raises(ValueError, match="name"):
            pb_svc.create_playbook(session, OWNER, name="   ", content="")


# ── template gallery ──────────────────────────────────────────────────────────

class TestTemplates:
    def test_gallery_has_the_seven_generic_templates(self):
        slugs = {t["slug"] for t in pb_svc.list_templates()}
        assert slugs == {
            "housekeeping-standards", "daily-operations", "emergency-response", "hiring",
            "onboarding-new-hire", "offboarding", "household-communication",
        }
        assert all(t["steps"] > 0 for t in pb_svc.list_templates())

    def test_unknown_template_is_none_not_a_path(self):
        assert pb_svc.get_template("../../../etc/passwd") is None

    def test_copy_creates_an_editable_playbook(self, session):
        pb = pb_svc.copy_template(session, ADMIN, "emergency-response")
        assert pb.name and pb_svc.checklist(pb)
        again = pb_svc.copy_template(session, ADMIN, "emergency-response")
        assert again.slug != pb.slug  # a second copy gets its own slug

    def test_copy_needs_an_editor(self, session):
        with pytest.raises(pb_svc.PlaybookPermissionError):
            pb_svc.copy_template(session, STAFF, "hiring")


# ── import ────────────────────────────────────────────────────────────────────

class TestImport:
    def test_import_is_idempotent(self, session):
        text = "# Spring Check\n\n**Type**: seasonal\n---\n\n## Prep\n- [ ] Turn on water\n"
        pb, created = pb_svc.import_markdown(session, "spring-check", text)
        assert created and pb.kind == "seasonal"
        _, created_again = pb_svc.import_markdown(session, "spring-check", text)
        assert not created_again
        assert len(pb_svc.list_playbooks(session)) == 1


# ── step keys ─────────────────────────────────────────────────────────────────

class TestStepKeys:
    def test_one_key_per_step_in_order(self):
        keys = step_keys("## A\n- [ ] One\n- note\n- [ ] Two\n")
        assert [t for _, t in keys] == ["One", "Two"]
        assert len({k for k, _ in keys}) == 2

    def test_reordering_keeps_a_steps_key(self):
        before = dict((t, k) for k, t in step_keys("- [ ] One\n- [ ] Two\n"))
        after = dict((t, k) for k, t in step_keys("- [ ] Two\n- [ ] One\n"))
        assert before == after

    def test_rewording_changes_that_steps_key_only(self):
        before = dict((t, k) for k, t in step_keys("- [ ] One\n- [ ] Two\n"))
        after = dict((t, k) for k, t in step_keys("- [ ] One\n- [ ] Two!\n"))
        assert before["One"] == after["One"] and "Two" not in after

    def test_the_same_text_twice_gets_two_keys(self):
        keys = step_keys("## Day\n- [ ] Lock the doors\n## Night\n- [ ] Lock the doors\n")
        assert len(keys) == 2 and keys[0][0] != keys[1][0]

    def test_case_and_spacing_do_not_matter(self):
        a = step_keys("- [ ] Lock  the Doors\n")[0][0]
        b = step_keys("- [ ] lock the doors\n")[0][0]
        assert a == b


# ── ticking ───────────────────────────────────────────────────────────────────

class TestTicks:
    @pytest.fixture
    def pb(self, session):
        return pb_svc.create_playbook(
            session, OWNER, name="Daily Close",
            content="## Close\n- [ ] Lock the doors\n- [ ] Set the alarm\n",
        )

    def _key(self, pb, title):
        return next(k for k, t in step_keys(pb.content) if t == title)

    def test_tick_then_untick(self, session, pb):
        key = self._key(pb, "Lock the doors")
        pb_svc.set_check(session, pb, None, key, True)
        assert key in pb_svc.get_checks(session, pb)
        pb_svc.set_check(session, pb, None, key, False)
        assert pb_svc.get_checks(session, pb) == {}

    def test_ticking_twice_is_harmless(self, session, pb):
        key = self._key(pb, "Lock the doors")
        pb_svc.set_check(session, pb, None, key, True)
        pb_svc.set_check(session, pb, None, key, True)
        assert len(pb_svc.get_checks(session, pb)) == 1

    def test_a_stale_key_is_refused(self, session, pb):
        with pytest.raises(ValueError, match="changed"):
            pb_svc.set_check(session, pb, None, "0" * 40, True)

    def test_tick_time_is_in_the_estate_time_zone(self, session, pb):
        from zoneinfo import ZoneInfo

        from mihomes.services.config_service import set_config

        set_config(session, "locale.timezone", "America/New_York")
        key = self._key(pb, "Lock the doors")
        pb_svc.set_check(session, pb, None, key, True)
        at = pb_svc.get_checks(session, pb)[key]["at"]
        assert at.tzinfo is not None and at.utcoffset() == ZoneInfo("America/New_York").utcoffset(at.replace(tzinfo=None))

    def test_editing_prunes_ticks_for_removed_steps(self, session, pb):
        lock, alarm = self._key(pb, "Lock the doors"), self._key(pb, "Set the alarm")
        pb_svc.set_check(session, pb, None, lock, True)
        pb_svc.set_check(session, pb, None, alarm, True)
        pb_svc.update_playbook(session, OWNER, pb.slug, name="Daily Close",
                               content="## Close\n- [ ] Lock the doors\n- [ ] Check windows\n")
        assert set(pb_svc.get_checks(session, pb)) == {lock}

    def test_reset_unticks_everything_and_needs_an_editor(self, session, pb):
        for key, _ in step_keys(pb.content):
            pb_svc.set_check(session, pb, None, key, True)
        with pytest.raises(pb_svc.PlaybookPermissionError):
            pb_svc.reset_checks(session, STAFF, pb.slug)
        assert len(pb_svc.get_checks(session, pb)) == 2
        assert pb_svc.reset_checks(session, STAFF_EDITOR, pb.slug) == 2
        assert pb_svc.get_checks(session, pb) == {}


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
