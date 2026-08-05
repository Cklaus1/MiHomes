# MiHomes — Pricing & Packaging

Purpose: Define the plans, feature packaging, entitlement limits, and upgrade/downgrade behavior for MiHomes as it becomes a multi-tenant SaaS at mihomes.ai.

Status: Draft — 2026-07-27

> Every dollar figure and quantity marked **PLACEHOLDER** is a starting point to be validated via pricing research and early-customer conversations before GA. The plan *names* (Free / Pro / Estate), roles (owner / admin / staff), and the freemium *line* (household free, business paid) are canon and should not change.

---

## 1. Plan comparison

Three plans. **Free** for a single household, free forever. **Pro** for multi-home families and owners with a little help. **Estate** for family offices and estate managers running a team across several properties.

| | **Free** | **Pro** | **Estate** |
|---|---|---|---|
| **Positioning** | Run your own home | Run it like a business | Run an estate with a team |
| **Price — monthly** | $0 forever | **$20/mo** *(PLACEHOLDER — validate)* | **$60/mo** *(PLACEHOLDER — validate)* |
| **Price — annual** | $0 | **$200/yr** (~2 mo free) *(PLACEHOLDER)* | **$600/yr** (~2 mo free) *(PLACEHOLDER)* |
| **Homes** | **1** | up to **5** *(PLACEHOLDER)* | **Unlimited** *(fair-use, see §3)* |
| **Seats** | **3** | **10** *(PLACEHOLDER)* | **50** *(PLACEHOLDER, fair-use)* |
| **Staff invites** | No | Yes | Yes |
| **Roles available** | owner, admin | owner, admin, staff | owner, admin, staff |
| **AI advisor** | Limited (see §3) | Full SPACE advisor | Priority AI (faster, higher limits) |
| **AI usage / mo** | **200 calls** *(PLACEHOLDER)* | **3,000 calls** *(PLACEHOLDER)* | **15,000 calls** *(PLACEHOLDER, fair-use)* |
| **Core management** | Properties, tasks, issues, vendors, inventory, documents, work orders | Everything in Free | Everything in Pro |
| **Advanced features** | — | Vendor ratings, work-order scheduling | + Predictive maintenance, weekly AI reports, audit export, priority support |
| **Support** | Community / docs | Email (best-effort) | Priority email + onboarding help |
| **Data export** | Manual export | Manual export | Manual export + scheduled audit export |

Feature-availability detail:

| Capability | Free | Pro | Estate |
|---|:--:|:--:|:--:|
| Multi-home dashboard | 1 home | ✅ | ✅ |
| External staff invites | ❌ | ✅ | ✅ |
| Admin role (delegate non-billing management) | ✅ | ✅ | ✅ |
| AI estate manager (SPACE prioritization) | Limited | ✅ | ✅ priority |
| Vendor ratings & history | ❌ | ✅ | ✅ |
| Predictive maintenance | ❌ | ❌ | ✅ *(PLACEHOLDER scope)* |
| Weekly AI report digest | ❌ | ❌ | ✅ |
| Audit / compliance export | ❌ | ❌ | ✅ |
| Priority support & onboarding | ❌ | ❌ | ✅ |

---

## 2. Freemium philosophy

**The free line is a household. The paid line is a business.**

Free forever means one home run by the people who live in it: an owner, a partner, and one more seat (a kid, a parent, a co-owner). That is **1 home, 3 seats**. A family can genuinely run their own home on MiHomes and never pay — and that is the point. It builds trust, seeds word of mouth, and makes the product a default habit before money is ever discussed.

You cross into paid the moment you start **running it like a business**. Three concrete moments trigger the Free → Pro gate:

| User moment | What they're trying to do | Gate |
|---|---|---|
| "I want my housekeeper to log tasks." | Invite **external staff** | Staff role requires Pro |
| "We just bought/manage a second place." | Add a **2nd home** | >1 home requires Pro |
| "My daughter and her partner both need access." | Add a **4th seat** | >3 seats requires Pro |

Each of these is a signal that the household has become an *operation* — staff, multiple assets, or a larger circle of people coordinating. That is exactly where the value (and the willingness to pay) shows up, and exactly where our costs begin to rise. The gate feels fair because it maps to the user's own sense that "this is more than just my house now."

Design implication: Free must be a *complete* experience for one home, not a crippled demo. Nothing that a single household needs day-to-day should be paywalled. The paywall lives only on the three "business" edges above, plus AI volume.

---

## 3. Entitlements & limits

