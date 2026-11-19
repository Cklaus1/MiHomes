# SPEC-009 Build Loop — Responsive web UI

> **Input spec:** `docs/specs/SPEC-009-responsive-web-ui.md` (**1 open decision** — O1: minimum
> viewport width, blocks nothing)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing inherited **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `ca70150`.
> **Status: COMPLETE 2026-08-31.** 15/15 criteria, suite 2705 passed. **U3 open by design**
> — the manual checklist is written, not walked. See `tasks/build-loop-spec009-report.md`.

**The stake:** `SAAS_PRD:44`'s Staff Member — *housekeeper, property manager, handyman* — has
"see assigned home(s), complete tasks, log issues" as key jobs. **That person is standing in a
house holding a phone.** The product currently answers them with a 240px fixed sidebar on a 375px
screen: 64% of the viewport before any content renders.

**Exit criterion:** A15 — every template enumerated from the filesystem satisfies A5–A10.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, four DB env vars | ✅ — though **these tests need none**; §0.3 |
| P2 | Node ≥18 + npm, for Step 2's Tailwind build | ⚠️ **verify before G2**, not before G1 |
| P3 | The 28 templates and `base.html` as measured | ✅ audited at `2750bd1`, §0.15 of the spec |
| P4 | **The 184-commit merge to `main`** | ❌ **OPEN, not this run's job** — U1 |

**Environment:** as every prior harness. `py -m pytest`, never `python`.

**P2 is the only new prerequisite in this spec, and it halts G2 alone.** Conventions §3.2: a
missing prerequisite halts *before* the task that needs it rather than poisoning three in a row.
G1, G3, G4 and G5 are pure template and test edits and need no Node at all.

---

## 0.3 What makes this harness different from the five before it

**No database, no fixtures, no tenancy.** Every test here reads files off disk. That has three
consequences worth stating, because each inverts a habit the previous runs built:

1. **The suite is fast** — no `session`, no `web_client_factory`, no engine pinning. The two
   fixture hazards that cost SPEC-006 real time (`cli_database` repointing `DATABASE_URL`;
   `session` + `web_client_factory` leaking tenant context) **cannot occur here**. Do not
   defensively import either fixture.
2. **There is no schema gate.** No `ENTITY_CLASSES`, no `TENANT_TABLES`, no pinned counts, no
   migration. C8's map — carried into every harness since SPEC-006 G1 — **does not apply**.
3. **The failure mode moves.** Previous specs risked a step not done. This one risks a step that
   *looks* done because a class was added somewhere. §3's gates exist for that, and N6 forbids
   the metric most likely to be gamed.

---

## 0.4 Stop condition

Per conventions §0, all five.

| | Condition | For SPEC-009 |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a + F.3b |
| **C** | full suite green **including this spec's new tests** | baseline **2538 passed** |
| **D** | smoke green | `tests/integration/test_smoke_all_tools.py` |
| **E** | every §8 criterion green by its own named test | **all 15**, F.2 |

**Baseline — HEAD `82c88d6`:** `2539 passed, 3 skipped, 2 xfailed, 0 failed`. **A new skip is
red.**

**The baseline moved by one, and not because of this spec.** G1's full-suite run surfaced
`test_m4_forecast_divides_by_actual_history` failing — a **pre-existing, date-dependent
fixture** that seeds "three months" with 30-day offsets and therefore lands in two calendar
months on any 31st. Confirmed pre-existing by stashing the tree and re-running clean. Fixed in
`82c88d6` rather than deferred, because it blocks this spec's own condition C and CLAUDE.md's
autonomous bug-fixing rule covers exactly that case. It had not appeared in SPEC-006 or SPEC-008
because those ran on days where the arithmetic happened to work.

---

## 0.5 The measured starting point, and how to read it

Every number below is a **defect count that must reach zero**, measured at `2750bd1`. They are
the harness's progress ledger, not criteria — N6 forbids asserting on counts.

