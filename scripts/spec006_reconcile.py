"""F.3b, executable — reconcile `tasks/build-loop-spec006.md`'s DAG against SPEC-006 §8.

Same construction as `spec005_reconcile.py`, and it exists for the same reason: SPEC-005's first
DAG was assigned by hand and was wrong three ways at once (ten criteria ungated, seven groups
mislabelled, nineteen `verify:` paths naming files the spec does not use). The rule earned there —
**derive the criteria column, never type it** — is what this script enforces here.

    py scripts/spec006_reconcile.py             # 0 = DAG and spec agree
    py scripts/spec006_reconcile.py --collect   # + every declared node id resolves

Three sources are joined:

* **§8** — the criteria table: ``{label: (text, declared_test)}``. Authoritative for *what* each
  label means and *which test* discharges it.
* **§9** — the test manifest: ``{basename: tests/unit | tests/integration}``. Authoritative for
  where a test file lives; §8 gives bare basenames.
* **§1** — this harness's DAG, which is what gets checked.

`--collect` is the check SPEC-005's first reconciler lacked (BD2): that each declared node id
**resolves to a real test**, not merely that two documents agree with each other. It is not
optional politeness here — the SPEC-006 pre-flight ran it against the three existing-file criteria
the spec names and **all three node ids were wrong** (see the harness §0.6 C6). A document-only
check would have passed on every one.

Labels are parsed as ``A\\d+[a-z]?`` even though SPEC-006's set is plain `A1`–`A25` with no
lettered members. Conventions C7 forbids range-checking regardless of whether the current set
happens to be gapless: the numeric set being contiguous today is not a property the next edit
preserves, and a range check reports a clean pass while never having looked.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "specs" / "SPEC-006-gateways-tenancy-webhook-cloud-api.md"
HARNESS = ROOT / "tasks" / "build-loop-spec006.md"

LABEL_RE = re.compile(r"A\d+[a-z]?")

# §8 node ids that do not resolve, and the real name, each with the measurement that found it.
#
# **This table is a liability, not a convenience.** Every entry weakens the join it sits inside:
# the whole point of the reconciler is that the harness cannot quietly disagree with the spec, and
# each row here is a sanctioned disagreement. So the bar is that the correction was verified by
# `pytest --collect-only` against the real file, and the harness records it in §0.6 C6 where a
# reader meets it.
#
# `tests/unit/test_spec006_reconciler.py::TestCorrectionsExpire` keeps this honest: if a spec
# edit ever makes one of these names correct, the entry becomes a lie that silently permits real
# drift, and that test fails until it is deleted. An override table with no expiry is how a
# one-time correction becomes permanent blindness.
# Tasks whose test is NEW but lives in a file that already exists (§9's "extend, do not replace").
#
# `--collect` treats a missing *file* as an unbuilt group, which is correct. It cannot make the
# same inference for a missing *test in a present file* — that is indistinguishable from the
# wrong-name defect C6 found four times. So the distinction is declared rather than guessed.
#
# **Each entry must be deleted the moment its group lands.** An entry that outlives its group
# silently exempts a real node id from the collect check — the same "sanctioned disagreement"
# hazard as SPEC_NODE_ID_CORRECTIONS below, and the reason G-Final's F.3b must run with this set
# empty. Verified by mutation (M4): removing an entry turns its unresolved node id red.
#
# **Write these tests at MODULE level** (harness §0.6 C10). Two of the three files nest every
# test in a class, so following the local convention produces a node id §8 does not declare and
# `--collect` cannot resolve. The expiry test below does NOT catch that: it asserts the node id
# does *not* resolve, and a nested test does not resolve either — so the entry would survive, the
# group would read as landed, and that criterion would be exempt from `--collect` forever. That
# is M5's failure through a door M5 does not cover, which is why the instruction is here rather
# than only in the harness.
PENDING_TESTS_IN_EXISTING_FILES = {
    "G4.2",  # A12 — test_gateway_safety.py (flat), new: trust scoped to an account
    "G4.3",  # A13 — test_gateway_property_resolution.py — NESTED file, write flat
    "G8.1",  # A21 — test_staff_pto.py — NESTED file, write flat
}

SPEC_NODE_ID_CORRECTIONS = {
    # A2 is the load-bearing one: it is the *prerequisite's own* verification step (§6 P2,
    # "the six existing gateway test files pass (A2)"), so trusting §8 would gate the
    # prerequisite on a name pytest answers "not found" to — not red, just absent.
    "A2": ("test_superset_schema", "test_schema_enum_is_superset"),
}


def label_order(label: str) -> tuple[int, str]:
    """Sort `A9` before `A10` — never by string, and never by int alone (a lettered
    label like `A14b` must sort after `A14` and before `A15`)."""
    return int(re.match(r"A(\d+)", label).group(1)), label


def spec_criteria(spec: str) -> dict[str, tuple[str, str]]:
    """§8's table: label -> (criterion text, declared test).

    The declared-test cell often carries a parenthetical aside — "(existing file, extended)" —
    which is prose about provenance, not part of the node id. Stripped here so the collect check
    compares an id against an id.
    """
    out: dict[str, tuple[str, str]] = {}
    body = spec.split("## 8. Acceptance criteria")[1].split("## 9.")[0]
    for label, text, test in re.findall(
        r"^\|\s*\*{0,2}(A\d+[a-z]?)\*{0,2}\s*\|(.+?)\|(.+?)\|\s*$", body, re.M
    ):
        declared = test.strip()
        declared = re.sub(r"\(.*?\)", "", declared).strip()  # drop "(existing file, extended)"
        out[label] = (text.strip().strip("*").strip(), declared.strip("`").strip())
    return out


def spec_manifest(spec: str) -> dict[str, str]:
    """§9's manifest: test basename -> the directory it lives in.

    §8 names bare basenames; only §9 says whether a file is a unit or an integration test. A
    harness that guesses the directory produces a node id that cannot resolve, which is what
    `--collect` catches and what a document-only comparison never would.
    """
    out: dict[str, str] = {}
    body = spec.split("## 9. Test manifest")[1]
    for path in re.findall(r"(tests/(?:unit|integration)/[\w./]+\.py)", body):
        out[pathlib.PurePosixPath(path).name] = str(pathlib.PurePosixPath(path).parent)
    return out


def harness_gated(harness: str) -> dict[str, list[str]]:
    """§1's DAG: label -> the task ids that claim to gate it.

    A label claimed by two tasks is not an error — a criterion can legitimately be discharged in
    parts — but a label claimed by none is condition B failing, and that is the whole point.
    """
    out: dict[str, list[str]] = {}
    dag = harness.split("## 1. Task DAG")[1].split("## 2.")[0]
    for line in dag.splitlines():
        m = re.match(r"^\s*-\s*\[[x!\s]\]\s*(\S+)\s*·", line)
        if not m:
            continue
        task_id = m.group(1)
        cells = [c.strip() for c in line.split("·")]
        if len(cells) < 3:
            continue
        for label in LABEL_RE.findall(cells[2]):
            out.setdefault(label, []).append(task_id)
    return out


def harness_verifies(harness: str) -> dict[str, str]:
    """§1's DAG: task id -> the node id its `verify:` cell names."""
    out: dict[str, str] = {}
    dag = harness.split("## 1. Task DAG")[1].split("## 2.")[0]
    for line in dag.splitlines():
        m = re.match(r"^\s*-\s*\[[x!\s]\]\s*(\S+)\s*·", line)
        if not m:
            continue
        v = re.search(r"verify:\s*`?([^`\n]+)`?", line)
        if v:
            out[m.group(1)] = v.group(1).strip()
    return out


