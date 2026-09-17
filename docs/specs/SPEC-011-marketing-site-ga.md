# SPEC-011 — Marketing Site for GA: trial, demo, and paid entry

**Phase:** 4 (GA) — `../product/SAAS_PRD.md` §10
**Status:** Draft — not ready to build (see §1.3)
**Written:** 2026-09-17
**Supersedes:** the CTA and pricing layers of `SPEC-001-phase0-landing-waitlist.md`. Everything
else in SPEC-001 — the app boundary, the email package, the waitlist table — stands.
**Source PRDs:** `../product/GTM_LAUNCH_PLAN.md` §1–2, §6–8, `../product/PRICING_AND_PACKAGING.md`,
`../architecture/BILLING_AND_EMAIL.md`

---

## 0. Context — why this spec exists

**A marketing page already exists and works.** SPEC-001 shipped `src/mihomes/landing/` in August:
nine sections, waitlist capture with double opt-in, a Google OAuth stub, per-IP rate limiting, and
a passing test suite. Reading the ask as "build a landing page" would rebuild a solved problem.

What has changed is the *premise*. SPEC-001 was written when there was no product:

> **`SPEC-001` §7-N2** — "Do not create a `users` table, a session, or a login cookie."

That is now false. SPEC-002 through SPEC-010 shipped multitenancy, onboarding, RBAC, Stripe
billing, a responsive UI, and email/password auth. The landing page still sells a *queue* because
that is all Phase 0 had to sell. Every CTA on it — hero, all three pricing cards, the closing form
— resolves to the same waitlist:

> **`landing/templates/index.html:135`** — "All three start with the waitlist below."

The gap this spec closes: a visitor who wants to **try**, **see a demo**, or **pay** has nowhere to
go. Those are three different intents with three different destinations, and the page has slots for
none of them.

**Scope.** Convert the Phase 0 waitlist page into a GA marketing site. Keep the app boundary, the
copy, and the section structure. Replace the CTA layer, add the pages a buyer needs before they
trust a purchase, and route each intent to a real destination.

---

## 1. Decisions

### 1.1 Delta against SPEC-001 — which decisions survive

Walking SPEC-001 §1.1–1.2. A decision that silently went stale is the main risk this table exists
to catch.

| SPEC-001 | Decision then | Status now | Resolution |
|---|---|---|---|
| **D1** | Standalone app, shares nothing with `mihomes.web` | **Rationale changed, conclusion holds** | The original reason — "the existing app is the single-user product with no authentication" — is obsolete; `mihomes.web` now has sessions, RBAC and RLS. But the boundary is now load-bearing for *different* reasons. See §2 |
| **D2** | Fly.io, single region | Unchanged | Keep |
| **D3** | Postgres, `waitlist` table only | **Superseded** | The marketing app now also reads plan metadata. Still no estate data. See §2.3 |
| **D4** | `waitlist` global, no `account_id` | Unchanged | Keep. Phase 1 did not convert it |
| **D5** | UUIDv7 via `mihomes.ids.new_id()` | Unchanged | Keep |
| **D6** | Server-side Jinja email templates | Unchanged | Keep |
| **D7** | Double opt-in required | **Narrowed** | Still required *for the waitlist*. A trial signup is not a waitlist row and must not inherit the confirm gate — that is the auth stack's flow. See §3 |
| **D8** | OAuth stub: verify token → waitlist row, nothing else | **Now false** | Google sign-in must reach the real auth stack. The stub is the single biggest piece of dead code this spec removes. See §3.2 |
| **D9** | Alembic as a deploy step, never on startup | Unchanged | Keep |
| **D10** | In-process per-IP rate limiting | Unchanged | Keep, and extend to the demo form (§4.2) |
| **D11** | DMARC without strict alignment | Unchanged | Keep |
| **D12** | `send.mihomes.ai` sending domain | Unchanged | Keep |
| **D13** | Marketing on apex, `app.` reserved | **Now activated** | `app.mihomes.ai` stops being a 503 placeholder and becomes the product. This is what makes the two-app split work at GA. See §2.1 |
| **D14** | Plan *shapes* only, no dollar figures | **Still true — verified 2026-09-17** | Every price in `PRICING_AND_PACKAGING.md` is still `PLACEHOLDER` (`:18-19`). See O1 |
| **D15** | Chat-intake card: Telegram only | **Verify before build** | Telegram shipped and works. WhatsApp did not. Re-read the card at build time rather than trusting this row |

### 1.2 Locked — new decisions