| Defect | Start | Target | Group |
|---|---|---|---|
| Mobile nav patterns in `base.html` | **0** | ≥1 | G1 |
| Tables that scroll | **0 of 20** | 20 of 20 | G3 |
| — of which *clipped* by `overflow-hidden` | **16** | 0 | G3 |
| `overflow-x-auto` anywhere in the tree | **0** | ≥20 | G3 |
| Templates with **zero** responsive prefixes | **8** | 0 | G3 |
| Non-responsive `grid-cols-N` (N≥2) | **78** | 0 (less the calendar carve-out) | G3 |
| Modal panels with no height cap **and** no scroll | **20 of 43** | 0 | G3 |
| Genuine `min-w-[…]` hazards | **5** | 0 | G3 |
| Tap targets under ~30px | **4** | 0 | G3 |
| Tailwind play-CDN references | **1** (`base.html:7`) | 0 | G2 |
| CSS files in the repo | **0** | 1 compiled | G2 |

**Three things measured as already correct. A group that "fixes" one has broken something:**
the viewport meta at `base.html:5`; the `hidden sm:table-cell` column-hiding pattern (62 uses);
modal *horizontal* gutters in both families.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION

Conventions §3.1 requires this before task 1. **SPEC-009 was written against `2750bd1` and the
audit ran at that ref**, one commit before this harness — so unlike SPEC-006 (181 commits stale)
and SPEC-008 (181 commits stale), the spec's claims are current by construction.

Two spot-checks were run independently of the audit before the spec was committed:
`base.html` has exactly **2** responsive prefixes, and `overflow-x-auto` count is **0**. Both
matched.

**Re-run §0.5's table before G1** and halt on any mismatch. The likely source of drift is not
time but *this run itself*: G1 changes `base.html`, so G3's starting numbers must be re-measured
after G1 lands, not taken from the table above.

---

## 0.7 O1 — blocks nothing

**O1 — the minimum supported viewport width.** D4 assumes **375**. Supporting 320 (iPhone SE 1st
gen) would change several layouts.

**Blocks-ship at worst, and arguably not even that.** Build at 375; every static assertion in §8
is width-agnostic — none of them names a pixel value — and the manual checklist takes the width
as a parameter. A later move to 320 edits one constant in one document. Carried as **U2**.

---

## 0.8 UNMET LAUNCH GATES

| # | What | Owner |
|---|---|---|
| **U1** | **184 commits unmerged to `main`.** This run builds on the branch; canon has none of it | founder |
| **U2** | **O1 — the minimum viewport width.** Built at 375 | founder |
| **U3** | **The manual checklist is written, not walked.** A11 asserts it *covers* every page; only a human at 375px can *pass* it. **This is the gate that decides whether the product is actually usable on a phone**, and no test in this spec can close it | founder |
| **U4** | **There is still no UI/UX PRD.** §1.1 of the spec is carrying product intent that belongs one level up | founder |
| **U5** | Everything SPEC-005 §10, SPEC-006 §0.8 and SPEC-008 §0.8 carry, unchanged | founder |

---

## 1. Task DAG

`py scripts/spec009_reconcile.py --collect` joins §8 → §9 → §1. **Run after every group commit.**

**Ordering the spec names as load-bearing:** **Step 1 before Step 3** — the audit's per-page
fixes are verified inside the frame Step 1 establishes, and auditing pages against a broken shell
measures the shell 28 times. **Step 2 after Step 1** — see G2's note; the build inverts the
edit loop.

### [x] G0 — the doc repairs — *dep: none*
- [x] G0.1 · §2 B1 · — · `SAAS_PRD:98` — keep the native-app exclusion, **distinguish it from responsive web** (D1/D2). As written a reader concludes phones are not a target, which is what happened · verify: `tests/unit/test_docs_ui_scope.py::test_native_and_responsive_are_distinguished`
- [x] G0.2 · §2 B2 · — · `SAAS_PRD:165` — add the product app beside the landing page, and D5's build requirement to the same non-functional row · verify: `tests/unit/test_docs_ui_scope.py::test_product_app_is_in_the_performance_row`

