"""SPEC-005 §6 Step 16 — the deliverability check (A20, D17, B1).

**A test over the repo's own documentation, not a live DNS query.** D17 is explicit about why,
and the reasoning is worth keeping next to the assertion: a test that resolves real DNS is a
network-dependent flake, it cannot run in CI before the domain exists, and it would not have
caught the defect that actually occurred. B1 was a **copy-paste inconsistency between two
documents** — `GTM_LAUNCH_PLAN.md`'s DNS table carried `adkim=s; aspf=s` while
`BILLING_AND_EMAIL.md:234` forbids exactly that, in a table `GTM:262` itself calls
non-authoritative. Reading DNS proves nothing about which document an operator will copy from.

**What "deliverability" is and is not, here.** §10 says it plainly: this asserts the documented
record is internally consistent. It does not prove SPF, DKIM and DMARC pass at a real mailbox
provider, that `send.mihomes.ai` is verified, or that mail reaches an inbox rather than a spam
folder. `GTM` §5's "send a real test message and confirm placement" is a human task no test
replaces (§0.8 U7).

**B1's edit was already applied when this was written.** Measured, not assumed — `GTM:273` reads
`v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai`. That makes this file a *regression* gate rather
than the fix, which is the more useful of the two: the edit is a one-line change anybody could
re-introduce while copying a "hardened DNS" example from elsewhere.
"""

from __future__ import annotations

import pathlib
import re

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"

#: Every document that quotes a DMARC record. Enumerated by *searching*, not listed: a third
#: document that copies the record in tomorrow is exactly the failure B1 was, and a hand-written
#: list would not see it.
_DMARC_PATTERN = re.compile(r"v=DMARC1;[^`|\n]*")

#: Documents that **describe** the defect rather than publishing a record.
#:
#: `PRD_REVIEW.md` is the audit that raised B1. Its §A6 quotes the wrong record *and* the right
#: one, side by side, to make the contradiction legible — so a naive scan finds `adkim=s` there
#: and reads the review as the disease rather than the diagnosis.
#:
#: **Excluded by path, not by pattern.** The tempting fix is to skip any record containing an
#: ellipsis, since the review elides `rua=…` — but that would also skip a real record somebody
#: abbreviated while pasting, which is precisely the copy-paste failure this file exists to
#: catch. Naming the reviewing documents is narrower and cannot silently grow.
#:
#: `SPEC-005`'s §2.1 quotes the defective record in the **B1 row that orders its deletion**, and
#: for the same reason: a fix instruction that cannot show the string it is fixing is unusable.
#: A spec is a reviewing document about the records, not a publisher of them — measured, not
#: assumed: the first version of this list held only `PRD_REVIEW.md` and this file failed on
#: SPEC-005 itself.
#:
#: **`docs/product/` and `docs/architecture/` are deliberately absent from this list.** Those are
#: where an operator copies from, which is exactly where B1 landed, and no exclusion should ever
#: be added for them.
_REVIEW_DOCS = frozenset({"PRD_REVIEW.md"})
_REVIEW_DIRS = ("specs",)


def _dmarc_records(include_reviews: bool = False) -> list[tuple[pathlib.Path, int, str]]:
    """Every DMARC record string under `docs/`, with its file and line.

    `include_reviews` exists so the exclusion above is *testable*: a test that only ever sees
    the filtered list cannot notice the filter growing to hide a real record.
    """
    found: list[tuple[pathlib.Path, int, str]] = []
    for path in sorted(DOCS.rglob("*.md")):
        if not include_reviews and (
            path.name in _REVIEW_DOCS
            or any(part in _REVIEW_DIRS for part in path.relative_to(DOCS).parts[:-1])
        ):
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for match in _DMARC_PATTERN.finditer(line):
                found.append((path, lineno, match.group(0).strip()))
    return found


def test_dmarc_relaxed():
    """A20 — no documented DMARC record sets strict alignment (D5, B1).

    `BILLING:234`: *"keep the default **relaxed** alignment (do not set `adkim=s; aspf=s`)"* —
    the From domain is `send.mihomes.ai` while Resend's return-path sits on its own sub-label,
    so strict SPF alignment fails **legitimately signed mail**. A record that hardens the wrong
    knob looks more secure and delivers less, which is why this is a launch gate rather than a
    style note: `SAAS_PRD:191` requires "DKIM/SPF/DMARC passing", and this line would break the
    gate it sits beside.
    """
    offenders = [
        f"{path.relative_to(DOCS.parent)}:{lineno} — {record}"
        for path, lineno, record in _dmarc_records()
        if "adkim=s" in record or "aspf=s" in record
    ]

    assert not offenders, (
        "a documented DMARC record sets strict alignment, which `BILLING:234` forbids — "
        "Resend's return-path is on a sub-label and strict SPF would fail mail we signed "
        "correctly (D5/B1):\n  " + "\n  ".join(offenders)
    )