def node_ids(text: str) -> list[str]:
    """Every `path::test` node id mentioned, in order."""
    return re.findall(r"(tests/[\w./]+\.py::[\w:]+)", text)


def main() -> int:
    spec = SPEC.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    criteria = spec_criteria(spec)
    manifest = spec_manifest(spec)
    gated = harness_gated(harness)
    verifies = harness_verifies(harness)

    problems: list[str] = []

    # --- 1. every §8 criterion is gated by some DAG task -----------------
    for label in sorted(criteria, key=label_order):
        if label not in gated:
            problems.append(
                f"{label}: declared in §8 ({criteria[label][0][:60]}...) but NO DAG task gates it"
            )

    # --- 2. no DAG task cites a label §8 does not define -----------------
    for label in sorted(gated, key=label_order):
        if label not in criteria:
            problems.append(
                f"{label}: gated by {gated[label]} but §8 defines no such criterion"
            )

    # --- 3. the gate points at the test §8 declares, in the dir §9 gives -
    # This is the join SPEC-005's first harness got wrong nineteen times.
    for label in sorted(criteria, key=label_order):
        if label not in gated:
            continue
        declared_test = criteria[label][1]
        if "::" not in declared_test:
            continue
        basename = declared_test.split("::")[0]
        directory = manifest.get(basename)
        if directory is None:
            problems.append(
                f"{label}: §8 names `{declared_test}` but §9's manifest does not place "
                f"`{basename}` in any directory — the node id cannot be constructed"
            )
            continue
        want = f"{directory}/{declared_test}"
        wanted_name = want.split("::")[-1]
        correction = SPEC_NODE_ID_CORRECTIONS.get(label)
        for task_id in gated[label]:
            got = verifies.get(task_id, "")
            if wanted_name in got:
                continue
            if correction and correction[0] == wanted_name and correction[1] in got:
                continue  # a documented, collect-verified correction (§0.6 C6)
            problems.append(
                f"{label}: task {task_id} verifies `{got}` but §8 declares `{want}`"
            )

    # --- 4. --collect: the node id resolves to a real test ---------------
    # BD2's lesson, and the SPEC-006 pre-flight's own finding: three of the three
    # existing-file node ids §8 names do not exist under those names.
    unbuilt = 0
    if "--collect" in sys.argv:
        for task_id, node in sorted(verifies.items()):
            for nid in node_ids(node):
                path = ROOT / nid.split("::")[0]
                if not path.exists():
                    unbuilt += 1  # an unbuilt group is not a defect
                    continue
                r = subprocess.run(
                    [sys.executable, "-m", "pytest", nid, "--collect-only", "-q", "--color=no"],
                    capture_output=True,
                    text=True,
                    cwd=ROOT,
                )
                if r.returncode != 0:
                    if task_id in PENDING_TESTS_IN_EXISTING_FILES:
                        unbuilt += 1  # C6: a new test in an existing file, not yet written
                        continue
                    problems.append(
                        f"{task_id}: node id `{nid}` does not resolve "
                        f"(file exists, test name wrong)"
                    )

    total = len(criteria)
    if problems:
        print(f"RECONCILE FAILED — {len(problems)} problem(s):\n", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    resolved = len(criteria) - unbuilt
    if "--collect" in sys.argv:
        print(
            f"collect: {resolved}/{total} node ids for completed tasks resolve "
            f"({unbuilt} criteria not built yet)"
        )
    print(f"§8 criteria: {total}   DAG tasks: {len(verifies)}   gated: {len(gated)}")
    print("OK — every §8 criterion is gated, and every gate points at the test §8 declares.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