Every gated action checks a single source of truth: the **entitlements service**. Given an account's current plan (and billing status), it answers "is this action allowed, and what is the limit?" The app never hardcodes plan logic at the call site — it asks the entitlements service.

See `../architecture/MULTITENANCY.md` for the account/tenant model and `../architecture/BILLING_AND_EMAIL.md` for how Stripe subscription state maps to plan status. This doc defines the *limits*; those docs define the *plumbing*. Not duplicated here.

### 3.1 Machine-enforceable limits

These are the entitlement keys the service resolves per account. All numeric values except Free's `1 home / 3 seats` are **PLACEHOLDER** and tunable without code changes (config-driven).

| Entitlement key | Type | Free | Pro | Estate |
|---|---|---|---|---|
| `max_homes` | int | **1** | 5 | `unlimited` (fair-use) |
| `max_seats` | int | **3** | 10 | 50 (fair-use) |
| `staff_invites_allowed` | bool | `false` | `true` | `true` |
| `roles_allowed` | set | owner, admin | owner, admin, staff | owner, admin, staff |
| `ai_calls_per_month` | int | 200 | 3,000 | 15,000 |
| `ai_overage_buffer_pct` | int (%) | **0** | 20 | 20 |
| `ai_priority` | enum | `standard` | `standard` | `priority` |
| `vendor_ratings` | bool | `false` | `true` | `true` |
| `work_order_scheduling` | bool | `false` | `true` | `true` |
| ↳ *scope of that key* | — | Covers **exactly one capability: setting `WorkOrder.due_date`.** Not `assignee_id` (no UI exposes it), and **not** `Appointment`/`/calendar` — that is a different product wearing the same word, and gating it would paywall the whole calendar plus a nightly automated job. | | |
| `predictive_maintenance` | bool | `false` | `false` | `true` |
| `weekly_ai_report` | bool | `false` | `false` | `true` |
| `audit_export` | bool | `false` | `false` | `true` |
| `support_tier` | enum | `community` | `email` | `priority` |

