"""The privacy check that keeps real people's data out of the repository (scripts/privacy_check.py).

Lines below that deliberately contain real-looking values carry the allow marker, which is also
what the check does for any line that genuinely needs one.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "privacy_check", Path(__file__).resolve().parents[2] / "scripts" / "privacy_check.py"
)
pc = importlib.util.module_from_spec(_SPEC)
sys.modules["privacy_check"] = pc  # dataclasses look the module up by name
_SPEC.loader.exec_module(pc)

DENY = {pc.term_hash("Jordan Quill"), pc.term_hash("42 Larkspur Lane")}


def rules(text, denylist=DENY):
    return [f.rule for f in pc.check_line("x.py", 1, text, denylist)]


@pytest.mark.parametrize("path", [
    "knowledge/staff/candidates/someone.md",
    "knowledge/staff/handbook.md",
    "knowledge/properties/home/alarm.md",
    "backups/estate.db",
    "export/vendors.csv",
    "docs/scan.pdf",
    ".env",
    ".env.production",
    "certs/server.key",
])
def test_private_paths_are_blocked(path):
    assert pc.path_blocked(path)


@pytest.mark.parametrize("path", [
    "knowledge/properties/property-template.md",
    ".env.example",
    "tests/fixtures/import.csv",
    "src/mihomes/services/demo.py",
    "docs/img/screenshot.png",
])
def test_ordinary_paths_pass(path):
    assert not pc.path_blocked(path)


def test_real_looking_email_is_flagged():
    assert rules('owner = "pat.doe@gmail.com"') == ["email"]  # privacy-check: allow


@pytest.mark.parametrize("text", [
    'owner = "pat@example.com"',
    'sender = "no-reply@send.mihomes.ai"',
    'user = "a@b.test"',
    'bot = "123+dev@users.noreply.github.com"',
])
def test_reserved_email_domains_pass(text):
    assert rules(text) == []


def test_real_looking_phone_is_flagged():
    assert rules('phone="404-231-8812"') == ["phone"]  # privacy-check: allow


@pytest.mark.parametrize("text", ['phone="404-555-0123"', 'phone="(212) 555 0199"', "id = 2026-11-25"])
def test_fictional_numbers_and_dates_pass(text):
    assert rules(text) == []


def test_denylisted_terms_are_found_case_and_punctuation_insensitive():
    assert rules("assigned to JORDAN   quill.") == ["private term"]
    assert rules("ship to 42 Larkspur Lane, apt 3") == ["private term"]


def test_unlisted_text_passes():
    assert rules("assigned to Jordan Smith at 42 Larkspur Court") == []


def test_allow_marker_skips_a_line():
    assert rules("call Jordan Quill  # privacy-check: allow") == []


def test_hash_ignores_case_and_spacing():
    assert pc.term_hash("  Jordan  QUILL ") == pc.term_hash("jordan quill")


def test_denylist_file_ignores_comments(tmp_path):
    f = tmp_path / "deny"
    f.write_text("# people\n" + pc.term_hash("x") + "  # trailing\n\n")
    assert pc.load_denylist(f) == {pc.term_hash("x")}


def test_the_repository_denylist_loads():
    assert len(pc.load_denylist()) > 0
