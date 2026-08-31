"""G0 · SPEC-009 §2 — the two `SAAS_PRD` repairs, as a regression gate (B1, B2).

**The defect these fix is not a false statement. It is three true statements read together.**

`SAAS_PRD` said, accurately: native mobile apps are out of scope (`:98`); the *landing page*
must be fast and responsive (`:165`); and the Staff Member persona — *"housekeeper, property
manager, handyman coordinator"* — reaches the product by *"chat via Telegram/WhatsApp"* (`:44`).

Each is true. Together they told five phases of implementation that phones were somebody else's
problem, and the product app was built desktop-only: a 240px fixed sidebar on a 375px screen,
20 tables that clip rather than scroll, 8 templates with no responsive class at all.

So these tests do not assert that the PRD is *correct* — it was. They assert that the two
clarifications survive, because the failure mode is a well-meaning edit that tightens the
wording back to something briefer and equally misleading.

Same construction as `test_docs_dns.py` and `test_docs_gateway_prds.py`: a documentation
regression gate, where the thing being protected is a distinction rather than a fact.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SAAS_PRD = ROOT / "docs" / "product" / "SAAS_PRD.md"


def _text() -> str:
    return SAAS_PRD.read_text(encoding="utf-8")


def _lines() -> list[tuple[int, str]]:
    return list(enumerate(_text().splitlines(), 1))


def test_native_and_responsive_are_distinguished():
    """**B1** — the native-app exclusion must not read as "phones are out of scope".

    The exclusion itself stays (D2 — native apps are still deferred). What must be present is
    the distinction: native *packaging* is out, responsive *web* is in.

    Asserted structurally rather than by exact string, so a rewording that preserves the meaning
    passes and a tightening that drops it fails: the bullet naming native apps must also name
    responsive web, on the same bullet.
    """
    native_bullets = [
        (n, line)
        for n, line in _lines()
        if re.search(r"native mobile app", line, re.IGNORECASE)
    ]
    assert native_bullets, (
        "no bullet mentions native mobile apps at all. The exclusion is real (D2) and should "
        "stay — this test guards its *framing*, not its existence"
    )

    # The bullet, plus the lines that continue it (markdown wraps, and the clarification is
    # deliberately longer than one line).
    n, _ = native_bullets[0]
    all_lines = _text().splitlines()
    block = " ".join(all_lines[n - 1 : n + 8])

    assert re.search(r"responsive", block, re.IGNORECASE), (
        "the native-app exclusion does not mention responsive web. Read beside §3's Staff "
        "Member persona, a bare exclusion says 'phones are not a target' — which is how the "
        "product app came to be built desktop-only for five phases (SPEC-009 §0.1)"
    )
    assert re.search(r"does not mean|not mean phones|web app is responsive", block, re.IGNORECASE), (
        "the bullet mentions responsive web but does not draw the distinction explicitly. The "
        "reader who got this wrong was not careless — they were reading a true sentence"
    )


def test_product_app_is_in_the_performance_row():
    """**B2** — the non-functional performance row must name the product app, not just landing.

    `:165` originally read *"landing page must be fast/responsive"*. That is the row where a
    responsiveness requirement would live, so its silence about the product app is why nothing
    tracked it.
    """
    perf_rows = [
        (n, line)
        for n, line in _lines()
        if line.startswith("| **Performance**")
    ]
    assert len(perf_rows) == 1, f"expected exactly one Performance row, found {len(perf_rows)}"

    _n, row = perf_rows[0]
    assert re.search(r"product app", row, re.IGNORECASE), (
        "the Performance row still names only the landing page. This is the row a responsive "
        "requirement belongs in, and its silence is why the product app's responsiveness was "
        "never a tracked requirement (B2)"
    )
    assert "responsive" in row.lower()


def test_the_performance_row_forbids_the_play_cdn():
    """**B2's other half** — D5 belongs in the performance row, not only in the spec.

    Shipping a CSS compiler to the browser and recompiling on every page load is a performance
    defect, and this is the row that governs performance. Stating it here means a future change
    that reintroduces the CDN contradicts the PRD rather than merely contradicting a spec
    nobody rereads.
    """
    _n, row = next(
        (n, line) for n, line in _lines() if line.startswith("| **Performance**")
    )
    assert re.search(r"compiled|CDN", row, re.IGNORECASE), (
        "the Performance row does not mention how CSS ships. D5 removes the Tailwind play CDN "
        "on performance grounds, and this row is where that requirement lives (B2)"
    )


def test_the_staff_persona_still_names_the_gateways():
    """The line that must **not** change, asserted so a later edit does not "simplify" it away.

    `:44`'s *"chat via Telegram/WhatsApp"* is not the defect — it is a correct description of
    what SPEC-006 built, and the gateways remain the right answer for write-in-passing. B1 and
    B2 add the web app *alongside* it.

    Without this arm, an over-eager application of B1 could rewrite the persona row to say
    "web app" and quietly de-scope the gateway work of an entire prior spec.
    """
    text = _text()
    staff_rows = [line for line in text.splitlines() if "Staff Member" in line]
    assert staff_rows, "the Staff Member persona row is gone"

    row = staff_rows[0]
    assert re.search(r"telegram|whatsapp", row, re.IGNORECASE), (
        "the Staff Member persona no longer names the chat gateways. SPEC-006 built that path "
        "and it is the right answer for logging an issue in passing; SPEC-009 adds the web app "
        "beside it rather than replacing it"
    )


@pytest.mark.parametrize("width", ["375", "768", "1440"])
def test_the_reference_widths_are_recorded_somewhere_a_reader_will_find(width):
    """D4's three widths must be discoverable from the PRD, not only from the spec.

    A developer asking "what do we support?" reads the PRD. If the answer lives only in
    `SPEC-009`, the next person to add a page will pick their own breakpoints — which is how a
    responsive product drifts back into a desktop one, page by page.
    """
    perf_row = next(
        line for line in _text().splitlines() if line.startswith("| **Performance**")
    )
    assert width in perf_row, (
        f"the reference width {width}px is not named in the Performance row. All three should "
        "be, or the next page gets whatever breakpoints its author happens to like (D4)"
    )
