# SPEC-009 Build Loop — end-of-run report

> **Status: COMPLETE**, with U3 open by design — the manual checklist is written, not walked.
> **Branch:** `worktree-spec-build-harness`. **Suite:** 2705 passed, 0 failed, 3 skipped
> (unchanged), 2 xfailed. **Reconciler:** `--collect` exits 0 at **15/15**,
> `PENDING_TESTS_IN_EXISTING_FILES` empty.
> Baseline at start: 2539 (after the pre-existing finance fix, `82c88d6`).

---

## 0. The premise was wrong, and correcting it is the report's first finding

The request was to start UI/UX work, on the belief that none had been done. **28 templates
already existed** — Tailwind, HTMX, all 28 using responsive prefixes — and `ui-frontend` was
fully contained in the build branch (269/0), not the 51-unpushed-commit branch that
`opportunities.md` and project memory both claimed. Both records were corrected.

**But "has responsive classes" was not "works on a phone".** The measured state:

- `base.html:63` — `<aside class="w-60 … flex-shrink-0">` with **no responsive prefix at all**:
  240px of a 375px viewport, 64% of the screen before any content, with `flex-shrink-0`
  explicitly forbidding it to give way. Grep for hamburger/drawer/off-canvas across the whole
  tree: **zero** non-table matches.
- `base.html:7` — the Tailwind **play** CDN, which ships a compiler to every browser. No build
  step anywhere in the repo, and no CSS file at all.

## 0.1 Why it was never built, which is the more useful answer

`SAAS_PRD` said three things that are individually true and jointly misleading: native apps are
out of scope (`:98`), the **landing page** must be responsive (`:165`), and the Staff Member —
*housekeeper, property manager, handyman* — reaches the product by *"chat via
Telegram/WhatsApp"* (`:44`).

Read together they are coherent: **the PRD routed the phone-holding persona to the gateways**,
not the web app. That is why no UI/UX PRD exists and why responsive was never a requirement.

SPEC-006 built that answer, and it did not remove the need — a housekeeper can log an issue by
message, but *seeing assigned homes* and *reviewing a task list* are web pages, and `:44` lists
both as their key jobs. So D1 records a product decision rather than deriving one, and G0's
doc-fix distinguishes native packaging (still out) from responsive web (now in).

---

## 1. What shipped

| Group | Step | Criteria | Commit |
|---|---|---|---|
| G0 | §2 doc repairs | — | `6bac399` |
| G1 | 1 — mobile navigation | A1–A4 | `66e227d` |
| G2 | 2 — production build | A12, A13 | `d0eee3d` |
| G3 | 3 — per-page audit | A5–A10 | `cb014d4` |
| G4/G5/G6 | 4, 5, exit | A14, A11, A15 | this commit |

**The measured deltas:**

| Defect | Start | End |
|---|---|---|
| Mobile nav patterns | 0 | drawer + toggle + backdrop, keyboard-operable |
| Tables that scroll | **0 of 20** (16 *clipped*) | 20 of 20 |
| `overflow-x-auto` in the tree | **0** | 20 |
| Non-responsive grids | 77 | 0 (+2 exempt) |
| Modal panels with no height cap | **20 of 43** | 0 |
| Tap targets under 30px | 43 | 0 |
| Raw brand hex bypassing tokens | 4 | 0 |
| Tailwind play-CDN references | 1 | 0 |
| CSS files in the repo | 0 | 1 compiled, 39KB |

---

## 2. Defects found by building — four, three of them in this run's own work

### 2.1 A gate caught a missing override in my own markup (G1)

A3 asserts every mobile-only class on the sidebar has an `md:` counterpart. It **failed on the
first version of the drawer**, because I wrote it without `md:z-auto`. A missing override
leaves the sidebar `fixed` or `z-40` at every width — desktop content sliding under it — while
A1 and A2 both stay green, because neither looks at desktop.

Mutation-checked all three overrides afterwards: each turns A3 red when dropped.

### 2.2 A pre-existing test that fails on any 31st

The G1 full-suite run surfaced `test_m4_forecast_divides_by_actual_history` failing. It seeds
"three months" with `today - timedelta(days=30 * m + 1)`, and from **the 31st** those offsets
land in only *two* calendar months — so `forecast()` correctly divided by 2 and the test
asserted 3.

**The production code was right; the fixture was lying about what it seeded.** Confirmed
pre-existing by stashing the tree and re-running clean. Fixed in `82c88d6` and verified across
seven boundary dates (four 31sts, a February boundary, a year rollover, leap day) rather than
just today. It never appeared in SPEC-006 or SPEC-008 because those ran on days where the
arithmetic happened to work.

### 2.3 Two matchers of mine that were wrong (G2)

- **The config would not parse.** A JSDoc comment containing a literal recursive glob — its
  `*/` closed the block comment early.
- **`fnmatch` has no `**` semantics.** It treats `**` as a single `*`, which cannot cross a
  directory boundary, so my coverage test passed on `partials/x.html` and **failed on all 28
  top-level templates** — reporting the config broken while the build was compiling correctly.
  Located because two gates disagreed: the CSS had already built at 39KB, and a config matching
  nothing would have produced an almost-empty file.

