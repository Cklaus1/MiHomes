"""F.3b, executable — reconcile `tasks/build-loop-spec005.md`'s DAG against SPEC-005 §8.

This script exists because the *first* version of the SPEC-005 harness assigned its criteria
column by hand and got seven groups wrong while leaving ten criteria ungated. The pre-flight had
already written the correct rule down (C7: parse `A\\d+[a-z]?`, never range-check) and then ran
only the left-hand side of the comparison — it enumerated the spec's labels and never built the
DAG's set to compare them against. A verification that cannot fail.

So the reconciliation is a committed script rather than a paragraph of intent, and it runs after
**every group commit** rather than once at G-Final. Exit status is the gate:

    py scripts/spec005_reconcile.py        # 0 = DAG and spec agree

Three sources are joined:

* **§8** — the criteria table: ``{label: (text, declared_test)}``. Authoritative for *what* each
  label means and *which test* discharges it.
* **§9** — the test manifest: ``{basename: tests/unit | tests/integration}``. Authoritative for
  where a test file lives. §8 gives bare basenames; §9 is the only place the directory is stated.
* **§6** — the sequenced steps, for the step→group mapping the DAG declares.

`--collect` adds the fourth check, and it is the one the first version lacked: that each declared
node id **resolves to a real test**. Without it the script compared two documents to each other
and passed while `pytest <node id>` said "not found" — G2 shipped A1/A2 into the right module for
the mechanism and the wrong module for §8, and only condition E caught it. Same half-a-comparison
shape as §0.5, one layer further in. It shells out to pytest, so it is opt-in rather than the
default: the document-level checks stay instant.
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SPEC = ROOT / "docs" / "specs" / "SPEC-005-phase4-polish-email-ga.md"
HARNESS = ROOT / "tasks" / "build-loop-spec005.md"

LABEL_RE = re.compile(r"A\d+[a-z]?")


def label_order(label: str) -> tuple[int, str]:
    """`A14b` sorts after `A14` and before `A15` — never by string, never by int alone."""
    return int(re.match(r"A(\d+)", label).group(1)), label


def spec_criteria(spec: str) -> dict[str, tuple[str, str]]:
    """§8's table: label -> (criterion text, declared test)."""
    out: dict[str, tuple[str, str]] = {}
    body = spec.split("## 8. Acceptance criteria")[1].split("## 9.")[0]
    for label, text, test in re.findall(
        r"^\|\s*\*{0,2}(A\d+[a-z]?)\*{0,2}\s*\|(.+?)\|(.+?)\|\s*$", body, re.M
    ):
        out[label] = (text.strip().strip("*").strip(), test.strip().strip("`"))
    return out


def spec_manifest(spec: str) -> dict[str, str]:
    """§9's manifest: test basename -> the directory it lives in."""
    block = spec.split("## 9. Test manifest")[1].split("```")[1]
    out: dict[str, str] = {}
    for path in re.findall(r"^(tests/\S+\.py)", block, re.M):
        out[pathlib.PurePosixPath(path).name] = str(pathlib.PurePosixPath(path).parent)
    return out


def dag_rows(harness: str) -> list[tuple[str, str, list[str], str, bool]]:
    """The DAG's task lines: (id, spec-ref, claimed labels, verify target, done).

    `done` is the checkbox, and it is what makes the `--collect` check both sound and
    tightening: a node id is only expected to resolve once the task claiming it is `[x]`.
    """
    body = harness.split("## 1. Task DAG")[1].split("## 2. Group-specific gates")[0]
    rows = []
    for line in body.splitlines():
        m = re.match(
            r"- \[([ x!])\] (G[\w.\-]+) · ([^·]+) · ([^·]+) · .*?verify: (.+?)\s*$", line
        )
        if m:
            box, gid, ref, crit, verify = m.groups()
            rows.append(
                (gid.strip(), ref.strip(), LABEL_RE.findall(crit), verify.strip().strip("`"),
                 box in "x!")
            )
    return rows


def declared_node_ids(criteria: dict, manifest: dict, labels=None) -> list[str]:
    """§8's tests as runnable pytest node ids, optionally restricted to `labels`."""
    ids = []
    for label, (_text, declared) in criteria.items():
        if labels is not None and label not in labels:
            continue
        basename = declared.split("::")[0]
        directory = manifest.get(basename)
        if directory and "::" in declared:
            ids.append(f"{directory}/{declared}")
    return sorted(set(ids))


