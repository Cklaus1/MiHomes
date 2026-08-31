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
