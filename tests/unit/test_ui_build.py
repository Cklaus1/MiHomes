"""SPEC-009 G2 · Step 2 — the production CSS build (A12, A13).

**D5:** the Tailwind *play* CDN ships a compiler to the browser and recompiles the whole
stylesheet on every page load. Tailwind's own documentation excludes it from production, and
`SAAS_PRD`'s Performance row now forbids it explicitly (B2).

**The build inverts a property that held for five phases.** Under the CDN, any class a template
named simply worked — the compiler was right there. With a compiled stylesheet, a class the
`content` globs do not see **does not exist**, and the failure is silent: no style, in
production, having looked correct in a checkout where the CSS happened to be built earlier.

A13 is the guard for that, and it is the only criterion in this spec whose defect is invisible
locally.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "mihomes" / "web" / "templates"
STATIC = ROOT / "src" / "mihomes" / "web" / "static"
APP_CSS = STATIC / "app.css"
STAMP = STATIC / "app.css.stamp"
INPUT_CSS = STATIC / "input.css"
CONFIG = ROOT / "tailwind.config.js"
PACKAGE_JSON = ROOT / "package.json"


def templates() -> list[pathlib.Path]:
    """Every template, from disk. Never a literal list — see `test_ui_responsive`."""
    return sorted(TEMPLATES.rglob("*.html"))


_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)


def _markup(path: pathlib.Path) -> str:
    """A template's markup with HTML comments stripped.

    **A comment that quotes what was removed is not the thing being removed**, and both A12 and
    the inline-style check hit that immediately: `base.html` now carries comments explaining
    that `cdn.tailwindcss.com` and the `<style>` block were replaced, and a raw substring scan
    flagged the explanations as the defect.

    Exactly the distinction G0 had to make for the PRDs, where "Corrected 2026-08-31 …" notes
    quote the stale string in order to fix it. The lesson generalises: any regression gate over
    a document has to separate the claim from the commentary about the claim. Stripping
    comments is the structural version — narrower than a keyword exemption, and it cannot
    silently grow to hide a real hit.
    """
    return _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------- #
# A12 — the play CDN is gone
# ------------------------------------------------------------------------------------- #
def test_no_cdn_tailwind():
    """**A12** — no template loads `cdn.tailwindcss.com`.

    Enumerated across every template rather than checked in `base.html` alone: the CDN was in
    the base layout, but nothing stops a page adding its own `{% block head %}` script, and
    that page would then ship a second Tailwind — the compiled one *and* the browser compiler.
    """
    offenders = [
        f"{t.relative_to(ROOT)}"
        for t in templates()
        if "cdn.tailwindcss.com" in _markup(t)
    ]
    assert not offenders, (
        "these templates still load the Tailwind play CDN, which compiles in the browser on "
        f"every page load (D5, and SAAS_PRD's Performance row forbids it): {offenders}"
    )


def test_the_compiled_stylesheet_is_actually_linked():
    """A12's positive twin — "no CDN" is trivially true of a page with no CSS at all.

    Without this, deleting the stylesheet link entirely would satisfy the criterion while
    rendering the app unstyled. §0.5b: every negative gets a positive.
    """
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert re.search(r'<link[^>]+href="/static/app\.css"', base), (
        "base.html does not link the compiled stylesheet. A12 passes on a page with no CSS "
        "whatsoever, which is why this arm exists"
    )
    assert APP_CSS.exists(), "app.css is linked but not committed"
    assert APP_CSS.stat().st_size > 5000, (
        f"app.css is only {APP_CSS.stat().st_size} bytes — suspiciously small for a compiled "
        "Tailwind build, which suggests the content globs matched nothing"
    )


def test_the_inline_style_block_did_not_survive_alongside_the_build():
    """The custom CSS moved to `input.css`; a leftover `<style>` block would be a second source.

    Not pedantry: the block defined `.ie-field` and `.ie-incard`, and two copies drifting apart
    is how a hover state ends up differing between the compiled sheet and the inline one.
    """
    base = _markup(TEMPLATES / "base.html")
    assert "<style>" not in base, (
        "base.html still carries an inline <style> block. Those rules now live in "
        "static/input.css and are compiled into app.css — two sources for one component style "
        "is how they drift"
    )
    text = INPUT_CSS.read_text(encoding="utf-8")
    for rule in (".ie-field", ".ie-incard", ".htmx-indicator"):
        assert rule in text, f"{rule} was dropped in the move rather than relocated"


# ------------------------------------------------------------------------------------- #
# A13 — the committed CSS is fresh
# ------------------------------------------------------------------------------------- #
def _expected_stamp() -> str:
    """Recompute the hash `scripts/css_stamp.js` writes.

    Deliberately a **reimplementation** rather than shelling out to Node: the test must run in
    CI and on a developer machine with no Node installed, and P2 (Node) is scoped to G2 alone.
    The two implementations agreeing is itself a check — they hash the same inputs in the same
    order, and a divergence means one of them changed without the other.
    """
    sources = sorted(TEMPLATES.rglob("*.html")) + [INPUT_CSS, CONFIG]
    h = hashlib.sha256()
    for f in sources:
        h.update(f.relative_to(ROOT).as_posix().encode("utf-8"))
        h.update(f.read_bytes())
    return h.hexdigest()


def test_css_is_current():
    """**A13 · G-freshness** — `app.css` is up to date with the templates and config.

    §4.2 commits the build output so deployment stays Python-only. The cost is drift, and
    **the symptom appears only in production**: a class added to a template silently does
    nothing for every user, while the developer who added it sees their locally-built CSS and
    notices nothing.

    Hashes the *inputs*, not the output — Tailwind's output is not byte-stable across versions,
    so an output hash would fail on a dependency bump that changed nothing here.
    """
    assert STAMP.exists(), (
        "static/app.css.stamp is missing. Run `npm run build:css && npm run stamp` — without "
        "it there is nothing asserting the committed CSS matches the templates"
    )

    recorded = STAMP.read_text(encoding="utf-8").strip()
    expected = _expected_stamp()

    assert recorded == expected, (
        "app.css is stale: a template, input.css or tailwind.config.js changed after the last "
        "build. Run `npm run build:css && npm run stamp`.\n"
        "This matters more than it looks — an unbuilt class silently does nothing **in "
        f"production only**.\n  recorded {recorded[:16]}…\n  expected {expected[:16]}…"
    )


def test_the_freshness_check_can_actually_fail():
    """G-freshness with teeth: prove the hash responds to a template change.

    A13 compares two strings. If `_expected_stamp` ignored its inputs — an empty source list, a
    hash of nothing — it would agree with the recorded value forever and assert nothing at all.
    So this perturbs a real input and asserts the digest moves.
    """
    baseline = _expected_stamp()

    real = hashlib.sha256()
    for f in sorted(TEMPLATES.rglob("*.html")) + [INPUT_CSS, CONFIG]:
        real.update(f.relative_to(ROOT).as_posix().encode("utf-8"))
        real.update(f.read_bytes())
    real.update(b"<div class='md:hidden'>a class that was never compiled</div>")

    assert real.hexdigest() != baseline, (
        "the stamp does not change when a source changes, so A13 compares two constants and "
        "would never detect a stale stylesheet"
    )


# ------------------------------------------------------------------------------------- #
# The content globs — the thing that makes a compiled build safe
# ------------------------------------------------------------------------------------- #
def _content_globs() -> list[str]:
    """The `content` array from `tailwind.config.js`, read from the file.

    Parsed rather than imported: the config is JavaScript, and the point is to assert what the
    build actually reads.
    """
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r"content:\s*(\[[^\]]*\])", text, re.S)
    assert match, "tailwind.config.js declares no `content` array"
    return json.loads(match.group(1).replace("'", '"'))


@pytest.mark.parametrize("template", templates(), ids=lambda p: p.name)
def test_content_globs_cover_every_template(template):
    """**Every** template must be matched by a content glob, subdirectories included.

    This is the criterion that makes the compiled build safe, and it is parametrized so a
    failure names the file. A top-level-only glob would silently exempt `partials/`,
    `settings/`, `team/` and `onboarding/` — four directories of real markup whose classes
    would then be absent from the stylesheet, in production, with nothing failing locally.
    """
    rel = pathlib.PurePosixPath(template.relative_to(ROOT).as_posix())
    globs = [g.lstrip("./") for g in _content_globs()]

    # `PurePath.full_match`, **not `fnmatch`**. `fnmatch` has no recursive-glob semantics: it
    # treats `**` as a single `*`, which cannot cross a directory boundary — so
    # `templates/**/*.html` matched `partials/x.html` and *failed* on `tasks.html`, reporting
    # all 28 top-level templates as uncovered while the build was compiling them correctly.
    #
    # Caught because the CSS had already built at 39KB: a config that really matched nothing
    # would have produced an almost-empty stylesheet, which `test_the_compiled_stylesheet_is_
    # actually_linked` asserts against. Two gates disagreeing is what located the bug in the
    # matcher rather than the config.
    assert any(rel.full_match(g) for g in globs), (
        f"{rel} is not matched by any tailwind `content` glob {globs}. Every class it uses "
        "will be missing from app.css — silently, and only in production"
    )


def test_the_build_is_declared_and_reproducible():
    """`package.json` names the build, so "run the build" is a command rather than folklore."""
    pkg = json.loads(PACKAGE_JSON.read_text(encoding="utf-8"))
    scripts = pkg.get("scripts", {})

    assert "build:css" in scripts, "no `build:css` script — the build is undocumented"
    assert "stamp" in scripts, "no `stamp` script, so A13's hash has to be written by hand"
    assert "tailwindcss" in pkg.get("devDependencies", {}), (
        "tailwind is not a declared dependency, so the build is not reproducible"
    )

    # A dev dependency, not a runtime one: nothing Python imports needs Node (§4.2).
    assert "dependencies" not in pkg or not pkg["dependencies"], (
        "package.json declares runtime dependencies. The deploy target is a Python app; Node "
        "is a build-time tool only, which is why app.css is committed"
    )


def test_node_modules_is_not_committed():
    """The one thing a first `npm install` in a Python repo is most likely to leave behind."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "node_modules" in gitignore, (
        ".gitignore does not exclude node_modules/ — a first `npm install` would otherwise "
        "commit tens of thousands of files"
    )
