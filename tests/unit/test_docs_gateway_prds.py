"""G0.2 · §2 — the doc repairs are a **regression gate**, not work (A1).

§6's P1 records B1–B10 as landed in the spec's own commit, so this file does not perform them:
it asserts the stale strings stay gone and both PRDs stay indexed. That is the more useful of
the two, for the same reason `test_docs_dns.py` gives about B1's DMARC record — these are
one-line claims anybody could reintroduce while copying an example from an older doc.

**A distinction this file has to make, and gets wrong if it is naive: a document that *quotes*
a stale string in order to correct it is not the defect.** Both PRDs carry "Corrected
2026-08-05" notes reading *"…into `review_common.py`, not `shared/responder.py`"*, and a
grep for the old path finds them. Excluding by pattern (say, skipping any line containing
"not") would also skip a real regression somebody phrased awkwardly. So the rule here is
**structural**: a correction names the right answer *and* the wrong one on the same line, and
that is what the checks below require.

Same construction as `_REVIEW_DOCS` in `test_docs_dns.py`, and the same reason: `PRD_REVIEW.md`
and `docs/specs/` **describe** these defects, so they are excluded by path. `docs/product/` is
never excluded — it is where an operator reads, which is where B1–B10 landed.

**P1 was verified rather than trusted.** Running the check found `OMNICHANNEL:55` still
claiming the shared core would live at `core/responder.py` — a path that has never existed. B3
was recorded as done and was done in two of its three places. Repaired in the G0.2 commit.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PRODUCT = ROOT / "docs" / "product"

OMNI = PRODUCT / "OMNICHANNEL_GATEWAY_PRD.md"
WHATSAPP = PRODUCT / "WHATSAPP_GATEWAY_PRD.md"

#: The shipped name. Every "where does the shared core live" answer must be this one.
REAL_CORE = "review_common.py"

#: Paths B3 says are wrong. `gateways/core/responder.py` (TWILIO) is the third spelling.
STALE_CORE_PATHS = ("core/responder.py", "shared/responder.py")


def _lines(path: pathlib.Path):
    return list(enumerate(path.read_text(encoding="utf-8").splitlines(), 1))


#: How far a correction's two halves may sit apart. Markdown wraps, so `OMNICHANNEL:598-600`
#: says *"living at `gateways/review_common.py`, not"* on one line and `core/responder.py` on
#: the next — one sentence, two lines. A strictly per-line check calls that a stale claim.
#:
#: Three lines, not "the whole document": a document that names the real module *somewhere* and
#: the wrong one in an unrelated section is exactly the defect B3 describes.
_CORRECTION_WINDOW = 3


def _uncorrected(path: pathlib.Path, needle: str) -> list[str]:
    """Lines mentioning `needle` with no correction nearby.

    The structural test described in the module docstring: a correction names both the right
    answer and the wrong one, a stale claim names only the wrong one. Which is why this is not
    a plain `needle not in text` — and why the window exists rather than a per-line check.
    """
    lines = _lines(path)
    out = []
    for i, (n, line) in enumerate(lines):
        if needle not in line:
            continue
        lo = max(0, i - _CORRECTION_WINDOW)
        hi = min(len(lines), i + _CORRECTION_WINDOW + 1)
        nearby = " ".join(text for _num, text in lines[lo:hi])
        if REAL_CORE not in nearby:
            out.append(f"{path.name}:{n} — {line.strip()[:110]}")
    return out


@pytest.mark.parametrize("stale", STALE_CORE_PATHS)
def test_repairs_landed(stale):
    """**A1** — B3's stale module paths stay gone from both gateway PRDs.

    The parametrization is the point: `core/` and `shared/` are two *different* wrong answers
    that appeared in two different documents, and a single combined assertion would report one
    failure for what are independent regressions.
    """
    offenders = _uncorrected(OMNI, stale) + _uncorrected(WHATSAPP, stale)
    assert not offenders, (
        f"a gateway PRD names {stale!r} as the home of the shared responder core without "
        f"naming {REAL_CORE!r} on the same line. The module is "
        "`gateways/review_common.py` (B3), and a doc that points at a path which has never "
        "existed sends the next reader to re-extract something that already ships (N1):\n  "
        + "\n  ".join(offenders)
    )


def test_the_corrected_notes_still_exist():
    """The other half of the structural rule: the exclusion must still be excluding *corrections*.

    If the "Corrected …" notes were deleted, the check above would pass by having nothing to
    look at — the same empty-set trap the DMARC file guards with
    `test_the_review_exclusion_still_describes_a_review`. This asserts the corrections are
    present and really do name both answers.
    """
    for path in (OMNI, WHATSAPP):
        text = path.read_text(encoding="utf-8")
        assert REAL_CORE in text, f"{path.name} never names the real module at all"

    corrections = [
        line
        for _n, line in _lines(WHATSAPP)
        if REAL_CORE in line and "shared/responder.py" in line
    ]
    assert corrections, (
        "WHATSAPP_GATEWAY_PRD no longer carries a correction naming both the right and wrong "
        "module paths — either it was cleaned up (fine, delete this test) or the repair was "
        "reverted (not fine)"
    )


def test_both_prds_are_indexed():
    """**B10** — both PRDs appear in the doc-set index that claims to be complete.

    `PRD_REVIEW` recommendation 4's second half: the set is 12 documents while its own indexes
    said 10. An index that silently omits two documents is worse than no index, because a
    reader treats it as exhaustive.
    """
    readme = (PRODUCT / "README.md").read_text(encoding="utf-8")
    for name in ("OMNICHANNEL_GATEWAY_PRD", "WHATSAPP_GATEWAY_PRD"):
        assert name in readme, (
            f"{name} is not indexed in docs/product/README.md, which presents itself as the "
            "complete doc set (B10)"
        )


def test_the_phase_invention_stayed_deleted():
    """**B4** — no gateway PRD invents a phase beyond the four `SAAS_PRD` §10 defines.

    N12: *"Do not invent a Phase 5."* `README` declares phases canon, and a PRD that grows its
    own numbering makes every cross-document phase reference ambiguous.
    """
    for path in (OMNI, WHATSAPP):
        offenders = [
            f"{path.name}:{n} — {line.strip()[:110]}"
            for n, line in _lines(path)
            if re.search(r"\bPhase\s*[5-9]\b", line)
        ]
        assert not offenders, (
            "a gateway PRD names a phase beyond Phase 4, which SAAS_PRD §10 does not define "
            "(B4/N12):\n  " + "\n  ".join(offenders)
        )


def test_the_launch_blocking_claim_stayed_deleted():
    """**B4's other half** — chat gateways are a 4+ growth bet, not launch-blocking.

    `SAAS_PRD:186`. A PRD marking six items "P0 = launch-blocking" competes with the actual GA
    gate list, and the cost of believing it is a delayed launch for work nobody scheduled.
    """
    for path in (OMNI, WHATSAPP):
        offenders = [
            f"{path.name}:{n} — {line.strip()[:110]}"
            for n, line in _lines(path)
            if re.search(r"launch[- ]blocking", line, re.IGNORECASE)
            # Same structural rule as `_uncorrected`: a line that says the claim was
            # *previously* wrong is the repair, not the defect. `OMNICHANNEL:71` reads
            # "P0 previously read 'launch-blocking', which contradicts canon" — quoting the
            # string it deleted, exactly as B1's DMARC note quotes the record it forbids.
            and not re.search(r"correct|previously|contradict|not launch", line, re.IGNORECASE)
        ]
        assert not offenders, (
            "a gateway PRD still calls its work launch-blocking; SAAS_PRD:186 makes chat "
            "gateways a Phase 4+ growth bet (B4):\n  " + "\n  ".join(offenders)
        )


def test_the_category_counts_describe_one_superset():
    """**B5/N13** — no PRD describes the 15-vs-8 category split as current.

    F5 measured that the drift is *fixed*: one `REVIEW_SCHEMA`, 15 categories, both channels.
    Describing the old split as current is doubly wrong — it reports a defect that no longer
    exists and would send someone to "repair" the unified schema by re-splitting it.

    Scoped to lines that pair a count with the word "categor", so prose mentioning either
    number for another reason does not trip it.
    """
    for path in (OMNI, WHATSAPP):
        offenders = [
            f"{path.name}:{n} — {line.strip()[:110]}"
            for n, line in _lines(path)
            if re.search(r"\b8\s+categor", line, re.IGNORECASE)
            and "15" not in line
        ]
        assert not offenders, (
            "a gateway PRD still says a channel handles 8 categories. Per F5 both channels "
            "share the 15-category superset (B5/N13):\n  " + "\n  ".join(offenders)
        )