| # | Decision | Choice | Rationale |
|---|---|---|---|
| **D16** | App boundary | **Keep the two-app split.** Marketing stays `mihomes.landing` on the apex; the product stays `mihomes.web` on `app.mihomes.ai` | D1's original rationale is gone but three new ones replaced it — a public cache-friendly surface, a blast radius that excludes tenant data, and the `<1.5s` LCP budget. Merging the apps to "save a route" trades all three for nothing. See §2 |
| **D17** | Three CTAs, three destinations | **Trial** → `app.mihomes.ai/signup`; **Demo** → booking link; **Paid** → pricing page, no live checkout | The core content gap. One button cannot serve three intents. See §3 |
| **D18** | Primary CTA | **"Start free"** — replaces "Join the waitlist" everywhere | The Free tier (1 home, 3 seats, free forever) is canon in `PRICING` and already built. A waitlist in front of a working free tier is friction that converts nothing |
| **D19** | Waitlist retained, demoted | Keep the table, the routes, and the email. Demote to a **fallback** surface, not the primary CTA | `GTM:293` makes confirmed signups the Phase 0 gate metric and `SPEC-001` §7 DEFERRED says "do not delete rows on conversion; `confirmed_at` is the funnel baseline". Deleting the waitlist destroys the baseline |
| **D20** | Pricing page | **New standalone page** at `/pricing`: tier shapes + **real limits** + feature matrix, **no dollar figures** | D14 still binds on *prices*, but the **limits are canon in code** — `entitlements/limits.py` holds `PLAN_LIMITS` with `max_homes` 1/5/unlimited, `max_seats` 3/10/50, `ai_calls_per_month` 200/3,000/15,000. Showing real limits without prices makes the page substantive rather than vague, and none of it is invented |
| **D21** | Demo capture | **Booking link** (Cal.com / Savvycal), not a built scheduler | Founder motion per `GTM:325` is "personally onboard the first ~20–50 signups". Building a scheduler to replace a link is the wrong week of work |
| **D22** | Trust surface | Real **Terms** and **Privacy** pages, served by the marketing app | These are currently 404 **by design** (`SPEC-001` O1). At GA they are legally required before capture and are the single most common reason an ICP buyer bounces at the payment step |
| **D23** | Social proof | **Real quotes or nothing.** No logos, no star ratings, no fabricated counts | `GTM:91` — "Never fabricate testimonials." This ICP verifies claims |
| **D24** | Security/compliance claims | State only what ships: per-account isolation, role-scoped access. **No SOC 2, no "bank-grade", no uptime numbers** | MFA is still open pre-GA (`SPEC-010` U4). A family-office buyer reads these literally and a false claim is worse than a missing one |
| **D25** | The category claim | Say the differentiator explicitly: **"the only owner-side, multi-home, team-aware, AI-first option"** | `GTM:30` — "say this explicitly on the page; **'command center' alone is not a differentiator**." The current page leads with "command center" and never makes the contrast. This is the single highest-value copy change in the spec |
| **D26** | Tone | Calm, competent, understated. No "revolutionary", no exclamation marks, no stock-photo cheer | `GTM:51`. The existing copy already holds this line — new copy must not break it |

### 1.3 `OPEN — needs decision: founder`

These block **launching**, not building, except O1 and O2 which block the pricing page's content.

> **O6 and O7 are the long-lead items, and neither is an engineering task.** A NonCommercial
> license and unpublished legal pages both block *charging money* — the goal this whole spec
> serves — and no amount of build work moves either. Everything else in this table is downstream
> of them. Start them first; they are the critical path.

| # | Question | Blocks | Notes |
|---|---|---|---|
| **O1** | **Final prices** — is Pro $20/mo and Estate $60/mo, or does research move them? | The pricing page showing numbers at all | `PRICING:18-19` still marks both `PLACEHOLDER`. Until ratified, D14/D20 hold and the page shows shapes only |
| **O2** | **Stripe account live + price IDs created** | Any checkout button | Carried from SPEC-004, still open. Until then "Start free" is the only real money path and Pro/Estate are "Talk to us" |
| **O3** | **Founding-member offer terms** — extended trial or annual discount? | Whether the offer survives GA at all | SPEC-001 O2, still open. At GA the waitlist is demoted; decide whether the promise made to existing waitlist rows is honored and how |
| **O4** | **Trial shape** — 14-day Pro trial, or Free tier only? | The "Start free" destination's behavior | `PRICING:140` recommends "14-day Pro trial, no credit card, started on first gated action (not automatically at signup)". If adopted, "Start free" still lands on signup; the trial begins later, inside the product |
| **O5** | **Demo booking tool + calendar** | The demo CTA's href | D21 picks the shape, not the vendor |
| **O6** | **ToS + Privacy drafted and counsel-reviewed** | Publishing, and therefore GA | SPEC-001 O1 unresolved since July. `GTM:351` flags counsel review. No doc owns drafting it — this is the longest-lead item here |
| **O7** | **Repository license is NonCommercial** | Charging money at all | Verified 2026-09-17: `LICENSE` is **CC BY-NC 4.0** and `pyproject.toml:10` declares `license = {text = "CC-BY-NC-4.0"}`, copyright Chris Klaus. A commercial SaaS under a NonCommercial license is a contradiction that a paying customer's counsel may raise. Relicensing is the owner's call and is **not a code change** — surface it, do not fix it |

