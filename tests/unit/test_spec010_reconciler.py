"""The SPEC-010 reconciler's two exemption tables must not outlive their reasons.

`scripts/spec010_reconcile.py` carries two dicts that let the harness disagree with the spec:

* ``SPEC_NODE_ID_CORRECTIONS`` — §8 names a test that does not exist under that name.
* ``PENDING_TESTS_IN_EXISTING_FILES`` — a task's test is new but its *file* already exists, so
  ``--collect`` cannot infer "unbuilt" from a missing file.

Both are sanctioned drift. That is the problem: an exemption with no expiry is how a one-time
correction becomes permanent blindness, and the reconciler exists precisely to stop the harness
quietly disagreeing with the spec. These tests are the expiry.

**This file gates the tooling, not a §8 criterion.** It is deliberately not in the DAG's criteria
column: `--collect` does not check it, which is the same gap SPEC-005's BD14 recorded when a
non-criterion node id in the DAG turned out to be wrong and nothing noticed.
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "spec010_reconcile.py"


def _load():
    spec = importlib.util.spec_from_file_location("spec010_reconcile", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def rec():
    return _load()


class TestCorrectionsExpire:
    """Every ``SPEC_NODE_ID_CORRECTIONS`` entry must still be *needed* and still be *right*."""

    def test_the_wrong_name_really_is_wrong(self, rec):
        """If a spec edit ever makes §8's name correct, the entry becomes a lie.

        A correction that is no longer needed does not merely sit there — it permits the harness
        to name a test the spec does not, which is the exact drift the reconciler's third check
        exists to catch. Deleting the entry is the fix; this test is what forces the deletion.
        """
        for label, (wrong, right) in rec.SPEC_NODE_ID_CORRECTIONS.items():
            spec_text = rec.SPEC.read_text(encoding="utf-8")
            criteria = rec.spec_criteria(spec_text)
            assert label in criteria, f"{label}: corrected but §8 no longer defines it"
            declared = criteria[label][1]
            assert declared.endswith(f"::{wrong}"), (
                f"{label}: the correction says §8 names `{wrong}`, but §8 now names "
                f"`{declared}`. If the spec was fixed, DELETE this entry — leaving it lets the "
                f"harness disagree with the spec unnoticed."
            )
            assert wrong != right, f"{label}: a correction that changes nothing is not a correction"

    def test_the_right_name_actually_resolves(self, rec):
        """The corrected name must name a real test.

        Without this, the table could substitute one non-existent name for another and the
        document-level checks would still pass — trading a visible wrong name for an invisible
        one. Measured by pytest, not by reading.
        """
        spec_text = rec.SPEC.read_text(encoding="utf-8")
        criteria = rec.spec_criteria(spec_text)
        manifest = rec.spec_manifest(spec_text)
        for label, (_wrong, right) in rec.SPEC_NODE_ID_CORRECTIONS.items():
            basename = criteria[label][1].split("::")[0]
            nid = f"{manifest[basename]}/{basename}::{right}"
            r = subprocess.run(
                [sys.executable, "-m", "pytest", nid, "--collect-only", "-q", "--color=no"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            assert r.returncode == 0, (
                f"{label}: the correction points at `{nid}`, which does not resolve either"
            )


class TestPendingSetExpires:
    """``PENDING_TESTS_IN_EXISTING_FILES`` must shrink to empty as groups land."""

    def test_every_pending_task_exists_in_the_dag(self, rec):
        """An entry naming a task the DAG does not have is stale by construction."""
        harness = rec.HARNESS.read_text(encoding="utf-8")
        verifies = rec.harness_verifies(harness)
        for task_id in rec.PENDING_TESTS_IN_EXISTING_FILES:
            assert task_id in verifies, (
                f"{task_id}: listed as pending but the DAG has no such task — DELETE the entry"
            )

    def test_a_pending_test_that_now_exists_must_be_removed(self, rec):
        """**The expiry itself.**

        The moment a group lands, its test resolves — and the entry must go, or that node id is
        permanently exempt from `--collect` and could later break silently. This is the negative
        assertion SPEC-005 BD19 warns about, so it is paired: the test asserts the entry is
        *needed*, which is only meaningful because a resolving node id makes it fail.
        """
        harness = rec.HARNESS.read_text(encoding="utf-8")
        verifies = rec.harness_verifies(harness)
        for task_id in sorted(rec.PENDING_TESTS_IN_EXISTING_FILES):
            for nid in rec.node_ids(verifies[task_id]):
                if not (ROOT / nid.split("::")[0]).exists():
                    continue
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", nid, "--collect-only", "-q", "--color=no"],
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                )
                assert r.returncode != 0, (
                    f"{task_id}: `{nid}` now RESOLVES, so the group has landed. Remove "
                    f"{task_id!r} from PENDING_TESTS_IN_EXISTING_FILES — leaving it exempts a "
                    f"real node id from the collect check forever."
                )


class TestTheReconcilerItselfHolds:
    """The gate must be green on the committed harness, and must be capable of failing."""

    def test_reconcile_passes_on_the_committed_harness(self, rec):
        r = subprocess.run(
            [sys.executable, str(SCRIPT)], capture_output=True, text=True, cwd=ROOT
        )
        assert r.returncode == 0, f"reconciler red on the committed harness:\n{r.stderr}"

    def test_an_ungated_criterion_is_detected(self, rec, tmp_path, monkeypatch):
        """Mutation M1, as a permanent test rather than a one-off.

        A reconciler that cannot report an ungated criterion is decoration. Rather than trusting
        the manual mutation, this rewrites the harness in a temp copy with A2 — the phase's
        definition of done — ungated, and asserts the check fires.
        """
        harness = rec.HARNESS.read_text(encoding="utf-8")
        assert "· A2 ·" in harness, "anchor moved; update this test"
        broken = tmp_path / "harness.md"
        broken.write_text(harness.replace("· A2 ·", "· — ·", 1), encoding="utf-8")

        monkeypatch.setattr(rec, "HARNESS", broken)
        gated = rec.harness_gated(broken.read_text(encoding="utf-8"))
        assert "A2" not in gated, (
            "the mutation did not actually ungate A2 — this test would pass vacuously"
        )