def unresolved_node_ids(node_ids: list[str]) -> list[str]:
    """Which of the given node ids pytest cannot collect.

    The caller passes only node ids for **completed** tasks, so anything that fails to
    collect here is drift, not unbuilt work.

    **Keyed on DAG state rather than file existence**, which the first version got wrong:
    `tests/integration/test_jobs.py` already exists from SPEC-004 while §8's `test_idempotent`
    inside it belongs to G5, so a file-existence rule reports drift where the truth is "not
    built yet". Checkbox state cannot false-positive that way, and it tightens as the run
    proceeds instead of staying permissive.

    Collected with `--collect-only` rather than parsed from source: a test can come from a
    fixture, a parametrize or a class, and a `grep` for `def test_x` would miss all three.
    """
    import subprocess

    if not node_ids:
        return []

    files = sorted({n.split("::")[0] for n in node_ids})
    existing = [f for f in files if (ROOT / f).exists()]
    missing_files = [n for n in node_ids if n.split("::")[0] not in existing]
    if not existing:
        return missing_files

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--color=no", *existing],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=ROOT,
    )
    collected = {
        line.strip().replace("\\", "/")
        for line in (proc.stdout + proc.stderr).splitlines()
        if "::" in line
    }
    return sorted(missing_files + [n for n in node_ids if n not in collected
                                   and n.split("::")[0] in existing])


def main() -> int:
    spec = SPEC.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")

    criteria = spec_criteria(spec)
    manifest = spec_manifest(spec)
    rows = dag_rows(harness)

    failures: list[str] = []

    # --- B, criteria half: every §8 label is claimed by exactly one task ------------------
    claimed: dict[str, list[str]] = {}
    for gid, _ref, labels, _verify, _done in rows:
        for label in labels:
            claimed.setdefault(label, []).append(gid)

    ungated = [c for c in sorted(criteria, key=label_order) if c not in claimed]
    if ungated:
        failures.append(f"{len(ungated)} criteria have no gate: {ungated}")

    unknown = [c for c in sorted(claimed, key=label_order) if c not in criteria]
    if unknown:
        failures.append(f"DAG claims labels that are not in §8: {unknown}")

    # A label claimed twice is not automatically wrong — A21 is dual-cited by §6 Steps 2 and
    # 6 — but it must be deliberate, so it is reported for reading rather than passed over.
    doubled = {c: g for c, g in claimed.items() if len(g) > 1 and c in criteria}

    # --- E, the pointing half: each task's verify target matches §8's declared test -------
    for gid, _ref, labels, verify, _done in rows:
        if not labels:
            continue  # G5.2/G6.3/G9.2 discharge a pre-flight correction, not an §8 label
        if verify.startswith("same module"):
            continue
        for label in labels:
            declared = criteria[label][1]
            basename = declared.split("::")[0]
            directory = manifest.get(basename)
            if directory is None:
                failures.append(f"{gid}: §8 names {basename} for {label}; §9 does not list it")
                continue
            want = f"{directory}/{declared}"
            if not verify.startswith(f"{directory}/{basename}"):
                failures.append(f"{gid} ({label}): verify={verify!r} but §8+§9 say {want!r}")

    if "--collect" in sys.argv:
        done_labels = {lab for _g, _r, labs, _v, done in rows if done for lab in labs}
        node_ids = declared_node_ids(criteria, manifest, labels=done_labels)
        drifted = unresolved_node_ids(node_ids)
        if drifted:
            failures.append(
                f"{len(drifted)} node ids for COMPLETED tasks do not resolve (condition E "
                f"would fail on each): {drifted}"
            )
        print(
            f"collect: {len(node_ids) - len(drifted)}/{len(node_ids)} node ids for completed "
            f"tasks resolve ({len(criteria) - len(done_labels)} criteria not built yet)"
        )

    print(f"§8 criteria: {len(criteria)}   DAG tasks: {len(rows)}   gated: {len(claimed)}")
    if doubled:
        print(f"deliberately dual-gated: { {k: v for k, v in sorted(doubled.items(), key=lambda kv: label_order(kv[0]))} }")
    if failures:
        print(f"\nFAIL — {len(failures)} discrepancies:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK — every §8 criterion is gated, and every gate points at the test §8 declares.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