---

## 2. Architecture — why the split stays

### 2.1 The two surfaces at GA

```
mihomes.ai            →  mihomes.landing   public, cacheable, no session, no tenant data
app.mihomes.ai        →  mihomes.web       authenticated product, RLS, per-tenant
```

D13 reserved `app.mihomes.ai` and let it 503. This spec activates it. Every CTA on the marketing
site is a cross-origin link to `app.mihomes.ai/signup` or `/login` — a plain `<a href>`, no shared
session, no shared cookie.

### 2.2 Why not merge into `mihomes.web`

D1's stated reason is dead, so the decision deserves re-justification rather than inheritance.
Three reasons replace it, and a CI test already enforces the boundary:

**`tests/integration/test_landing_app.py::test_no_single_user_router_is_mounted`** asserts the
marketing app mounts none of the product's routers. It passes today. Merging the apps means
deleting that test, which should feel expensive.

1. **Blast radius.** The marketing app's Postgres role needs `waitlist` and nothing else. A public
   surface that cannot reach estate data cannot leak it — regardless of future bugs. Note the
   precedent in `web/app.py:91-99`: an unauthenticated static mount was removed as a cross-tenant
   hole because "a static mount has nowhere to put an authorisation check."
2. **`/` is taken, and contested.** `web/routes/dashboard.py:24` is `@router.get("/")` with
   `@declares("property.view", Access.COLLECTION)`. Anonymous `GET /` raises 401, which
   `web/app.py:190` redirects to `/login`. Serving marketing from the same `/` means branching the
   dashboard on session state *and* punching a hole in the 401 handler. The harness log already
   carries `fix(onboarding): / and /onboarding/ bounced off each other forever` — this path is
   historically fragile.
3. **Performance budget.** `GTM:55` requires <1.5s LCP on 4G. The marketing app ships inlined
   critical CSS and one SVG with no session lookup and no RLS round-trip. Serving it from the
   product app puts it behind session middleware for a visitor who has no session.

**The cost of the split, stated honestly:** the two apps have different palettes — marketing uses
warm neutrals (`--ink:#1c1c1a`, `--bg:#f6f6f4`), the product uses a sky-blue `brand-*` Tailwind
ramp. A visitor crossing from `mihomes.ai` to `app.mihomes.ai/signup` sees a visual seam. §5
addresses it; it is a real tradeoff, not a non-issue.

### 2.3 Database access (supersedes D3)

The marketing app gains read access to plan *metadata* for the pricing page, and keeps its
`waitlist` write. It gains **no** access to `accounts`, `users`, or any tenant table.

Plan metadata is **config, not a query** — the tier shapes come from the same config that drives
entitlements (`PRICING` §75: "entitlement keys the service resolves per account … config-driven"),
imported as data. The pricing page must render with the database down.

---

## 3. The three intents

The core of this spec. Each intent gets a destination, a CTA label, and a failure mode to avoid.

### 3.1 Trial — "Start free"

**Destination:** `https://app.mihomes.ai/signup`
**Appears:** hero (primary), pricing Free card, closing CTA, sticky nav

The Free tier already exists, is already built, and is already described accurately on the page
("One home, three seats. Free forever."). This is the highest-intent path and it currently dead-ends
in a waitlist.

Per O4, the trial may start on first gated action rather than at signup — that behavior lives in the
product, not here. The marketing site's only job is to hand the visitor to `/signup` with their
intent intact.

> **Failure mode to avoid.** Do not reintroduce a waitlist gate in front of signup "to manage
> load". A working free tier behind a queue converts worse than either alone.

### 3.2 Demo — "Book a demo"

**Destination:** external booking link (O5)
**Appears:** hero (secondary), pricing Estate card, nav

The Estate tier targets family offices and estate managers. That buyer does not self-serve; they
book a call. `GTM:325` already commits the founder to personally onboarding the first 20–50 signups
— this CTA is the front door to that motion, not new work.