### [x] G1 — Step 1: the mobile navigation — *dep: G0 — MUST precede G3*
- [x] G1.1 · §6 Step 1 · A1 · the drawer, toggle and backdrop of §4.1 in `base.html` — **there is no existing pattern to extend**; grep for hamburger/drawer/off-canvas returns zero across the whole tree · verify: `tests/unit/test_ui_responsive.py::test_mobile_nav_exists`
- [x] G1.2 · §6 Step 1 · A2 · `<aside class="w-60 … flex-shrink-0">` (`base.html:63`) must not be unconditionally visible below `md`, and **must** be at `md`+ · verify: `tests/unit/test_ui_responsive.py::test_sidebar_is_responsive`
- [x] G1.3 · §6 Step 1 · A3 · **N1's guard** — every mobile class is overridden at `md`+ (`md:static md:translate-x-0`), so the ≥`md` computed layout is byte-for-byte what ships today · verify: `tests/unit/test_ui_responsive.py::test_desktop_layout_unchanged`
- [x] G1.4 · §6 Step 1 · A4 · `aria-expanded` + `aria-controls`, Escape closes, focus moves in and back. **A drawer only a mouse can dismiss is a trap on a screen reader** · verify: `tests/unit/test_ui_responsive.py::test_nav_is_accessible`

### [x] G2 — Step 2: the production build — *dep: G1 — needs P2 (Node)*
- [x] G2.1 · §6 Step 2 · A12 · `package.json`, `tailwind.config.js`, compiled `app.css`; the palette moves out of `base.html:10-19`; **the CDN script at `base.html:7` is deleted** · verify: `tests/unit/test_ui_build.py::test_no_cdn_tailwind`
- [x] G2.2 · §6 Step 2 · A13 · the committed CSS is **fresh** against templates + config — a hash stamp, because §4.2 commits the output and a stale stylesheet fails silently in production only · verify: `tests/unit/test_ui_build.py::test_css_is_current`

> **G2 inverts the edit loop, which is why it is second and not first.** Today the play CDN
> compiles in the browser, so *any* class a template names works — including arbitrary values
> and including everything G1 and G3 add. After G2 a class the `content` globs do not see
> **silently does not exist**: no style, in production, having looked correct in development.
> The glob must cover `templates/**/*.html` — `partials/`, `settings/`, `team/` and
> `onboarding/` all hold classes.

### [x] G3 — Step 3: the per-page audit — *dep: G1*
- [x] G3.1 · §6 Step 3 · A5 · **20 tables, 0 scroll, and 16 are *clipped*** by `overflow-hidden` card wrappers — unreachable, not merely off-screen. Add a scroll wrapper **inside** the card; removing `overflow-hidden` from the card destroys its rounded corners · verify: `tests/unit/test_ui_responsive.py::test_tables_scroll`
- [x] G3.2 · §6 Step 3 · A6 · the **5** genuine `min-w-[…]` hazards. `max-w-[…]` paired with `truncate` **constrains** and is not one — 8 of those exist and must be left alone · verify: `tests/unit/test_ui_responsive.py::test_no_fixed_pixel_widths`
- [x] G3.3 · §6 Step 3 · A7 · the **8 zero-prefix templates**, `calendar.html` (408 lines) and `inventory.html` (511) worst. A **floor, not a threshold** — N6 · verify: `tests/unit/test_ui_responsive.py::test_no_zero_prefix_templates`
- [x] G3.4 · §6 Step 3 · A8 · **78** non-responsive `grid-cols-N`, mostly modal form pairs. **`calendar.html:56,64`'s `grid-cols-7` are exempt** — a month is seven columns by definition; it scrolls or becomes a list, it does not collapse to one column · verify: `tests/unit/test_ui_responsive.py::test_grids_are_responsive`
- [x] G3.5 · §6 Step 3 · A9 · **20 of 43 modal panels** have no height cap and no scroll, so the submit button grows off-screen. **23 already do it right** with `max-h-[90vh] overflow-y-auto` — apply the codebase's own pattern. The defect is vertical; gutters are fine · verify: `tests/unit/test_ui_responsive.py::test_modals_cap_height`
- [x] G3.6 · §6 Step 3 · A10 · tap targets. `base.html:177`'s 28×28px preview-close is on **every page**; `inventory.html` holds two more · verify: `tests/unit/test_ui_responsive.py::test_tap_targets`

### [x] G4 — Step 4: design tokens — *dep: G2*
- [x] G4.1 · §6 Step 4 · A14 · the brand palette in `tailwind.config.js` is the single source; no template hardcodes a brand hex. **Structural only (D7/N7)** — tokens exist, tokens are used, raw hex does not bypass them. Nothing about spacing scales or typography · verify: `tests/unit/test_ui_tokens.py::test_no_raw_brand_hex`