def test_the_documented_records_agree_with_each_other():
    """B1 itself: two documents quoting one record must quote the same one.

    This is the assertion that would have caught the original defect. `GTM:262` says
    `BILLING_AND_EMAIL.md` "is authoritative for the email records" and then contradicted it
    inside a copy-pasteable table — so the failure was never that either document was
    unreachable, it was that an operator would copy the wrong one.
    """
    records = _dmarc_records()
    assert records, "no DMARC record found in docs/ at all — the scope of this test is wrong"

    distinct = {record for _path, _lineno, record in records}
    assert len(distinct) == 1, (
        "docs/ quotes more than one DMARC record. `GTM:262` names BILLING_AND_EMAIL.md "
        "authoritative, so any other copy must match it exactly or an operator will paste the "
        "wrong one:\n  "
        + "\n  ".join(
            f"{p.relative_to(DOCS.parent)}:{n} — {r}" for p, n, r in sorted(records)
        )
    )


def test_the_dmarc_policy_starts_at_none():
    """`BILLING:233`'s rollout: `p=none` first, tighten later.

    Not pedantry about a default. Publishing `p=reject` before aggregate reports confirm
    alignment means every misconfiguration silently *discards* real mail rather than reporting
    it — the failure mode is invisible in exactly the window where it is most likely.
    """
    for path, lineno, record in _dmarc_records():
        assert "p=none" in record, (
            f"{path.relative_to(DOCS.parent)}:{lineno} publishes {record!r}. `BILLING:233` "
            "starts at `p=none` and tightens only after reports are clean — a stricter policy "
            "published early discards legitimate mail instead of reporting it"
        )


def test_the_record_names_a_reporting_address():
    """`rua=` is what makes the `p=none` phase mean anything.

    A DMARC record with no reporting address monitors nothing, so the rollout in `BILLING:233`
    can never move past its first step: there is no evidence to tighten on.
    """
    for path, lineno, record in _dmarc_records():
        assert "rua=mailto:" in record, (
            f"{path.relative_to(DOCS.parent)}:{lineno} has no `rua=` — `p=none` without a "
            "reporting address collects nothing, and the rollout has nothing to act on"
        )


def test_the_review_exclusion_still_describes_a_review():
    """The exclusion above must stay an exclusion for *reviews*, not a place to hide a record.

    An allowlist that nothing checks is the cheapest way to make a failing gate pass — one line,
    and the offending document is invisible. Same construction as `ALLOWLIST_MECHANISMS` and
    `UNFILTERED_CLASSES`: the exemption has to say why, and something has to read the why.

    So this asserts the excluded document really is arguing *against* the record it quotes. If
    `PRD_REVIEW.md` ever stops saying "do not set", the exclusion stops being justified and this
    fails — which is the point at which a human should look rather than the test being edited.
    """
    for name in _REVIEW_DOCS:
        matches = list(DOCS.rglob(name))
        assert matches, f"{name} is excluded from the DMARC scan but does not exist"

        text = matches[0].read_text(encoding="utf-8")
        assert "do not set" in text.lower(), (
            f"{name} is excluded because it *documents* the strict-alignment defect rather than "
            "publishing it. It no longer contains that argument, so the exclusion is now hiding "
            "a record instead of explaining one"
        )

    # And the scan genuinely finds something there — otherwise the exclusion is dead weight
    # nobody will remove, and a future reader will assume it is load-bearing.
    excluded = [
        record
        for path, _lineno, record in _dmarc_records(include_reviews=True)
        if path.name in _REVIEW_DOCS
    ]
    assert excluded, (
        "the review exclusion matches no DMARC record at all — delete it rather than leaving a "
        "filter that appears to be doing something"
    )


@pytest.mark.parametrize("term", ["SPF", "DKIM", "DMARC"])
def test_the_three_records_are_all_documented(term):
    """`SAAS_PRD:191` names all three. A launch checklist missing one is a launch that fails it.

    Asserted against `BILLING_AND_EMAIL.md` specifically, because `GTM:262` makes it the
    authority — a term appearing only in the non-authoritative table is the B1 shape again.
    """
    billing = (DOCS / "architecture" / "BILLING_AND_EMAIL.md").read_text(encoding="utf-8")
    assert term in billing, (
        f"{term} is not documented in BILLING_AND_EMAIL.md, which GTM:262 names authoritative "
        f"for the email records — SAAS_PRD:191 requires all three passing at GA"
    )