Per D21 this is a link, not a built scheduler.

### 3.3 Paid — "See pricing"

**Destination:** `/pricing` (new page, §4.1)
**Appears:** nav, pricing teaser "See full pricing →"

Until O1 and O2 close, the pricing page shows **tier shapes and a feature matrix with no dollar
figures**, Free → signup, Pro → "Talk to us", Estate → "Talk to us". When Stripe lands, Pro's CTA
becomes checkout and prices appear. The page is built so that change is content, not a rewrite.

### 3.4 What happens to the waitlist (D19)

The routes, table, service, and confirmation email stay. The waitlist stops being the primary CTA
and becomes a fallback — for a visitor who wants updates but is not ready to sign up. The
`confirmed_at` funnel baseline is preserved.

---

## 4. File manifest

### Modified — `src/mihomes/landing/`

```
templates/index.html        CTA layer replaced (§3); hero, pricing, closing sections rewired.
                            Section structure and copy otherwise preserved.
templates/base.html         sticky nav (GTM §2 "sticky, minimal nav" — currently absent);
                            footer links resolve instead of 404 (D22)
routes.py                   + GET /pricing, GET /legal/terms, GET /legal/privacy,
                            + POST /demo (or external link only, per D21/O5)
oauth.py                    DELETE or redirect. The D8 stub creates a waitlist row and no
                            session — obsolete once Google sign-in reaches the real auth
                            stack at app.mihomes.ai/login (§3.2, N3)
```

### New — `src/mihomes/landing/`

```
templates/pricing.html      tier shapes + feature matrix, no dollar figures (D20)
templates/legal_terms.html  (D22, content blocked on O6)
templates/legal_privacy.html
plans.py                    plan shapes as config-derived data (§2.3)
```

### Not modified

```
src/mihomes/web/**          the product app is untouched by this spec
src/mihomes/models/waitlist.py
src/mihomes/services/waitlist.py
src/mihomes/services/email/**
```

---

## 5. Design

**Reuse the marketing app's existing tokens.** `landing/templates/base.html:11` defines
`--ink/--muted/--line/--bg/--card`. New pages use those variables. Do not introduce a third
palette.

**Do not import the product's Tailwind build into the marketing app.** N5 (no bundler, no CDN, no
web fonts) still binds, and the CI guards in `tests/unit/test_ui_tokens.py` and
`test_ui_build.py` scope to `src/mihomes/web/templates` only — the marketing app's inline CSS is
legal precisely because it is outside them. Pulling `app.css` in would blow the LCP budget and
couple the two deploys.

**The visual seam (§2.2) is closed from the product side, not this one.** The product's public
pages (`login.html`, `signup.html`) already use a neutral `bg-gray-900` CTA rather than brand blue,
which is close to the marketing app's `--ink`. Aligning the signup page's header wordmark to the
marketing header is a one-template change *in `mihomes.web`* and is **out of scope here** — noted
so it is not lost.

> **If any work does touch `src/mihomes/web/templates/`:** raw hex is forbidden
> (`test_no_raw_brand_hex`) and the CSS stamp gate requires `npm run build:css && npm run stamp`
> before commit (`test_css_is_current`).

---

## 6. Sequenced steps

**Step 1 — plan config.** `plans.py`, tier shapes as data. Verify: renders with the DB down.

**Step 2 — pricing page.** `/pricing` + `pricing.html`. Verify: no dollar figures in the rendered
HTML (the A16 test pattern, extended).

**Step 3 — CTA rewire.** `index.html` hero, pricing cards, closing section per §3. Verify: no CTA
resolves to `/waitlist` except the demoted fallback; trial CTAs point at `app.mihomes.ai/signup`.

**Step 4 — nav + footer.** Sticky nav (Features / Pricing / FAQ / Start free), footer links resolve.

**Step 4b — the category claim (D25).** Add the differentiation line to the hero or the section
directly below it: MiHomes is the only *owner-side, multi-home, team-aware, AI-first* option.
Consumer home apps are single-home with no concept of a team; landlord suites are tenant-side and
per-door priced; spreadsheets do not prioritize. Verify: the rendered page names the contrast, not
just the category.

**Step 5 — legal pages.** Templates and routes. **Content blocked on O6** — ship the routes with
drafted-not-final content behind the founder's review, or hold the step.

**Step 6 — OAuth stub removal.** Delete `oauth.py` and its routes, or redirect them to
`app.mihomes.ai/login`. Verify: no path creates a waitlist row from an OAuth callback.

