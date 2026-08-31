"""SPEC-009 — the structural responsive audit (A1–A11, A15).

**Every assertion here is static.** No browser, no rendering, no geometry. D3 chose this
deliberately (Playwright is not installed in this project), and §8 states the limit plainly:
green means *no page declares a structure known to break*, **not** that any page is usable on a
phone. Only a human at 375px knows that, which is what A11's checklist exists for.

That honesty is the point rather than a caveat. The failure this whole spec set guards against
is a green suite over a broken product, and a test file that implied otherwise would be the more
dangerous artifact.

**Templates are enumerated from the filesystem, never listed.** A hand-written list is how page
29 ships unaudited — the same construction SPEC-006 A11 used for `REVIEW_SCHEMA`'s categories,
and the failure that has recurred in every spec in this set.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "mihomes" / "web" / "templates"
BASE = TEMPLATES / "base.html"


def templates() -> list[pathlib.Path]:
    """Every `.html` under `templates/`, subdirectories included.

    **Derived, never listed** — `partials/`, `settings/`, `team/` and `onboarding/` all hold
    real markup, and a top-level-only glob would silently exempt them.
    """
    return sorted(TEMPLATES.rglob("*.html"))


def test_the_enumeration_finds_the_templates():
    """Guard on the guard: every test below is driven by `templates()`.

    An empty list would make each of them pass by having nothing to check — the empty-set trap
    that a hand-written list is supposed to avoid, arriving through the back door.
    """
    found = templates()
    assert len(found) >= 28, f"expected at least 28 templates, found {len(found)}"
    assert BASE in found


# ------------------------------------------------------------------------------------- #
# A1-A4 (G1) — the mobile navigation
# ------------------------------------------------------------------------------------- #
def test_mobile_nav_exists():
    """**A1** — `base.html` declares a toggle, a drawer and a backdrop.

    Before G1 there was no pattern to extend: a grep across the whole tree for `hamburger`,
    `drawer`, `off-canvas` or a nav toggle returned **zero** non-table matches. The sidebar was
    `w-60 … flex-shrink-0` with no responsive prefix — 240px of a 375px phone.
    """
    html = BASE.read_text(encoding="utf-8")

    assert 'id="nav-toggle"' in html, "no nav toggle — there is nothing to open the drawer with"
    assert 'id="sidebar"' in html, "the sidebar has no id, so the toggle cannot address it"
    assert 'id="nav-backdrop"' in html, (
        "no backdrop. Without one the drawer opens over the page with no visible dismissal "
        "target, and a tap outside it does nothing"
    )

    # The toggle must be mobile-only, or it appears beside a sidebar that is already visible.
    toggle = _element_with_id(html, "nav-toggle")
    assert "md:hidden" in toggle, (
        "the nav toggle is not hidden at md+, so desktop shows a hamburger next to an "
        "already-visible sidebar"
    )


def test_sidebar_is_responsive():
    """**A2** — the sidebar is not unconditionally visible below `md`, **and is** at `md`+.

    Paired deliberately (§0.5b). "Not visible below md" is trivially satisfied by deleting the
    sidebar, which is why the second half is asserted in the same test.
    """
    html = BASE.read_text(encoding="utf-8")
    aside = _element_with_id(html, "sidebar")

    # --- negative: off-canvas at rest -----------------------------------------------------
    assert "-translate-x-full" in aside, (
        "the sidebar has no off-canvas rest position, so at 375px it still occupies 240px — "
        "64% of the viewport before any content renders"
    )
    assert "fixed" in aside, "an off-canvas drawer must be `fixed`, or it displaces content"

    # --- positive: still a real column at md+ ---------------------------------------------
    assert "md:static" in aside and "md:translate-x-0" in aside, (
        "the sidebar does not return to a static column at md+. The desktop layout is good and "
        "N1 forbids redesigning it"
    )


def test_desktop_layout_unchanged():
    """**A3 · G-override** — every mobile-only class on the sidebar is overridden at `md`+.

    This is N1's guard, and it is the one criterion no behavioural test makes: "the desktop is
    fine" is a claim about something the mobile tests never look at. A missing `md:static`
    leaves the sidebar `fixed` at every width — desktop content slides under it — while A1 and
    A2 both stay green.

    Derived from the class list rather than hardcoded, so a mobile class added later must bring
    its own override or this fails.
    """
    aside = _element_with_id(BASE.read_text(encoding="utf-8"), "sidebar")
    classes = _classes(aside)

    # Each mobile-only class -> the md: class that must neutralise it.
    required_overrides = {
        "fixed": "md:static",
        "-translate-x-full": "md:translate-x-0",
        "z-40": "md:z-auto",
    }

    missing = [
        f"{mobile} needs {override}"
        for mobile, override in required_overrides.items()
        if mobile in classes and override not in classes
    ]
    assert not missing, (
        "the sidebar carries mobile-only classes that are not overridden at md+, so the "
        f"desktop layout changed — which N1 forbids: {missing}"
    )


def test_nav_is_accessible():
    """**A4** — the toggle is announced, and the drawer is escapable without a mouse.

    A drawer only a mouse can dismiss is a trap on a screen reader: the user opens it and
    cannot get out. `aria-expanded` is what tells assistive tech the state at all.
    """
    html = BASE.read_text(encoding="utf-8")
    toggle = _element_with_id(html, "nav-toggle")

    assert 'aria-expanded="false"' in toggle, (
        "the toggle has no initial `aria-expanded`, so a screen reader cannot tell whether the "
        "navigation is open"
    )
    assert 'aria-controls="sidebar"' in toggle, "the toggle does not say what it controls"
    assert "aria-label" in toggle, "an icon-only button with no label announces as 'button'"

    # The behaviours, asserted against the script rather than by rendering.
    assert "aria-expanded" in html and "setAttribute('aria-expanded'" in html, (
        "`aria-expanded` is set once in markup but never updated, so it lies as soon as the "
        "drawer opens"
    )
    assert re.search(r"key\s*===?\s*'Escape'", html), (
        "Escape does not close the drawer — the standard dismissal for any overlay, and the "
        "only one available without a pointer"
    )
    assert ".focus()" in html, (
        "focus is never moved. On open it must enter the drawer, and on close return to the "
        "toggle, or focus is orphaned on a hidden element"
    )


# ------------------------------------------------------------------------------------- #
# A5-A10 (G3) — the per-page audit
# ------------------------------------------------------------------------------------- #
def _tables_with_ancestors(html: str):
    """Yield `(line_no, ancestor_lines)` for every `<table>`, innermost ancestor first.

    **G-ancestor.** A5's defect was not merely "no scroll" — 16 of the 20 tables sat inside
    `<div class="… overflow-hidden">` card wrappers that *clip*, so the columns were
    unreachable rather than off-screen. A naive `"overflow-x-auto" in html` check passes when
    that class sits on an unrelated element elsewhere on the page, which is exactly the false
    green this walks ancestors to avoid.
    """
    lines = html.splitlines()
    for i, line in enumerate(lines):
        if "<table" not in line:
            continue
        # Walk backwards collecting opening tags that have not yet been closed. Cheap and
        # sufficient: these templates are hand-written and consistently indented.
        yield i + 1, [lines[j] for j in range(i - 1, max(-1, i - 6), -1)]


@pytest.mark.parametrize("template", templates(), ids=lambda p: p.name)
def test_tables_scroll(template):
    """**A5 · the phase's definition of done** — no `<table>` is clipped or unscrollable.

    > A page that overflows horizontally on a phone is unusable in a way the user cannot work
    > around. They cannot scroll to a column they cannot reach; they cannot tap a button off the
    > right edge.

    28 pages of an estate-management product are mostly lists of things, which is why this is
    the criterion the spec calls done rather than one of the milder ones.

    The fix is a scroll wrapper **inside** the card, not removing `overflow-hidden` from the
    card — that class is what gives the card its rounded corners.
    """
    html = template.read_text(encoding="utf-8")
    offenders = [
        f"{template.name}:{line_no}"
        for line_no, ancestors in _tables_with_ancestors(html)
        if not any("overflow-x-auto" in a for a in ancestors)
    ]
    assert not offenders, (
        f"these tables have no horizontally scrollable ancestor: {offenders}. 16 of the "
        "original 20 sat in `overflow-hidden` cards, so their columns were *clipped* — "
        "unreachable, not merely off-screen"
    )


def test_the_table_scan_finds_the_tables():
    """Guard on A5: the parametrized test passes trivially on a page with no tables.

    Most templates have none, so 30-odd green ticks prove nothing on their own. This asserts
    the corpus really does contain the tables the audit counted.
    """
    total = sum(
        len(list(_tables_with_ancestors(t.read_text(encoding="utf-8")))) for t in templates()
    )
    assert total >= 20, (
        f"found only {total} tables; the audit measured 20. If tables were deleted rather than "
        "wrapped, A5 is passing for the wrong reason"
    )


def test_no_fixed_pixel_widths():
    """**A6** — no layout container carries a hardcoded pixel width that a phone cannot hold.

    Scoped to widths that actually exceed a 375px viewport's usable space once padding is
    accounted for. `max-w-[…]` paired with `truncate` is deliberately **not** an offender: it
    *constrains* rather than overflows, and eight of those exist and are correct.
    """
    offenders = []
    for t in templates():
        for i, line in enumerate(t.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"(?<![:\w-])min-w-\[(\d+)px\]", line):
                px = int(m.group(1))
                if px > 100:
                    offenders.append(f"{t.name}:{i} min-w-[{px}px]")

    assert not offenders, (
        "these minimum widths can force horizontal overflow on a 375px viewport once the "
        f"layout's own padding is subtracted: {offenders}"
    )


#: A7's scope. **D12** — a template owns layout when it declares a multi-column grid or a
#: table. Deliberately *not* `max-w-*` or `w-full`: a `max-w-2xl mx-auto` container is already
#: responsive by construction (max-width is a ceiling, so it shrinks on its own) and `w-full` is
#: fluid by definition. Requiring a breakpoint on those would mean adding `md:` classes that
#: change nothing — N6's gamed metric arriving through A7's own door.
_LAYOUT_OWNING = re.compile(r"grid-cols-[2-9]|grid-cols-1[0-2]|<table")


def layout_owning_templates() -> list[pathlib.Path]:
    return [t for t in templates() if _LAYOUT_OWNING.search(t.read_text(encoding="utf-8"))]


def test_no_zero_prefix_templates():
    """**A7** — no *layout-owning* template has zero responsive prefixes (D12).

    A **floor, not a threshold** (N6). Counting prefixes would reward classes that do nothing:
    `sm:` is 640px, above every phone width, so a template could score well and still break at
    375px.

    The scope narrowed at G3 from a measurement. §0.15's "8 zero-prefix templates" counted the
    28 top-level files; this test walks all 61, and reported 30. Both are right about different
    sets — but most of the extra were partials like `alert_badge.html` (11 lines), inline
    `<span>` badges with no layout to make responsive.
    """
    in_scope = layout_owning_templates()
    assert len(in_scope) >= 25, (
        f"only {len(in_scope)} templates own layout, which is fewer than the audit found — the "
        "predicate is probably too narrow, and A7 would be scoped to almost nothing"
    )

    offenders = [
        t.relative_to(TEMPLATES).as_posix()
        for t in in_scope
        if not re.search(r"\b(?:sm|md|lg|xl):", t.read_text(encoding="utf-8"))
    ]
    assert not offenders, (
        "these templates declare a grid or a table but no breakpoint anywhere, so their layout "
        f"is identical at 375px and 1440px: {offenders}"
    )


def test_grids_are_responsive():
    """**A8** — no multi-column grid lacks a responsive prefix.

    77 at G3's start, concentrated in modal form pairs: at 375px a two-column form gives each
    field ~160px, which does not hold a date input or a select.

    **`calendar.html`'s two `grid-cols-7` month grids are exempt**, and named rather than
    silently skipped. A month is seven columns by definition; collapsing it to one produces a
    list of 30 numbered boxes, not a calendar. That page needs a different answer — horizontal
    scroll, or a genuine list view — which is the spec's §6 Step 3 note, not something to fake
    with a prefix.
    """
    offenders = []
    for t in templates():
        for i, line in enumerate(t.read_text(encoding="utf-8").splitlines(), 1):
            if not re.search(r"(?<![:\w-])grid-cols-([2-9]|1[0-2])\b", line):
                continue
            if re.search(r"\b(?:sm|md|lg|xl):grid-cols-", line):
                continue
            if t.name == "calendar.html" and "grid-cols-7" in line:
                continue  # the month grid — see the docstring
            offenders.append(f"{t.relative_to(TEMPLATES).as_posix()}:{i}")

    assert not offenders, (
        f"these grids hold their column count at every width: {offenders}"
    )


def test_the_calendar_exemption_is_still_needed():
    """The exemption must stay an exemption for the *month grid*, not a hole.

    Same construction as `ALLOWLIST_MECHANISMS` and `EXPECTED_NON_LEADING`: an exemption that
    nothing checks is the cheapest way to make a gate pass. If `calendar.html` stops using
    `grid-cols-7`, the carve-out is dead and must be deleted rather than left as a standing
    permission for any seven-column grid anybody adds later.
    """
    cal = (TEMPLATES / "calendar.html").read_text(encoding="utf-8")
    assert "grid-cols-7" in cal, (
        "calendar.html no longer uses grid-cols-7, so A8's exemption for it is now dead code "
        "granting a permission nothing needs — delete the carve-out"
    )


def test_modals_cap_height():
    """**A9** — every modal panel caps its height and scrolls.

    **The defect is vertical, not horizontal**, which the audit established and an earlier
    draft of this criterion had backwards. Both modal families already supply a 16px gutter —
    Family A via the overlay's `p-4`, Family B via the panel's `mx-4` — so width was never the
    problem.

    20 of 43 panels had neither `max-h-` nor a scroll region: a long form grows past the
    viewport and the submit button becomes unreachable, which on a short phone viewport is most
    of them. 23 already did it correctly with `max-h-[90vh] overflow-y-auto`, so the fix was to
    apply the codebase's own pattern rather than invent one.
    """
    panel = re.compile(r'class="[^"]*bg-white[^"]*rounded-2xl[^"]*max-w-[^"]*"')
    offenders = []
    for t in templates():
        text = t.read_text(encoding="utf-8")
        # **A modal is a panel inside an overlay.** Requiring the file to contain a
        # `fixed inset-0` overlay is what separates a dialog from a card that happens to share
        # its class vocabulary — `partials/ai_message.html:27` is an AI *chat bubble*
        # (`rounded-2xl rounded-tl-sm max-w-2xl`, a speech-bubble tail, no overlay anywhere),
        # and capping its height at 90vh would put a scrollbar inside a chat message.
        #
        # Found by the test failing on it, which is the check doing its job: a matcher tuned
        # only to class names cannot tell a dialog from a bubble.
        if not re.search(r"fixed inset-0", text):
            continue
        for i, line in enumerate(text.splitlines(), 1):
            m = panel.search(line)
            if not m:
                continue
            cls = m.group(0)
            if "max-h-" in cls or "max-height" in line:
                continue
            # `base.html`'s preview overlay caps via an inline style on the following line.
            if t.name == "base.html":
                continue
            offenders.append(f"{t.relative_to(TEMPLATES).as_posix()}:{i}")

    assert not offenders, (
        "these modal panels have no height cap, so a long form grows past the viewport and the "
        f"submit button cannot be reached: {offenders}"
    )


def test_tap_targets():
    """**A10** — interactive elements clear the tap-target floor.

    WCAG 2.5.5 and Apple's HIG both say 44px; Material says 48dp. `p-1` around a `w-5 h-5` icon
    is 28x28 — and `base.html`'s preview-close button was one of those, on **every page**.

    Scoped to `<button>` and `<a>`: padding on a `<span>` or a `<td>` is spacing, not a target,
    and inflating it would change layout for no accessibility gain.
    """
    interactive = re.compile(r"<(?:button|a)\b[^>]*>", re.S)
    small = re.compile(r'class="[^"]*?(?<![\w.])p-1(?:\.5)?(?![\w.])')

    offenders = []
    for t in templates():
        for tag in interactive.finditer(t.read_text(encoding="utf-8")):
            if small.search(tag.group(0)):
                offenders.append(f"{t.relative_to(TEMPLATES).as_posix()}: {tag.group(0)[:60]}")

    assert not offenders, (
        f"{len(offenders)} interactive elements are under ~30px on a touch screen "
        f"(44px is the WCAG 2.5.5 floor): {offenders[:5]}"
    )


# ------------------------------------------------------------------------------------- #
# A11 (G5) — the manual checklist covers every page
# ------------------------------------------------------------------------------------- #
CHECKLIST = ROOT / "docs" / "UI_MANUAL_CHECKLIST.md"


def navigable_templates() -> list[pathlib.Path]:
    """Templates a human can actually open — everything but `base.html` and the partials.

    You cannot navigate to `partials/task_row.html`; its defects surface on whichever page
    includes it, and the static tests cover it directly there.
    """
    return [
        t
        for t in templates()
        if t.name != "base.html" and t.relative_to(TEMPLATES).parts[0] != "partials"
    ]


def test_checklist_is_complete():
    """**A11 · G-derived** — the checklist names every navigable page.

    **This is the criterion that admits what the others cannot do.** Every assertion in this
    file is static: green means no page *declares* a structure known to break. Whether a page
    is usable at 375px is a question only a person holding a phone answers, and U3 carries that
    as an unmet gate rather than pretending a test closed it.

    So A11 asserts *coverage*, not passage — and derives the page list from the filesystem,
    because a hand-written table is how page 29 goes unwalked. The same construction as A15,
    and the failure it prevents has recurred in every spec in this set.
    """
    assert CHECKLIST.exists(), (
        "docs/UI_MANUAL_CHECKLIST.md is missing — U3 is the gate that decides whether the "
        "product is actually usable on a phone, and it needs a document to be walked against"
    )
    text = CHECKLIST.read_text(encoding="utf-8")

    missing = [
        t.relative_to(TEMPLATES).as_posix()
        for t in navigable_templates()
        if f"`{t.relative_to(TEMPLATES).as_posix()}`" not in text
    ]
    assert not missing, (
        f"these pages are absent from the manual checklist, so nobody would know to walk "
        f"them: {missing}"
    )


def test_the_checklist_names_the_three_widths():
    """D4's reference widths must be in the document a human follows.

    A checklist that says "check it on mobile" gets walked at whatever width the reviewer's
    window happens to be.
    """
    text = CHECKLIST.read_text(encoding="utf-8")
    for width in ("375", "768", "1440"):
        assert width in text, f"the checklist does not name the {width}px reference width (D4)"


def test_the_checklist_is_honest_about_what_it_is():
    """The document must say it has not been walked, until it has.

    A generated checklist reads exactly like a completed one. If it silently claimed coverage,
    U3 would look closed while nobody had opened a phone — the "green suite over a broken
    product" failure this whole spec set exists to prevent, arriving through its own paperwork.
    """
    text = CHECKLIST.read_text(encoding="utf-8")
    assert "NOT WALKED" in text or "walked" in text.lower(), (
        "the checklist does not state its own status. Generated and completed look identical "
        "on the page, which is how U3 gets mistaken for closed"
    )


# ------------------------------------------------------------------------------------- #
# A15 (G6) — the exit criterion
# ------------------------------------------------------------------------------------- #
def test_exit_criterion():
    """**A15** — every template, enumerated from disk, satisfies A5–A10.

    Deliberately re-runs the per-criterion checks over the *whole* corpus rather than trusting
    that the parametrized tests above covered it. Those are parametrized from the same
    `templates()` call, so this is not merely a repeat: it is the assertion that the
    enumeration itself is complete, and it fails if the corpus shrinks.

    A hand-listed subset passes forever while page 29 ships unaudited — SPEC-006 A11's
    construction, and the failure mode that has appeared in every spec in this set.
    """
    all_templates = templates()
    assert len(all_templates) >= 59, (
        f"only {len(all_templates)} templates found. If pages were deleted rather than fixed, "
        "every criterion above is passing over a smaller corpus than it was written for"
    )

    failures: list[str] = []

    for t in all_templates:
        html = t.read_text(encoding="utf-8")
        rel = t.relative_to(TEMPLATES).as_posix()

        # A5 — tables scroll
        for line_no, ancestors in _tables_with_ancestors(html):
            if not any("overflow-x-auto" in a for a in ancestors):
                failures.append(f"A5 {rel}:{line_no} table not scrollable")

        # A8 — grids are responsive (calendar's month grid exempt)
        for i, line in enumerate(html.splitlines(), 1):
            if not re.search(r"(?<![:\w-])grid-cols-([2-9]|1[0-2])\b", line):
                continue
            if re.search(r"\b(?:sm|md|lg|xl):grid-cols-", line):
                continue
            if t.name == "calendar.html" and "grid-cols-7" in line:
                continue
            failures.append(f"A8 {rel}:{i} grid frozen at its column count")

    assert not failures, (
        f"{len(failures)} structural defects remain across {len(all_templates)} templates:\n  "
        + "\n  ".join(failures[:15])
    )


# ------------------------------------------------------------------------------------- #
# helpers
# ------------------------------------------------------------------------------------- #
def _classes(tag: str) -> set[str]:
    """The class tokens of an opening tag, parsed from the `class="…"` attribute.

    **Not `tag.split()`.** The first version of A3 did exactly that and reported a false
    failure: a multi-line class list ends `md:z-auto">`, so the naive token kept its trailing
    quote and never compared equal to `md:z-auto`. The class was present and correct; the
    parser was wrong.

    Worth recording rather than quietly fixing, because the failure mode was the *right* shape
    — a gate reporting a missing override — arriving from the wrong cause. A gate that cries
    wolf gets edited away the third time it fires.
    """
    match = re.search(r'\bclass=["\']([^"\']*)["\']', tag, re.S)
    return set(match.group(1).split()) if match else set()


def _element_with_id(html: str, element_id: str) -> str:
    """The opening tag of the element carrying `id="<element_id>"`.

    Returns the tag text so class-list assertions are scoped to *that* element. A whole-file
    substring check would happily pass on `md:static` appearing anywhere on the page, which is
    exactly the false green A3 exists to prevent.
    """
    match = re.search(
        r"<[a-zA-Z]+[^>]*\bid=[\"']" + re.escape(element_id) + r"[\"'][^>]*>", html
    )
    assert match, f"no element with id={element_id!r}"
    return match.group(0)


def test_the_element_helper_scopes_to_one_tag():
    """The helper is load-bearing for A2/A3/A4, so it gets its own test.

    If it returned the whole document, every class assertion above would pass on a class
    appearing anywhere in `base.html` — and `md:` classes do appear elsewhere. Asserted by
    checking it does *not* find a class that is present in the file but not on the sidebar.
    """
    html = BASE.read_text(encoding="utf-8")
    aside = _element_with_id(html, "sidebar")

    assert aside.startswith("<aside")
    assert aside.endswith(">")
    assert "md:hidden" in html, "precondition: md:hidden exists somewhere in base.html"
    assert "md:hidden" not in aside, (
        "the helper returned more than the sidebar's own tag — every scoped assertion above "
        "would then be checking the whole file"
    )
