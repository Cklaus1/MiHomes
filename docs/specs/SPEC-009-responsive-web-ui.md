# SPEC-009 — Responsive web UI: the existing 28 pages, on a phone and on a desktop

**Phase:** 4+ — **not a growth bet.** Unlike SPEC-006/007/008 this ships no new capability; it
makes an already-built surface usable on the device half its users hold. See §0.2
**Stage:** The whole product web app. No native app, no new pages, no redesign — see §7
**Status:** Ready to build — **1 open decision** (O1: the minimum supported viewport width)
**Written:** 2026-08-30
**Verified against:** `worktree-spec-build-harness` @ **`2750bd1`** (SPEC-008 harness authored).
Every code claim below was measured at that ref, not inferred. **Not `origin/main`** — see §0.3
**Source PRDs:** `../product/SAAS_PRD.md` §3 (personas), §6.2 (scope), §8 (non-functional).
**There is no UI/UX PRD** — see §0.1, which is the most important thing on this page
**Depends on:** nothing. Every prior spec's work is in the tree; this one touches templates and
the build, not models, services or tenancy.

**Goal.** An owner on a phone and an estate manager on a laptop both get a working MiHomes.
Today the second works and the first does not.

**Exit criterion:** every page in `web/templates/` renders its content reachable and unclipped at
375px, 768px and 1440px; the navigation is operable at all three; and Tailwind ships compiled
rather than compiled-in-the-browser.

**The stake.** `SAAS_PRD:44` describes the Staff Member — *"housekeeper, property manager,
handyman coordinator"* — whose key jobs are *"see assigned home(s), complete tasks, log issues."*
**That person is standing in a house holding a phone.** They are not at a desk. The product
currently answers them with a 240px fixed sidebar on a 375px screen, and the failure is not
subtle: it is 64% of the viewport, before any content.

---

## 0. Four things a reader must know before trusting this spec

### 0.1 — Mobile web is *unspecified*, not deprioritized, and this spec is where that gets decided

This is the finding that shaped everything below. Measured across `SAAS_PRD`:

- **`:98`** — *"Native mobile apps"* are explicitly **out of scope for GA**. Correct, and this
  spec does not reopen it.
- **`:165`** — the only appearance of the word *responsive* in the entire PRD set applies to the
  **landing page**: *"landing page must be fast/responsive."* The product app is not mentioned.
- **`:44`** — the Staff Member persona's channel is named as **"chat via Telegram/WhatsApp."**

Read together, those three lines are coherent: the PRD routed the phone-holding persona to the
**gateways**, not to the web app. That is why no UI/UX PRD exists, and why "responsive" was never
a product requirement — it was answered a different way.

**SPEC-006 built that answer, and it did not remove the need.** A housekeeper can now log an
issue by message. They still cannot *see assigned homes* or *review a task list* — those are web
pages, and `SAAS_PRD:44` lists them as key jobs. The gateway covers write-in-passing; it does not
cover read-and-review.

**So this spec records a product decision rather than deriving one** (D1). A reader must not
mistake §1 for something the PRDs already said. They did not, and §2's doc-fix is where the
distinction between *native apps* (still out) and *responsive web* (now in) gets written down.

### 0.2 — This spec ships no feature, and that changes how "done" reads

Every other spec in the set adds capability. This one asserts that capability already built is
**reachable**. Two consequences:

- There is **no schema**. §4 substitutes the concrete artifacts — the build config, the
  `base.html` structure — because "schemas as code" exists so a reader cannot interpret prose
  loosely, and that requirement holds whatever the artifact is.
- The exit criterion is a **property of existing pages**, so the work is an audit-and-fix, and
  its risk is the opposite of the usual one. The usual risk is a step not done. Here it is a
  step that *appears* done because a class was added somewhere.

### 0.3 — The tree this is verified against is not canon, and that is now three specs old

`worktree-spec-build-harness` is **182 commits ahead of `origin/main`, 0 behind** — SPEC-002
through SPEC-006 plus SPEC-008's harness. The merge is a clean fast-forward and is a human
action; it has been offered and not yet taken.

Every claim below was measured on the build branch. A future reader must not read "SPEC-009
built successfully" as "main has any of this." Carried as **U1**.

### 0.4 — The numbering, and a collision worth naming

`docs/specs/README.md` reserves **SPEC-007** for the Twilio gateway (unwritten; A2P 10DLC has a
regulatory lead time and no owner). **SPEC-008's own header says "D1–D3 are SPEC-009+"** — so
Vendor Discovery's later stages have a prior claim on this number.