### 2.4 My own comments tripped my own checks (G2)

A12 and the inline-style check both failed on `base.html` — because the new comments *explain*
that the CDN and the `<style>` block were removed, and a substring scan flagged the explanation
as the defect.

Exactly the distinction G0 had to make for the PRDs, where "Corrected 2026-08-31…" notes quote
the stale string in order to fix it. **The lesson generalises: a regression gate over a document
must separate the claim from the commentary about the claim.** Both checks now strip HTML
comments — structural, and narrower than a keyword exemption.

---

## 3. Deviations — one, and it took three attempts to get right

### D12 — A7's scope was a measurement artifact

A7 read *"no template has zero responsive prefixes"*. §0.15 measured **8**; the test walks all
61 via `rglob` and reported **30**. Both right about different sets — the audit counted
top-level files.

But the extra 22 were partials like `alert_badge.html` (11 lines) and
`property_status_badge.html` (7) — inline `<span>` badges with **no layout to make
responsive**. Demanding a breakpoint means adding `md:` classes that change nothing: **N6's
gamed metric, arriving through A7's own door**.

So A7 is scoped by a derived predicate — and the predicate was wrong twice before it was right:

1. `grid|flex|w-*|max-w-*` — too broad. Every badge uses `inline-flex` for icon alignment.
2. `grid-cols|table|w-60..96|max-w-xl..7xl` — still too broad. **A `max-w-2xl mx-auto`
   container is already responsive**: max-width is a ceiling that shrinks on its own, and
   `w-full` is fluid by definition. Neither can overflow a viewport.
3. `grid-cols-[2-12]` or `<table>` — **33 in scope, exactly 1 genuinely failing.**

"30 templates failing" was an artifact of the wrong question. One was the truth.

---

## 4. G-Final — compound-stop verification

| | Condition | Result |
|---|---|---|
| **F.1** | full suite green (C) | ✅ **2705 passed**, 0 failed, 3 skipped (unchanged), 2 xfailed |
| **F.2** | every criterion by its own node id (E) | ✅ **all 15** |
| **F.3a** | every §6 step tasked (B) | ✅ 5 steps |
| **F.3b** | `--collect` exits 0 | ✅ **15/15**, pending set **empty** |
| **F.4** | smoke green (D) | ✅ 18 passed |
| **F.5** | this report | ✅ |
| **A** | every checkbox `[x]` or `[!]` | ✅ |

`PENDING_TESTS_IN_EXISTING_FILES` ended empty. Every entry expired with its group —
G3.1–G3.6 at G3, G5.1 at G5, G6.1 at G6. **§4 of the harness pre-declared this would happen**,
because §8 groups criteria by *file* and `test_ui_responsive.py` holds A1–A11 plus A15 across
four groups. First time in this set that the recurrence was a recognised event rather than a
rediscovery.

---

## 5. What is NOT done — and must not be read as done

### U3 is the gate that matters, and no test in this spec closes it

**Every assertion here is static.** Green means *no page declares a structure known to break* —
no clipped table, no uncapped modal, no grid frozen at four columns. It does **not** mean any
page is usable, readable, or pleasant on a phone.

`docs/UI_MANUAL_CHECKLIST.md` is generated and **NOT WALKED**. A11 asserts it *covers* every
navigable page; only a person at 375px can pass it. The document says so about itself, because
a generated checklist and a completed one look identical on the page — which is how U3 would
get mistaken for closed.

**A spec claiming otherwise would be the more dangerous artifact.** The failure this whole set
guards against is a green suite over a broken product.

### Unmet gates

| # | State |
|---|---|
| **U1** | **194 commits unmerged to `main`.** This run builds on the branch; canon has none of it |
| **U2** | O1 — the minimum viewport width. Built at 375; supporting 320 is undecided |
| **U3** | **The checklist is written, not walked.** See above |
| **U4** | **There is still no UI/UX PRD.** §1.1 of the spec carries product intent that belongs one level up, and the next UI change will meet the same absence |
| **U5** | Everything SPEC-005 §10, SPEC-006 §0.8 and SPEC-008 §0.8 carry |

### A named carve-out that needs a real answer

**`calendar.html`'s two `grid-cols-7` month grids are exempt from A8.** A month is seven columns
by definition; collapsing it to one produces 30 numbered boxes, not a calendar. It needs
horizontal scroll or a genuine list view below `md` — its own piece of work.
`test_the_calendar_exemption_is_still_needed` keeps the carve-out honest: if the file stops
using `grid-cols-7`, the exemption becomes a standing permission for any seven-column grid and
must be deleted.

---

## 6. For the next session

1. **Walk the checklist.** It is the only thing standing between "structurally sound" and
   "works on a phone", and it needs a human with a device.
2. **The calendar needs a mobile design**, not a breakpoint.
3. **Editing a template now requires `npm run build:css && npm run stamp`**, or A13 goes red.
   That is the deliberate cost of committing the build output (§4.2); the alternative is Node
   on the production deploy path.
4. **Write a UI/UX PRD** (U4), or the next UI spec will carry product intent it should be
   citing.