### [x] G5 — Step 5: the manual checklist — *dep: G3*
- [x] G5.1 · §6 Step 5 · A11 · `docs/UI_MANUAL_CHECKLIST.md` — every template × three widths, **enumerated from the filesystem** so page 29 cannot be silently absent. This is **U3**: the test asserts coverage, only a human asserts usability · verify: `tests/unit/test_ui_responsive.py::test_checklist_is_complete`

### [x] G6 — the exit criterion — *dep: all*
- [x] G6.1 · §6 exit · A15 · **every template enumerated from disk satisfies A5–A10.** A hand-listed subset passes forever while page 29 ships unaudited — SPEC-006 A11's construction · verify: `tests/unit/test_ui_responsive.py::test_exit_criterion`

### [x] G-Final — Compound-stop verification
- [x] F.1 · full suite green (condition C) — baseline **2538 passed**; a new skip is red
- [x] F.2 · every §8 criterion green by its own named test (condition E) — **all 15**, by node id
- [x] F.3a · walk §6: every step has a task (condition B) — **5 steps**
- [x] F.3b · `py scripts/spec009_reconcile.py --collect` exits 0, `PENDING_TESTS_IN_EXISTING_FILES` **empty**
- [x] F.4 · smoke green (condition D)
- [x] F.5 · write `tasks/build-loop-spec009-report.md`

---

## 2. Group-specific gates

| Group | Gate | Failure class it targets |
|---|---|---|
| **G1** | A3 asserts the **desktop layout is unchanged** | A drawer that also alters ≥`md` has broken N1 while A1/A2 stay green |
| **G2** | A13's freshness hash | A committed stylesheet silently stale against the templates — invisible until production |
| **G3** | A5 walks **ancestors**, not siblings | `overflow-x-auto` on an unrelated element passes a naive substring check |
| **G4** | Tokens asserted **used**, not merely defined | A palette nothing references is decoration |
| **G5** | The checklist is **derived from disk** | A hand-written page list is how page 29 goes unaudited |

---

## 3. Gates this spec cannot close by itself

Conventions §0: *"a stub can satisfy A+B+C+D."*

| Gate | Check | Closes |
|---|---|---|
| **G-ancestor** | A5 finds each `<table>`, walks **up** its parents, and asserts one establishes horizontal scroll — and that no ancestor *between* table and scroller re-clips with `overflow-hidden` | **A5** — the 16 clipped tables sit in `overflow-hidden` cards, so a wrapper added in the wrong place looks right and still clips |
| **G-override** | A3 parses `base.html`'s `<aside>` class list and asserts **every** mobile-only class has an `md:` counterpart | **A3** — "the desktop is fine" is a claim no behavioural test makes; a missing `md:static` breaks desktop while every mobile criterion passes |
| **G-freshness** | A13 hashes templates + config and compares to a committed stamp | **A13** — the one defect whose symptom appears *only* in production |
| **G-derived** | A11 and A15 enumerate templates from the filesystem, never a literal | **A11/A15** — the failure that has recurred in every spec in this set |

---

## 4. Recurring hazards, pre-declared

**`PENDING_TESTS_IN_EXISTING_FILES` will be needed at least twice.** §8 groups by **file**:
`test_ui_responsive.py` holds A1–A11 **and** A15 — spanning G1, G3, G5 and G6. So G1 creates the
file every later group writes into, and `--collect` starts checking node ids that correctly do
not resolve yet. This recurred **three times** across SPEC-006 and was rediscovered each time.
Add the entry, annotate it, **delete it the moment its group lands**.

**Every negative assertion needs a positive twin** (§0.5b). This spec is almost entirely
negatives — "not visible below `md`", "no unwrapped table", "no zero-prefix template", "no raw
hex". Each is trivially satisfied by deleting the thing. A2 must assert the sidebar **is**
visible at `md`+; A5 that a table still renders; A14 that the tokens are *used*.

**SPEC-006 shipped three criteria green over a webhook that dispatched nothing**, because the
rows counted came from a different code path. The equivalent here: a template that passes A5–A10
because its content was removed. G6's enumeration is the guard — it asserts every template, and
an empty template still has to satisfy A7.

**Do not count responsive prefixes** (N6). `sm:` is **640px** — above every phone width — so a
count rewards classes that do nothing at 375px. Assert structural properties.