Taking it anyway, deliberately: SPEC-008 is *authored but not run*, its D1–D3 are unwritten and
unscheduled, and "the next vendor-discovery stage" is a weaker claim on a number than a spec that
is about to be built. **Vendor Discovery D1–D3 should be SPEC-010+.** Flagged here so a reader
meeting two claimants finds the reasoning rather than a contradiction.

---

## 1. Decisions

### 1.1 Locked — founder decisions, 2026-08-30

| # | Decision | Rationale |
|---|---|---|
| **D1** | **The product web app is responsive and supports phones.** Owners and staff use it on a phone; the gateways complement that, they do not replace it | §0.1. `SAAS_PRD:44`'s key jobs — *see assigned homes, complete tasks* — are read-and-review work that no chat message performs |
| **D2** | **Native apps stay out of scope**, unchanged from `SAAS_PRD:98` | Responsive web is not a step toward an app; it is the alternative to one. Conflating them is how this becomes a much larger project |
| **D3** | **Verification is static assertion plus a written manual checklist**, not browser automation | Founder decision. A rendered-viewport test needs Playwright, which this project does not install. §8 states plainly what the static half cannot prove — see the note under A15 |
| **D4** | **Three reference widths: 375 / 768 / 1440.** Phone, tablet, desktop | 375 is the narrow end of current phones. Not 320: supporting a 2016 iPhone SE costs layout compromises on every page for a vanishing share |
| **D5** | **Tailwind is compiled at build time.** The play CDN is removed | `base.html:7` loads `cdn.tailwindcss.com`, which its own documentation excludes from production: it ships the compiler to the browser and recompiles on every page load. This is a performance defect against `SAAS_PRD:165` independently of layout |
| **D6** | **The sidebar becomes a drawer below `md`**, and stays a sidebar at and above it | The desktop layout is good and is not being redesigned. D2's "no redesign" is load-bearing: this spec changes *where* the nav is at narrow widths, not what it contains |
| **D7** | **Design-system criteria are structural only** — tokens exist and are used, never "looks good" | Aesthetic judgement cannot be a pytest assertion, and a criterion that cannot fail is not a criterion. §6 Step 4 asserts the tokens are defined and that raw hex values do not bypass them |

### 1.2 `OPEN — needs decision: founder`

| # | Question | Blocks |
|---|---|---|
| **O1** | **The minimum supported viewport width.** D4 assumes 375. Supporting 320 (iPhone SE 1st gen, ~0.2% of traffic) changes several layouts | **Blocks nothing.** Build at 375; the checklist and the static assertions are width-agnostic, and a later change to 320 edits one constant. Carried as **U2** |

---

## 2. Doc-fix prerequisites

Both edits exist because §0.1's three lines are individually true and jointly misleading.

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `SAAS_PRD:98` — *"Native mobile apps; non-Google auth; marketing automation; multi-language"* under **out of scope** | Keep the exclusion, **distinguish it from responsive web**. Native apps are out; the web app being usable on a phone is now in (D1/D2). As written, a reader reasonably concludes phones are not a target at all — which is what happened |
| **B2** | `SAAS_PRD:165` — *"landing page must be fast/responsive"* | Add the product app. The row is the **non-functional performance requirement**, so it is also where D5 belongs: shipping a browser-side Tailwind compiler on every page load is a performance defect the row already forbids in spirit |

**Not a doc-fix, but recorded:** there is **no UI/UX PRD**. Every other spec cites one and this
one cannot. That is a real gap in the doc set — this spec is carrying product intent it had to
establish itself (D1, D4, D6), which is the shape `docs/specs/README.md` warns about when it says
*"a spec never re-derives product intent."* Writing one is a founder task, not this spec's; §1.1
is the interim record.

---

## 3. File manifest

### New — the build

```
package.json                        tailwindcss + a build script; the first Node in the Python tree
tailwind.config.js                  content globs over templates/, the brand palette moved out of base.html
src/mihomes/web/static/app.css      the COMPILED output — committed, see §4.2
docs/UI_MANUAL_CHECKLIST.md         D3's other half: what a human walks, and what it means to pass
```

### New — tests

```
tests/unit/test_ui_responsive.py    A1-A11 — structural assertions over the template tree
tests/unit/test_ui_build.py         A12-A13 — the build config, and that the CDN is gone
tests/unit/test_ui_tokens.py        A14 — design tokens exist and are not bypassed
```

### Modified

