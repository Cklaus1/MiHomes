"""SPEC-009 G4 · Step 4 — design tokens (A14).

**Structural only, and that boundary is the whole design of this group** (D7/N7). Tokens exist,
tokens are used, raw hex does not bypass them. Nothing here asserts that anything *looks* good:
aesthetic judgement cannot be a pytest assertion, and a criterion that cannot fail is not a
criterion.

N7 names the risk explicitly — the design-system step is the one most likely to swallow the
project. Spacing scales, typography and a component library are a different piece of work.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TEMPLATES = ROOT / "src" / "mihomes" / "web" / "templates"
CONFIG = ROOT / "tailwind.config.js"
INPUT_CSS = ROOT / "src" / "mihomes" / "web" / "static" / "input.css"


def templates() -> list[pathlib.Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def brand_tokens() -> dict[str, str]:
    """The brand palette, read from `tailwind.config.js`.

    Read rather than restated: a test carrying its own copy of the palette agrees with itself
    while the config drifts, which is the failure this whole criterion is about.
    """
    body = CONFIG.read_text(encoding="utf-8")
    block = re.search(r"brand:\s*\{(.*?)\}", body, re.S)
    assert block, "tailwind.config.js declares no `brand` palette"
    return {k: v.lower() for k, v in re.findall(r"(\d+):\s*\"(#[0-9a-fA-F]{6})\"", block.group(1))}


def test_the_palette_is_defined_in_one_place():
    """Precondition for everything below: the tokens exist and are non-trivial."""
    tokens = brand_tokens()
    assert len(tokens) >= 3, f"expected a real brand ramp, found {tokens}"
    assert all(re.fullmatch(r"#[0-9a-f]{6}", v) for v in tokens.values())


def test_no_raw_brand_hex():
    """**A14** — no template hardcodes a brand hex value.

    The four that did were all inside page-level `<style>` blocks — a blockquote border, two
    streaming cursors, an active-session highlight. That is the sharp case rather than the
    obvious one: **plain CSS cannot read a Tailwind token**, so those rules had `#0284c7`
    written out by hand and would have kept the old colour through any palette change, silently
    and only in the places nobody re-checks.

    Fixed with CSS custom properties in `input.css`, generated from the same values the config
    declares — see `test_the_css_variables_match_the_config`, which is what stops that bridge
    becoming a second source of truth.
    """
    values = set(brand_tokens().values())
    offenders = []
    for t in templates():
        for i, line in enumerate(t.read_text(encoding="utf-8").splitlines(), 1):
            for m in re.finditer(r"#[0-9a-fA-F]{6}\b", line):
                if m.group(0).lower() in values:
                    offenders.append(f"{t.relative_to(TEMPLATES).as_posix()}:{i} {m.group(0)}")

    assert not offenders, (
        "these templates hardcode a brand colour instead of using the token, so a palette "
        f"change would leave them behind: {offenders}"
    )


def test_the_tokens_are_actually_used():
    """A14's positive twin — a palette nothing references is decoration.

    "No raw hex" is trivially satisfied by a codebase that never uses the brand colour at all,
    which is why §0.5b insists every negative gets a positive. This asserts the tokens reach
    real markup.
    """
    used = sum(
        len(re.findall(r"\b(?:bg|text|border|ring|from|to|via)-brand-\d+", t.read_text(encoding="utf-8")))
        for t in templates()
    )
    assert used >= 10, (
        f"only {used} brand-token utilities across every template. A14 passes on a codebase "
        "that abandoned the palette entirely, which is not the same as one that respects it"
    )


def test_the_css_variables_match_the_config():
    """The bridge must not become a second source of truth.

    `input.css`'s `:root` block exists so plain CSS in `<style>` blocks can reach the palette.
    That is a *copy*, and a copy nothing checks is how two sources drift — the exact defect A14
    is about, reintroduced one layer down.

    So this reads both files and asserts they agree, value by value.
    """
    tokens = brand_tokens()
    css = INPUT_CSS.read_text(encoding="utf-8")
    declared = {
        k: v.lower() for k, v in re.findall(r"--brand-(\d+):\s*(#[0-9a-fA-F]{6})", css)
    }

    assert declared, (
        "input.css declares no --brand-* custom properties, so a `<style>` block has no way to "
        "reference the palette and must hardcode it"
    )

    mismatched = {
        k: (tokens.get(k), declared.get(k))
        for k in set(tokens) | set(declared)
        if tokens.get(k) != declared.get(k)
    }
    assert not mismatched, (
        "the CSS custom properties and tailwind.config.js disagree — two sources for one "
        f"palette, which is the drift A14 exists to prevent: {mismatched}"
    )


@pytest.mark.parametrize("var_name", ["--brand-600"])
def test_the_style_blocks_reference_variables_not_literals(var_name):
    """The four fixed rules must keep using the variable.

    Named specifically because a `<style>` block is where the next hardcoded colour will go:
    it is the one place a Tailwind utility is not available, so the path of least resistance is
    a literal.
    """
    hits = sum(
        t.read_text(encoding="utf-8").count(f"var({var_name})") for t in templates()
    )
    assert hits >= 4, (
        f"expected the page-level <style> rules to use var({var_name}); found {hits}. If they "
        "were rewritten as utilities that is an improvement — update this count deliberately"
    )


def test_the_scope_stayed_structural():
    """**N7's guard.** The design-system step is the one most likely to swallow the project.

    D7 limits G4 to "tokens exist and are used". If `tailwind.config.js` grows a spacing scale,
    a type ramp or a component layer, that is a different piece of work wearing this one's
    commit message — and it should be a deliberate decision with its own spec, not something
    that arrives inside a colour-token change.
    """
    config = CONFIG.read_text(encoding="utf-8")
    extend = re.search(r"extend:\s*\{(.*)\},?\s*\},?\s*plugins", config, re.S)
    assert extend, "could not locate the `extend` block to check its scope"

    overreach = [
        key
        for key in ("spacing", "fontSize", "fontFamily", "lineHeight", "borderRadius", "screens")
        if re.search(rf"\b{key}\s*:", extend.group(1))
    ]
    assert not overreach, (
        f"tailwind.config.js's `extend` now customises {overreach}. That is a design system, "
        "not a token — N7 defers it, and it needs its own spec rather than arriving inside G4"
    )