Notes:
- `unlimited` / fair-use values are enforced as a high soft ceiling with an internal alert, not literal infinity, to protect against abuse and runaway AI cost. The ceiling is itself a config value (e.g. `max_homes = 100` internally for Estate — *PLACEHOLDER*, see Open Question #7), never a magic number in code.
- Every home counts against `max_homes` regardless of who created it. Read-only (frozen) homes still count — the limit gates *having* homes, not writing to them.

**Seat semantics (unambiguous):**
- A **seat** = one `active` row in `memberships` **plus** one pending row in `invites` — **two tables, not one status enum.** `memberships.status` is only `active` or `revoked`; there is no `invited` status, because an invitee may have no `users` row yet and a membership requires one. `revoked` memberships and expired/revoked invites do **not** count.
- The **owner counts** as a seat. Free's 3 seats = owner + 2 others.
- **Pending invites consume a seat** from the moment the invite is created (so we never email an invite that can't be honored — see `ONBOARDING_AUTH_RBAC.md` §6.4). Revoking or letting an invite expire frees the seat immediately.
- Seats are counted **per account**. A staff member working in two accounts consumes one seat in *each* account (they are two memberships). Cross-account seat sharing is Open Question #5 in `ONBOARDING_AUTH_RBAC.md`.
- Roles are irrelevant to seat counting: owner, admin, and staff each consume exactly one seat.

### 3.2 The entitlements service contract

The service exposes two questions to the rest of the app:

- `can(account, action, context) -> Allowed | Denied(reason, upgrade_target)` — for boolean/limit gates (add home, invite staff, add seat, use predictive maintenance).
- `usage(account, meter) -> {used, limit, resets_at}` — for metered resources (AI calls). See §5.

Rules of the contract:
1. **One source of truth.** Plan → entitlements mapping lives in one config module, not scattered across features.
2. **Fail closed on paid features; fail open on read *when billing status is indeterminate*.** If we cannot tell whether an account is paid — a webhook is late, a reconciliation is mid-flight — deny *new* paid actions but never block a user from reading their own data. **This clause is about uncertainty, not about reads in general:** a plan whose row in §3.1 is `false` may legitimately gate a read (see rule 5).
3. **Billing status is an input.** `active`, `trialing`, `past_due`, `unpaid`, `canceled` change what the same plan is allowed to do (see §4). Status→behavior mapping is canon in `../architecture/BILLING_AND_EMAIL.md` §5: `trialing`/`active` = full plan entitlements; `past_due` = full entitlements during grace; `unpaid` = restricted (§4.3); `canceled`/`incomplete` = Free entitlements.
4. **Deny returns an upgrade target.** Every `Denied` names the plan that would allow it, so the UI can render the right upgrade prompt.
5. **Checks fire on state creation — and on a declared read — not just in the UI.** `can()` is called at invite creation, home creation, seat activation, and AI dispatch, server-side and transactionally, so races (two concurrent invites at the seat cap) cannot exceed a limit. **This list is not exhaustive: a `false` row in §3.1 may also gate a *read*.** `vendor_ratings` is the current example — ratings have no write path, so the only thing enforceable is viewing them. A read gate must still let the page load: gate at context assembly, never by 403-ing a route that renders other things too. UI gating is a courtesy; the service check is the enforcement.

---

## 4. Upgrade & downgrade flows

### 4.1 Upgrade (Free → Pro / Pro → Estate)

**Recommended: soft gate on discovery, hard gate on the action.** The user can always *see* that a locked action exists (the "Invite staff" or "Add home" button is visible), but clicking it opens an upgrade prompt instead of performing the action. This teaches the value ("Pro lets you do this") at the exact moment of intent, which converts far better than hiding the feature entirely.

| Trigger | Behavior |
|---|---|
| Free user clicks "Add a 2nd home" | Modal: "Adding another home is a Pro feature. Start your 14-day Pro trial." → one-click trial start (no card, §4.2); Stripe Checkout only when converting to paid |
| Free user clicks "Invite staff" | Modal: staff invites need Pro; same trial CTA |
| Free user adds a 4th seat | Same pattern |
| Pro user hits 6th home / Estate-only feature | Upgrade to Estate prompt |

The three Free→Pro triggers are canon: **2nd home, 4th seat, or inviting staff**.

Upgrades take effect **immediately** on successful payment/trial start; entitlements re-resolve on the next `can()` call.

### 4.2 Trial

**Recommendation: 14-day Pro trial *(PLACEHOLDER length)*, no credit card, started on first gated action (not automatically at signup).**

Rationale: MiHomes' value compounds over days of real use (tasks logged, AI advice acted on), so a time-boxed trial showcases it better than a feature demo. No card removes friction for the household audience, who are consumers, not procurement buyers, and maximizes top-of-funnel. Starting on first *gated action* (rather than at signup) means the trial clock runs while the user actually needs Pro — a trial that starts at signup is often burned before the 2nd home or first staff hire appears. We accept higher trial-to-nothing drop-off in exchange for volume and goodwill; the Free tier catches everyone who doesn't convert (they simply revert to Free; §4.3 restricted policy applies to any surplus homes/seats/staff created during trial). Card-required would raise conversion *rate* but shrink the funnel and clash with the "free forever household" brand promise. Revisit if trial abuse or AI cost during trials becomes material.

Mechanics (must match `../architecture/BILLING_AND_EMAIL.md`): with no card there is **no Stripe subscription during the trial** — the trial is app-managed state (`plan=pro`, `subscription_status=trialing`, `trial_ends_at`), and the entitlements service treats it as `trialing`. Stripe objects are created only at conversion (Checkout). Consequence: the `trial_ending` email is triggered by **our scheduler**, not Stripe's trial-will-end webhook, until/unless we move to card-required Stripe-native trials. One trial per account, ever (flag `trial_used_at` on the account) to bound abuse.

### 4.3 Downgrade & past-due (over the new limit)

When an account drops to a lower plan — voluntary downgrade, canceled subscription, or `past_due` after failed payment — it may exceed the new plan's limits (e.g., 4 homes on a plan that allows 1). Policy is **humane and non-destructive**: we never delete data for a billing lapse.

Sequence (past-due path; a **voluntary downgrade skips Grace** — the user chose it, so Restricted applies at the moment the lower plan takes effect):

| Stage | Timing | Behavior |
|---|---|---|
| **Grace** | Days 0–14 *(PLACEHOLDER — align with Stripe Smart Retries dunning window)* while `past_due` | Full access retained. Banner: "Update payment to keep Pro features." Stripe dunning runs. Maps to Stripe `past_due` (see `../architecture/BILLING_AND_EMAIL.md` §5). |
| **Restricted** | After grace (Stripe `unpaid`), or immediately on voluntary downgrade / cancel-at-period-end | Account moves to **read-only on the over-limit surplus**. Everything within the new plan's limits stays fully usable. |
| **Reactivation** | Any time | Paying again instantly restores full access — nothing was deleted. |
| **Trial expiry** | Day 0 | A no-card trial ends by **our scheduler**, not a Stripe webhook (§4.2). The account reverts to Free and lands over-limit exactly as a voluntary downgrade would. Show the home-picker **~3 days before expiry**, alongside the `trial_ending` email, so the choice is made before access changes rather than after. No Grace period — nothing was owed. |
| **Arrived over-limit (import)** | At import | An account can reach an over-limit state without ever downgrading: `mihomes import` of a multi-property archive into a Free account. The importer **asserts `can()` per home and refuses an over-limit import** rather than creating an account this table would then have to rescue. Named here because "how did this account get over its limit" has three answers, not two. |

Restricted, precisely:

- **Homes.** The owner **chooses which home(s) stay active** (up to the new `max_homes`) via an in-app picker shown from day 0 of Grace. If no choice is made by the time Restricted starts, default = keep the **oldest-created home** active, freeze the rest (newest first). Frozen homes are **read-only, never deleted**: view and export yes; create/edit/complete/AI-advise no.
- **Seats.** Surplus memberships (beyond the new `max_seats`) are **not removed**; they flip to read-only account-wide until the account is back under limit or upgraded. Pending invites over the new limit are **auto-revoked** (the invitee sees "invite no longer valid").
- **Staff on Free.** Free disallows the staff role entirely, so after a drop to Free *all* staff memberships go read-only (they can still view their scoped homes and their task history, so nothing silently vanishes for a housekeeper mid-week). Re-upgrading reactivates them in place.
- **AI.** Frozen homes get no AI; active homes get the new plan's `ai_calls_per_month`.
- **Owner choice is mutable.** The owner can swap which home is active at most once per billing cycle *(PLACEHOLDER cadence)* to prevent freeze/thaw gaming.

### 4.4 Cancellation & data retention

| Event | Behavior |
|---|---|
| Cancel subscription | Account reverts to **Free** at period end (not instantly) — 1 active home (owner's choice, §4.3), 3 seats. |
| Over Free limits after cancel | §4.3 restricted policy applies to the surplus, with **no additional grace** (period-end was the notice). |
| Full account deletion (user-requested) | Data retained **30 days** *(PLACEHOLDER)* then hard-deleted; export offered first. |
| Involuntary (never pays, long dormant) | Data retained per retention policy; export always available while account exists. |

---

## 5. Metering (AI usage)

AI is the primary variable cost (Claude API tokens). Everything else is roughly fixed per account, so **AI usage is the only metered resource** at launch.

### 5.1 What we meter

- **Unit surfaced to users:** *AI calls per month* (one advisor request = one call). Simple and predictable for a non-technical audience. **What counts as one call:** any user-initiated AI request — an advisor question in web, WhatsApp, or Telegram; an AI-drafted message; an on-demand prioritization run. **What does not count:** system-initiated AI (the Estate weekly report digest, internal classification/routing) — those are plan features, budgeted internally, not drawn from the user's meter. A multi-turn conversation counts one call per user turn.
- **Unit tracked internally:** *tokens* (input + output) per call, per account, so we can see true cost and re-tune the call-limit → token-cost relationship without changing the user-facing number.
- Each metered event records: account, plan, feature, tokens_in, tokens_out, timestamp. Aggregated to `usage(account, "ai_calls")`.
- Meter **resets monthly** aligned to the billing cycle anchor (or calendar month for Free).

### 5.2 How limits are surfaced

- Usage meter visible in-app: "1,240 / 3,000 AI actions this month."
- Nudges at **80%** and **100%** of the limit.
- The `usage()` contract (§3.2) returns `{used, limit, resets_at}` so any surface can render the meter.

### 5.3 Overage policy — recommendation

**Recommended: soft cap + upgrade nudge (not a hard wall), with an internal hard ceiling for abuse.**

| Level | Behavior |
|---|---|
| Under limit | Normal. |
| At 80% | In-app nudge (once per cycle): "You're using MiHomes' AI a lot — here's what more you'd get on the next plan." |
| At 100% (soft cap) | AI keeps working through the plan's `ai_overage_buffer_pct` buffer (**+20%** for Pro/Estate, **0%** for Free — *(PLACEHOLDER)*; see §3.1), with a persistent upgrade banner. |
| Beyond buffer (hard ceiling = `ai_calls_per_month × (1 + ai_overage_buffer_pct/100)`) | Every AI request is **denied by the entitlements check before the Claude call is made** — the user sees "AI paused until <resets_at> — upgrade to continue," with their usage meter and reset date. All non-AI features remain fully usable. Chat-gateway (WhatsApp/Telegram) AI requests get the same message as a reply. |

Enforcement mechanics: the AI dispatch path calls `usage(account, "ai_calls")` and compares against the hard ceiling *before* invoking the provider; the metered event is recorded on dispatch (attempted calls past the ceiling are rejected, not recorded). Counting is per **account**, not per user — a 10-seat Pro account shares one 3,000-call pool. `resets_at` = billing-cycle anchor (Pro/Estate) or first of the calendar month UTC (Free).

Rationale: a hard wall at exactly the limit punishes the most engaged users (our best upgrade candidates) and creates a bad moment. A soft cap keeps them happy while making the value of upgrading concrete. The hard ceiling exists purely to bound worst-case Claude spend. Free's buffer is **0%** — a hard stop at 200 — since there's no revenue to offset cost.

---

## 6. Pricing rationale & competitive context

> Honest caveat: exact prices below are **PLACEHOLDER** and must be validated. This section names *category comparanda* to anchor expectations — it does **not** assert specific competitor prices, which need real research before we quote them.

Where MiHomes sits relative to adjacent categories:

| Category | Who / what | Relevance to MiHomes pricing |
|---|---|---|
| Household / family organizers | Shared to-do, chore, and family-calendar apps | Sets the *consumer* price ceiling — households expect low/$0. Justifies Free tier and ~$20 Pro anchor. |
| Property management SaaS | Landlord/rental management tools (per-unit or per-door pricing) | Business tools priced per property/unit; Estate's per-home value maps here, but MiHomes is owner-side, not tenant-side. |
| Home maintenance / home-management apps | Maintenance-tracking and home-inventory apps | Direct feature overlap (maintenance, inventory, documents); typically low consumer subscriptions. |
| Estate / family-office software | Tools for family offices and estate/household staff management | Anchors the *high* end; these buyers pay materially more, supporting Estate at $60+ and future higher tiers. |

Pricing logic:
- **Free** exists to win the household and build habit; its cost is bounded by tight AI limits (§5).
- **Pro at ~$20/mo** sits at a familiar consumer-subscription price, below the pain threshold for a family already spending on their home, and above our per-account AI cost with headroom.
- **Estate at ~$60/mo** targets buyers who already pay for staff and services; it is priced on *value* (running a team across properties), not cost. There is likely room above this for a future custom/enterprise tier.
- **Annual ~2 months free** trades a discount for cash upfront and lower churn; validate the exact discount against take-rate.

All of the above requires: (1) willingness-to-pay interviews with the three personas, (2) a Claude-cost model per active account, and (3) A/B on the Pro price point post-launch.

---

## 7. Open questions

| # | Question | Notes / leaning |
|---|---|---|
| 1 | **Annual vs monthly mix & discount** | Leaning ~2 months free on annual; validate discount depth against conversion and churn. |
| 2 | **Trial with or without card** | Leaning no-card 14-day Pro trial (§4.2); revisit if trial AI cost or abuse is high. |
| 3 | **Per-seat / per-home add-ons** | Should Pro sell extra homes or seats à la carte instead of forcing Estate? Cleaner tiers vs. more revenue capture. Decide before GA. |
| 4 | **AI overage as a paid add-on** | Offer a metered AI top-up pack vs. only upgrade path? Depends on Claude cost model. |
| 5 | **Nonprofit / edu pricing** | Likely low priority for this audience; hold unless demand appears. |
| 6 | **Founder / early-bird discount for waitlist** | Recommended: lifetime or first-year discount for Phase 0 waitlist to reward early adopters and seed testimonials. Define the exact offer before landing page launch. |
| 7 | **Estate "unlimited" fair-use threshold** | Set the internal soft ceiling and alerting numbers before Estate goes live. |
| 8 | **Currency & regional pricing** | USD only at launch; localized pricing is post-GA. |

---

## Phasing note

The **entitlements service** ships in **Phase 2**, config-only — it exists and is called, but every account is Free and every limit reads "unlimited". The **gates and the billing UI** ship in **Phase 3**, which wires billing status in as an *input* to the same service. The **limits in §3 are defined now** so Phase 2 has real keys to declare and Phase 3 has real numbers to enforce. Phase 0 (landing + waitlist) should reflect the three-plan structure and the founder-discount decision (Open Question #6). GA is Phase 4.