| Path | Change |
|---|---|
| `src/mihomes/web/templates/base.html` | **The whole spec in one file.** Drawer + toggle + backdrop (D6); `<aside class="w-60">` at `:63` gains responsive visibility; compiled CSS replaces the CDN at `:7`; the inline `tailwind.config` block at `:10-19` moves to `tailwind.config.js` |
| `src/mihomes/web/templates/*.html` | Per-page fixes the audit names — table wrappers, grid breakpoints, form widths |
| `pyproject.toml` | Nothing. **This adds no Python dependency** |
| `.gitignore` | `node_modules/` |

---

## 4. Artifacts as code

§0.2: there is no schema here. `docs/specs/README.md` requires §4 to be *"real source, never
prose"* because prose leaves interpretation room — that requirement applies to the artifacts this
spec actually produces.

### 4.1 `base.html` — the drawer

The defect, measured at `2750bd1`:

```html
<!-- base.html:63 — a 240px fixed sidebar, with NO responsive prefix -->
<aside class="w-60 bg-gray-900 flex flex-col flex-shrink-0">
```

At 375px that is **64% of the viewport** before any content renders. Grep across the whole
template tree for `hamburger`, `drawer`, `off-canvas` or a nav toggle returns **zero matches** —
there is no mobile navigation pattern to fix, so one is being added.

The target structure, stated as code so "add a drawer" cannot be read three ways:

```html
<!-- Toggle: visible only below md, in the top bar -->
<button type="button" id="nav-toggle" aria-label="Open navigation"
        aria-controls="sidebar" aria-expanded="false"
        class="md:hidden p-2 -ml-2 rounded-lg hover:bg-gray-100">
  <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
    <path stroke-linecap="round" stroke-width="2" d="M4 6h16M4 12h16M4 18h16"/>
  </svg>
</button>

<!-- Backdrop: below md only, dismisses the drawer -->
<div id="nav-backdrop" class="hidden md:hidden fixed inset-0 bg-black/50 z-30"></div>

<!-- The sidebar, now a drawer below md and unchanged at md+ -->
<aside id="sidebar"
       class="w-60 bg-gray-900 flex flex-col flex-shrink-0
              fixed inset-y-0 left-0 z-40 -translate-x-full transition-transform
              md:static md:translate-x-0 md:z-auto">
```

**`md:static md:translate-x-0` is what makes D6's "no redesign" true**: at and above `md` every
one of those mobile classes is overridden and the computed layout is byte-for-byte what ships
today. The drawer exists only below the breakpoint.

**Keyboard and assistive access are part of the criterion, not polish** (A4): `aria-expanded`
tracks state, Escape closes, and focus moves into the drawer when it opens and back to the
toggle when it closes. A drawer that only a mouse can dismiss is a trap on a screen reader.

### 4.2 The build — and why the CSS is committed

```jsonc
// package.json
{
  "name": "mihomes-ui",
  "private": true,
  "scripts": {
    "build:css": "tailwindcss -i ./src/input.css -o ./src/mihomes/web/static/app.css --minify",
    "watch:css": "tailwindcss -i ./src/input.css -o ./src/mihomes/web/static/app.css --watch"
  },
  "devDependencies": { "tailwindcss": "^3.4" }
}
```

```js
// tailwind.config.js — the palette moves here from base.html:10-19
module.exports = {
  content: ["./src/mihomes/web/templates/**/*.html"],
  theme: { extend: { colors: { brand: {
    50:'#f0f9ff', 100:'#e0f2fe', 500:'#0ea5e9', 600:'#0284c7', 700:'#0369a1',
  } } } },
};
```

**`app.css` is committed to the repository**, and that is a deliberate deviation from normal
practice. The reasoning is `SPEC-002 D1`'s: the CLI is an operator tool and the deploy target is
a Python app on Fly. Requiring Node at deploy time to render a page would add a toolchain to the
production path for one asset. Committing the output keeps deployment Python-only.

The cost is that the file can go stale against the templates, which is exactly why **A13 asserts
freshness** rather than trusting a build step nobody runs.

---

## 5. Function signatures

No new Python functions. The only executable additions are test helpers, whose shapes matter
because §8's criteria are written against them:

```python
# tests/unit/test_ui_responsive.py

def templates() -> list[pathlib.Path]:
    """Every .html under web/templates/, including subdirectories.

    **Derived, never listed.** A hand-written list is how page 29 ships unaudited — the same
    reasoning SPEC-006 A11 used for enumerating category branches from the schema rather than
    from a literal.
    """

def layout_containers(html: str) -> list[str]:
    """The class attributes of elements that establish page layout — grids, flex rows, and
    anything with an explicit width. These are what break at 375px; a `text-sm` does not."""
```