**Step 7 — demo CTA.** Wire the booking link (O5). If a form is chosen instead, rate-limit it (D10).

**Step 8 — pre-launch checklist.**
- [ ] **O6** — ToS + Privacy published, counsel-reviewed, footer links resolve
- [ ] **O1** — prices ratified or D14 confirmed to still hold
- [ ] **O2** — Stripe live, or Pro/Estate remain "Talk to us"
- [ ] **O5** — booking link live and calendar connected
- [ ] `app.mihomes.ai` serves the product over HTTPS (D13 activated)
- [ ] Cross-origin CTA links verified from apex → app
- [ ] **O3** — decide what existing waitlist rows are owed

---

## 7. Non-goals

**N1 — Do not merge the marketing app into `mihomes.web`.** §2.2. The test asserting isolation
stays.

**N2 — Do not delete the `waitlist` table, rows, or `confirmed_at`.** D19. It is the funnel
baseline and `SPEC-001` §7 DEFERRED explicitly forbids dropping rows on conversion.

**N3 — Do not keep the Phase 0 OAuth stub alongside real auth.** Two Google flows with different
outcomes — one makes a session, one makes a waitlist row — is a bug waiting to happen. Remove or
redirect (Step 6).

**N4 — Do not hardcode dollar amounts.** D14, unchanged since SPEC-001 N6. Verified still
`PLACEHOLDER` on 2026-09-17.

**N5 — Do not add a JS framework, analytics SDK, or web fonts.** SPEC-001 N5 unchanged.
`GTM:55` LCP budget.

**N6 — Do not fabricate social proof.** D23. No logos, no stars, no "Join 300+" unless the number
is real.

**N7 — Do not claim security certifications the product does not hold.** D24. No SOC 2, no
"bank-grade encryption", no uptime SLA. MFA is still open (`SPEC-010` U4).

**N8 — Do not build a scheduler, a CMS, or a blog.** D21. Content marketing (`GTM:318`) is a
channel decision, not this spec.

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | No rendered marketing page contains a dollar amount | `test_landing_page.py::test_no_prices_rendered` (extend to `/pricing`) |
| A2 | The product app is still unreachable from the marketing app | `test_landing_app.py::test_no_single_user_router_is_mounted` (must keep passing) |
| A3 | Every trial CTA resolves to the product's signup URL | `test_landing_page.py::test_trial_ctas_point_to_app` |
| A4 | No CTA except the demoted fallback resolves to `/waitlist` | `test_landing_page.py::test_waitlist_is_not_primary_cta` |
| A5 | `/pricing` renders with the database unreachable | `test_pricing_page.py::test_renders_without_db` |
| A6 | `/pricing` shows all three tiers and the feature matrix | `test_pricing_page.py::test_tiers_present` |
| A7 | Footer legal links resolve (no 404) | `test_landing_page.py::test_footer_links_resolve` |
| A8 | No OAuth callback creates a waitlist row | `test_oauth_removed.py::test_no_oauth_waitlist_path` |
| A9 | Waitlist signup and confirm still work | existing `test_waitlist_routes.py` (must keep passing) |
| A10 | No fabricated social proof in the rendered HTML | `test_landing_page.py::test_no_testimonials_without_source` |
| A11 | No security-certification claims in the rendered HTML | `test_landing_page.py::test_no_unbacked_security_claims` |
| A12 | The demo CTA is present and resolves | `test_landing_page.py::test_demo_cta_present` |
| A13 | The page states the owner-side / multi-home / team-aware differentiator | `test_landing_page.py::test_category_claim_present` |
| A14 | `/pricing` states the real plan limits from `entitlements/limits.py` | `test_pricing_page.py::test_limits_match_entitlements` |

> A14 pins the page to the code rather than to a copy-edit: if `PLAN_LIMITS` changes, the test
> fails instead of the page quietly going stale.

> A11 is a grep-level docs test on purpose, matching the A18 precedent in SPEC-001: a compliance
> claim is cheap to add in a copy edit and expensive to have shipped.

---

## 9. Prerequisite — branch state

**This spec targets `worktree-spec-build-harness` and only that branch.**

`src/mihomes/landing/`, the auth stack, the Tailwind build, and `docs/specs/` exist **only** there
and on `origin/worktree-spec-build-harness`. The current working branch `telegram-bot` has none of
them. Building against any other branch means the premise does not hold.

**Durability risk.** Per `CLAUDE.md`'s worktree golden rule — "nothing is deleted until the primary
branch is pushed" — this worktree holds substantial unmerged work. Confirm the merge and push path
for the harness branch **before** starting SPEC-011 work, not after.