---

## 6. Sequenced steps

Ordering follows the founder's stated priority, and **Step 1 must precede Step 3**: the audit's
per-page fixes are verified inside the frame Step 1 establishes, so auditing pages against a
broken shell measures the shell twice.

**Step 1 — the mobile navigation.** §4.1's drawer, toggle, backdrop and focus handling in
`base.html`. *Verify:* `base.html` declares a mobile nav pattern (A1); the sidebar is not
unconditionally visible below `md` (A2); the toggle carries `aria-expanded` and `aria-controls`
(A4).

**Step 2 — the production build.** `package.json`, `tailwind.config.js`, compiled `app.css`, the
palette moved out of `base.html`, the CDN script deleted. *Verify:* no template loads
`cdn.tailwindcss.com` (A12); the committed CSS is fresh against the templates (A13).

**Step 3 — the per-page audit.** Every template: tables wrapped for horizontal scroll, grids
given breakpoints, forms and modals bounded by viewport rather than fixed widths. **After Step
1.** *Verify:* no unwrapped `<table>` (A5); no hardcoded pixel width on a layout container (A6);
every page's top-level container declares a breakpoint (A7); no multi-column grid without a
responsive prefix (A8); modals are viewport-bounded (A9); interactive elements meet the tap-target
floor (A10).

**Step 4 — design tokens.** The brand palette in `tailwind.config.js` is the single source; raw
hex values in templates are replaced by token classes. **Structural only (D7).** *Verify:* the
tokens are defined and used, and templates do not bypass them with raw hex (A14).

**Step 5 — the manual checklist.** `docs/UI_MANUAL_CHECKLIST.md`: every page × three widths, with
what to look for and what counts as a failure. *Verify:* the checklist covers every template the
audit enumerates, with no page silently absent (A11).

**Exit criterion check.** With Steps 1–5 green: every page reachable and unclipped at all three
widths, nav operable throughout, CSS compiled. That is A15.

---

## 7. Non-goals and deferred scope

**N1 — Do not redesign anything.** The desktop layout is good. D6's breakpoint override exists so
the ≥`md` computed layout is unchanged; a step that "improves" a desktop page while passing
through has broken the one thing this spec must not.

**N2 — Do not add a JS framework.** The app is HTMX plus templates. A drawer is a class toggle
and about fifteen lines of vanilla JS. Introducing React/Alpine/Vue for it is a re-platform
wearing a bug fix's clothes.

**N3 — Do not build a native app or a PWA.** D2, and `SAAS_PRD:98` unchanged. No service worker,
no manifest, no offline story.

**N4 — Do not change any route, service or model.** This spec touches templates, one build
config, and tests. A pull request under it that modifies `services/` or `models/` is out of
scope by construction.

**N5 — Do not assert on pixel counts in tests.** D3: static assertions check *structure*, not
rendered geometry. A test claiming "this element is 44px" without a browser is asserting a
belief, and the belief is what needs checking.

**N6 — Do not use per-template responsive-utility counts as a criterion.** "Every template has ≥N
`md:` classes" is gamed by adding `md:block` to a `<div>`. The audit's counts say *where to look*;
the criteria assert structural properties.

**N7 — Do not let the design-system step grow.** D7. Tokens exist, tokens are used, raw hex does
not bypass them. Spacing scales, component libraries and typographic systems are a different
project, and it is the one most likely to swallow this one.

### `DEFERRED (later)` — leave room, do not build

| Item | Interface room to leave |
|---|---|
| Dark mode | Tokens in `tailwind.config.js` rather than hardcoded hex is the whole prerequisite. Step 4 delivers it incidentally; do not add a toggle |
| Rendered-viewport testing | If Playwright is ever sanctioned, A5–A10's structural assertions become the fallback layer rather than the only one. Nothing here needs rewriting for that |
| Offline / PWA | N3. Would need a service worker and a sync story; the gateways already cover the disconnected case |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | `base.html` declares a mobile navigation pattern — a toggle, a drawer, and a backdrop | `test_ui_responsive.py::test_mobile_nav_exists` |
| A2 | The sidebar is **not unconditionally visible below `md`**, and **is** visible at `md`+ | `test_ui_responsive.py::test_sidebar_is_responsive` |
| A3 | The drawer's mobile classes are all overridden at `md`+, so the desktop layout is unchanged (N1) | `test_ui_responsive.py::test_desktop_layout_unchanged` |
| A4 | The nav toggle carries `aria-expanded` and `aria-controls`, and the drawer is dismissible by keyboard | `test_ui_responsive.py::test_nav_is_accessible` |
| A5 | **No `<table>` renders outside a horizontally scrollable container** | `test_ui_responsive.py::test_tables_scroll` |
| A6 | No layout container carries a hardcoded pixel width | `test_ui_responsive.py::test_no_fixed_pixel_widths` |
| A7 | Every page's top-level content container declares at least one breakpoint | `test_ui_responsive.py::test_containers_declare_breakpoints` |
| A8 | No multi-column grid lacks a responsive prefix | `test_ui_responsive.py::test_grids_are_responsive` |
| A9 | Modals and dialogs are bounded by the viewport, not by a fixed width | `test_ui_responsive.py::test_modals_fit_viewport` |
| A10 | Interactive elements meet the tap-target floor | `test_ui_responsive.py::test_tap_targets` |
| A11 | **The manual checklist covers every template** — no page silently absent | `test_ui_responsive.py::test_checklist_is_complete` |
| A12 | **No template loads the Tailwind play CDN** | `test_ui_build.py::test_no_cdn_tailwind` |
| A13 | The committed `app.css` is **fresh** against the templates and config | `test_ui_build.py::test_css_is_current` |
| A14 | Brand colours come from tokens; no template hardcodes a brand hex value | `test_ui_tokens.py::test_no_raw_brand_hex` |
| A15 | **Every template is enumerated, and every one satisfies A5–A10** | `test_ui_responsive.py::test_exit_criterion` |

**A15 is the exit criterion**, and **A5 is the phase's definition of done.**

> **A page that overflows horizontally on a phone is unusable in a way the user cannot work
> around.** They cannot scroll to a column they cannot reach; they cannot tap a button off the
> right edge. Every other criterion here describes something degraded. This one describes
> something *broken*, and tables are where it happens — 28 pages of an estate-management product
> are mostly lists of things.

A15 is written as an **enumeration**: it walks `web/templates/**/*.html` from the filesystem and
applies A5–A10 to each. A hand-listed subset passes forever while page 29 ships unaudited — the
same construction SPEC-006 A11 used for `REVIEW_SCHEMA`'s categories, and for the same reason.

> **What these criteria cannot prove, stated plainly (D3).** Every assertion here is structural.
> Green means *no page declares a structure known to break*; it does not mean any page looks
> right, reads well, or is pleasant to use on a phone. Only a human at 375px knows that, which is
> what A11's checklist is for and why it is a criterion rather than a nicety. **A spec that
> claimed otherwise would be the more dangerous artifact** — the failure this whole set guards
> against is a green suite over a broken product.

---

## 9. Test manifest

```
tests/unit/test_ui_responsive.py   A1-A11, A15 — the structural audit, enumerated from the tree
tests/unit/test_ui_build.py        A12, A13 — the CDN is gone, the committed CSS is fresh
tests/unit/test_ui_tokens.py       A14 — tokens defined, tokens used, no raw hex bypass
```

**No new fixtures.** These tests read files; they need no database, no account and no client —
which also keeps them fast enough to run on every commit.

**How A13 works, since "is the CSS fresh" is not obvious.** Hash the template tree plus
`tailwind.config.js`, and compare against a stamp committed beside `app.css`. A template edit
that changes which classes are used therefore turns A13 red until `npm run build:css` runs.
Without it, the committed-output decision in §4.2 silently degrades into a stale stylesheet, and
the symptom is a class that works locally and does nothing in production.

**The adversarial pattern for A5.** Not "does the page contain `overflow-x-auto`" — a page can
carry that class on an unrelated element and still overflow. Find every `<table>`, walk **up** its
ancestor chain, and assert some ancestor establishes horizontal scrolling. The test must fail on a
table whose wrapper is a sibling rather than a parent.

---

## 10. What this stage does not make safe

- **It does not make the product usable on a phone.** It removes the structural defects that
  guarantee it is not. The difference is D3's whole subject, and A11's checklist is the only
  thing in this spec that speaks to actual usability.
- **It does not cover the landing app.** `SAAS_PRD:165`'s existing responsive requirement is
  Phase 0's and is out of scope here.
- **It does not address performance beyond the CDN.** Removing the browser-side compiler is one
  measurable win; page weight, image sizing and query counts are untouched.
- **It does not establish a design system.** D7/N7 — tokens exist and are used. Everything else a
  designer would mean by the phrase is deferred.
- **It does not close the doc-set gap.** There is still no UI/UX PRD, and §1.1 is a spec carrying
  product intent that should live one level up. The next UI change will face the same absence.
