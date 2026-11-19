# SPEC-004 — Phase 3: Billing / Freemium

**Phase:** 3 (canon — `../product/SAAS_PRD.md` §10)
**Status:** Ready to build — **1 open decision** (O1: launch prices/limits, blocks config only)
**Written:** 2026-08-04
**Source PRDs:** `../architecture/BILLING_AND_EMAIL.md` (primary — provider interfaces, webhook rules, Stripe mapping), `../product/PRICING_AND_PACKAGING.md` §3–§5 (plans, entitlement keys, trial, downgrade, metering), `../product/SAAS_PRD.md` §8.6/§10
**Depends on:** SPEC-002 (Phase 1) — the Stripe columns on `accounts` (§4.2), RLS, the `TenantOwned` mixin, the scoped session, and D9/D12/D13's Postgres baseline. SPEC-003 (Phase 2) — `can()`/`usage()`, `require_permission`, the limits config module. SPEC-001 (Phase 0) — the whole `services/email/` package.

**Goal.** Make the plans real. Wire Stripe in as an *input* to `can()`, build the AI usage meter
that makes `usage()` mean something, and turn the entitlement keys SPEC-003 declared into gates
that actually deny.

**Exit criteria** (`SAAS_PRD:180`): **a Free user can upgrade to Pro and gates flip via webhook.**
Phase 3 is the **MVP cut line** (`SAAS_PRD:187`) — this is the last phase before the product can
be sold.

**The stake.** Phase 1 defended the boundary between customers; Phase 2 defended the boundary
inside one. Phase 3 defends the boundary between *what was paid for and what was not* — and it is
the first phase where a defect costs money in both directions. A gate that fails open gives away
the product. A meter that fails open hands an unbounded Claude bill to whoever is paying for
inference: a Free account capped at 200 AI calls that can actually make unlimited ones is not a
pricing bug, it is an uncapped liability. Both failure modes look exactly like the feature working.

---

## 0. Three things a reader must know before trusting this spec

**0.1 — Phases 0, 1 and 2 are all unbuilt.** SPEC-003 §0.1 recorded that it sat on an unbuilt
Phase 1. This spec sits on three unbuilt phases, and the forward reference is correspondingly
longer. Verified on `telegram-bot`, clean tree, 2026-08-04:

- Zero hits in `src/` for `stripe`, `BillingProvider`, `EmailProvider`, `resend`, `entitlement`,
  `max_homes`, `def can(`. No `User`, `Account` or `Tenant` model. **No auth layer at all.**
- `src/mihomes/services/email/` does not exist — SPEC-001 §3 creates it.
- `config.py:14` still hardcodes `DB_URL = f"sqlite:///{DB_PATH}"`, not env-overridable, and no
  Postgres driver is installed on any branch.

Consequences a reader must carry:

- Every reference here to `can()`, `Account.plan`, `subscription_status`, `require_permission`,
  `TenantOwned`, or `EmailService` describes **a spec, not code**.
- **Divergence compounds across three specs.** If SPEC-002's or SPEC-003's implementation departs
  from its spec, this document inherits both departures. Re-verify §4 and §5 against the tree
  before building.
- **Postgres is assumed, not observed.** §4's DDL, and the RLS carve-out in particular, assume
  SPEC-002 **D9** (Postgres-only Alembic baseline), **D12** (docker-compose Postgres for dev and
  CI) and **D13** (managed Postgres) have landed. They have not. The idempotency pattern in Step 5
  is nonetheless written to be correct on SQLite *and* Postgres, because a dialect branch in the
  webhook path is a worse liability than a slightly conservative insert.

**0.2 — `O1` here is not SPEC-002's `O1` and not SPEC-003's `O1`.** Label namespaces are
per-spec-local (`README.md` §"Working on a spec"). SPEC-002's O1/O2 **closed** on 2026-07-31
(→ D13/D14). SPEC-003's O1 — at-rest encryption of provider API keys — **is still open** and is
carried into §10 of this document unchanged. This spec's own O1 is a different question entirely
(launch prices). A reader who resolves "O1" against the wrong spec will conclude a live gate is
settled.

**0.3 — What this phase inherits from SPEC-003 §10.** Four items were declared not-made-safe
there and none are addressed here: mis-declared action keys, secrets at rest (SPEC-003 O1), the
Telegram bot's transport, and aggregate inference. §10 restates them plus this phase's own.

---

## 1. Decisions

### 1.1 Locked — inherited or doc-derivable

| # | Decision | Source |
|---|---|---|
| D1 | **Webhooks are the source of truth.** Entitlements change on a verified webhook, never on the `success_url` redirect | `BILLING` §6–§7. The redirect is a UI affordance the user can forge, replay, or never reach |
| D2 | The provider adapter is **stateless and DB-free** | `BILLING` §4.1 — `NormalizedEvent` deliberately carries *provider* ids, not an `account_id`. Mapping `provider_customer_id → account` is `BillingService`'s job |
| D3 | A vendor price id **never appears in the interface and never arrives from the client** | `BILLING` §4.1 — the plan→price mapping is provider-internal config (§9). A client-supplied price id is a self-service discount |
| D4 | Free accounts have **no Stripe subscription object** | `BILLING` §5 — "*(no subscription row)* → Free, the default state of every account". Stripe objects are created at conversion only |
| D5 | Billing status is an **input** to `can()`, not a parallel gate | `PRICING` §3.2 rule 3, with the status→behaviour mapping canon in `BILLING` §5 |
| D6 | Every `Denied` names an **upgrade target** | `PRICING` §3.2 rule 4. Already asserted by SPEC-003 A25 |
| D7 | Checks fire **server-side, transactionally, on state creation** — UI gating is a courtesy | `PRICING` §3.2 rule 5 |
| D8 | Billing management is **owner-only** | `ONBOARDING` §9.2 via SPEC-003's matrix — admins manage the estate, not the card |
| D9 | Downgrade is **humane and non-destructive**: surplus homes/seats go read-only; the core home stays fully usable; **nothing is deleted** | `PRICING` §4.3, restated in `BILLING` §5's `unpaid` row |
| D10 | `past_due` keeps **full access** during grace (0–14 days); `unpaid` restricts | `BILLING` §5; `PRICING` §4.3 |
| D11 | AI calls are the **only** metered resource; **system-initiated calls do not count** | `PRICING` §5.1–§5.2 |

### 1.2 Locked — founder decisions, 2026-08-04

| # | Decision | Rationale |
|---|---|---|
| **D12** | **`vendor_ratings` and `work_order_scheduling` are enforced exactly as `PRICING` §3.1 writes them** — `false` on Free, `true` on Pro and Estate | Founder decision, 2026-08-04. **This closes P3-a** (SPEC-003 §1.4) and **supersedes SPEC-003 N8**, which forbade enforcing either. The reasoning N8 gave — that enforcement would "delete working functionality from every user" — does not survive contact: there are no hosted users to grandfather (§0.1: no `Account`, no auth layer), so this is a pricing question, and the PRD already answered it. See D13 for the scope this required |
| **D13** | **`work_order_scheduling` covers exactly one capability: setting `WorkOrder.due_date`.** Not `assignee_id`, not `Appointment`/`/calendar` | The key named a feature that does not exist (F5), so enforcing it required defining it first. `PRICING:27` sells "work-**order** scheduling" and `due_date` is the only candidate that literally is that. `assignee_id` is not coherently gateable — the web UI exposes no assignee field at all, so a gate would fire only on the CLI, which SPEC-002 D1 makes an operator tool. `Appointment`/`/calendar` is a **different product** wearing the same word: gating it would paywall the entire calendar, the Telegram bot's appointment creation, and `services/recurring.py:158`'s automated scheduler — far more than `PRICING:27` sells, and it would break a nightly job rather than show an upgrade prompt. Doc-fix **B4** writes this scope into `PRICING` so the key stops naming something undefined |
| **D14** | **`vendor_ratings` is a declared *read* gate — an explicit exception to `PRICING` §3.2 rule 5's write-only list** | Ratings have **no write path today**: `create_rating` has zero callers (F6). So the only thing enforceable is reading, and §3.2 does not cleanly authorise that: rule 5 enumerates where `can()` fires as "invite creation, home creation, seat activation, and AI dispatch" — all writes — and rule 2 says "never block a user from reading their own data". Ratings *are* the user's own data. Enforcing D12 therefore requires stating the exception rather than leaving SPEC-004 in silent conflict with SPEC-003 §5. Doc-fix **B5** amends §3.2 to admit read gates as a category and scopes rule 2's read clause to *indeterminate billing status*, which is what it was written for |
| **D15** | **Scheduled work is a plain idempotent CLI command.** The trigger is a deployment choice: **Fly scheduled machine** by default, a dedicated always-on machine as the named alternative | `MULTITENANCY` §11.4 deferred this to Phase 3 (F1). Both jobs are low-frequency batch work, so a permanently awake machine bills 24h/day for minutes of work and defeats scale-to-zero. **The load-bearing half is the interface, not the trigger:** a command that is safe to run twice is testable with no scheduler present and swappable if the platform's capability differs from what is assumed here. Fly's current scheduled-task mechanism has **not** been verified against their documentation — `README.md:154` applies to infra claims as much as code claims, so it is a default with an alternative, not an asserted fact |
| **D16** | **The importer asserts `can()` per home and refuses to create an over-limit account** | `PRD_REVIEW` B1 asked what plan a founder/local install gets. SPEC-002 D1 dissolved the "local install" half by dropping SQLite mode, but **D10 kept the importer** and states the archive "imports later into its own account" — and that archive is multi-property (F2). Phase 1 has no gates, so importing 5 properties succeeds; Phase 3 turns on `max_homes` and the account is retroactively over-limit — a state `PRICING` §4.3 never describes (it covers past-due and voluntary downgrade; `PRD_REVIEW` B2 adds trial expiry; none cover *arrived* over-limit). Asserting at import time is chosen over provisioning the founder's account as Estate because it generalises: any future customer migrating in hits the same path. Doc-fix **B6** records the third downgrade row |
| **D17** | **The AI meter enforces at `get_provider()`, and `agent_stream` is refactored to route through it** | `agent.py:79` currently constructs `anthropic.Anthropic()` directly, bypassing the factory entirely (F8). Metering the factory alone would leave the highest-token path in the app uncapped while every test passed. The bypass is closed **before** the meter is built (Step 9), not after |
| **D18** | **Usage counters are materialized rows, never derived from `ai_conversations`** | `archive.py:120` DELETEs `ai_conversations` rows (F10), so a derived count silently resets a customer's usage when they archive. The table is not a complete request log either — only 5 of ~12 AI paths write to it (F9) |

### 1.3 `OPEN — needs decision: founder`

| # | Question | Why it cannot be defaulted | What it blocks |
|---|---|---|---|
| **O1** | The **actual prices and limits**: Pro/Estate monthly + annual amounts, `max_homes`, `max_seats`, `ai_calls_per_month` per tier | Every figure in `PRICING` except Free's 1 home / 3 seats is tagged `PLACEHOLDER` (~20 values). These are revenue decisions, not engineering ones, and a placeholder shipped to a live Stripe Product is a real charge at the wrong price | **Launch configuration only, not the build.** Every step below targets config keys and `STRIPE_PRICE_*` env vars, never literals, so the code is complete and testable before the numbers exist. The gate is creating the Stripe Products and setting the env vars |

Everything else this phase depends on is settled.

### 1.4 How SPEC-003's forward-flagged items resolve

| Ref | SPEC-003's statement | Resolution here |
|---|---|---|
| **P3-a** | `vendor_ratings`/`work_order_scheduling` `false` on Free but "both features **ship today**" | **Closed by D12** — both enforced per the PRD. SPEC-003's factual claim was **half wrong**: ratings ship (F6), scheduling-as-named does not exist (F5). Enforcement therefore needed D13's scope definition and D14's read-gate exception. **SPEC-003 N8 is superseded** |
| **P3-b** | `ai_calls_per_month` unenforceable — no meter exists | **Closed by Steps 8–10.** `usage()` keeps SPEC-003 §5's exact signature and stops returning the declared-only stub. Confirmed worse than SPEC-003 knew: `tokens_used` is dead in every row and the table is incomplete (F9), so there is **no history to backfill** — the meter starts from zero by necessity, not by choice |
| **P3-c** | `PRICING:250` ("Free tier, gates, billing UI ship in Phase 3") vs `SAAS_PRD:179`'s Phase 2 entitlements | **Already resolved** in SPEC-003's favour: service in Phase 2, gates in Phase 3. Doc-fix **B3** applies the `PRICING:250` edit that SPEC-003 recorded but did not land |

### 1.5 Survey findings that shaped this spec

Ten findings, all verified against the tree or the doc set on 2026-08-04. Negative results are
stated as negatives, per `README.md:154`.

| # | Finding | Consequence |
|---|---|---|
| **F1** | **No scheduler exists, and this is the first phase that needs one.** `MULTITENANCY` §11.4 names both jobs — the `trial_ending` scheduler and the daily reconciliation sweep — then defers: "*Decide when Phase 3 schedules land; it does not block Phase 1.*" Fly machines scale to zero, so a sleeping app runs no timers | D15. The entrypoint is not optional: it is a hard dependency of the trial state machine *and* of one of the four emails (F3) |
| **F2** | **`PRD_REVIEW` B1 half-survives.** SPEC-002 D1 drops local SQLite mode, so there is no local install to plan-assign. But D10 keeps `mihomes import <sqlite-path>` and the founder's archive is multi-property | D16. Phase 3 is where B1 bites, because Phase 1 has no gates to stop the over-limit import |
| **F3** | **The no-card trial has no Stripe object.** `PRICING:143`: "*with no card there is **no Stripe subscription during the trial*** … *the `trial_ending` email is triggered by **our scheduler**, not Stripe's trial-will-end webhook*" — plus "*One trial per account, ever*" (`trial_used_at`). `BILLING:485` confirms `trialing` only appears in card-first flows | Two trial paths must coexist. `customer.subscription.trial_will_end` (`BILLING` §6) fires only for a card-first trial we do not currently offer — the handler is written but is **not** the trigger for our trial. Creates Step 11's ordering constraint |
| **F4** | **Email phasing contradicts itself.** `SAAS_PRD:154` + `BILLING` §2.6 put receipt/`trial_ending`/payment_failed/cancellation in Phase 3; `SAAS_PRD:182/185/187` put "full email lifecycle, dunning" in Phase 4 | Doc-fix **B2**. All four ship here; Phase 4 ships lifecycle polish. **The rationale splits 3/1, not 4/0:** three are webhook-triggered, `trial_ending` ships here because it is the trial state machine's own output (F3) |
| **F5** | **`work_order_scheduling` names a feature that does not exist.** Zero scheduling functions in `services/work_order.py` or `models/work_order.py`. Three unrelated candidates carry the meaning: `WorkOrder.due_date` (`models/work_order.py:39`), `WorkOrder.assignee_id` (`:34`), and the separate `Appointment` model (`models/appointment.py:20`, `/calendar` mounted) | D13 picks `due_date`. Repo-wide sweep of `create_work_order(`/`update_work_order(`: **five callers, four take a due date** — CLI create `cli/work_order.py:51`, CLI edit `:180`, web create `routes/work_orders.py:65`, web edit `:144`. The fifth, Telegram `gateways/telegram/responder.py:601`, creates work orders but **passes no `due_date`**, so the gate never fires on a bot path |
| **F6** | **The ratings gate must target `services/vendor.py`, not `services/vendor_rating.py`.** `create_rating:16`, `get_vendor_scores:52` and `compare_vendors:75` have **zero callers each**. Every live path calls `vendor_svc.get_vendor_ratings` (`services/vendor.py:262`) from `cli/vendor.py:70,199` and `web/routes/vendors.py:56` | Gate **both** modules — the live one for effect, the dead one so a future caller inherits the gate. Ratings are **read-only today**, which is why D14 is needed. Six templates render ratings, two of which (`dashboard.html`, `property_detail.html`) a Free user must still be able to load |
| **F7** | **No AI agent tool exposes a gated feature.** All 15 tools in `services/ai/tools.py` are read-only `query_*` functions; `_query_vendors:603` returns name/category/contacts and **no rating or score**; no tool touches `Appointment` | Checked and empty. The gates need no AI-tool surface — stated so a later reader does not re-derive it |
| **F8** | **`agent_stream` bypasses the provider factory.** `services/ai/agent.py:79` calls `anthropic.Anthropic(api_key=api_key)` directly and streams at `:157`. It never touches `get_provider()`. Separately, `provider.stream()` is called at `agent.py:45` but is **not declared** on the `AIProvider` Protocol (`provider.py:29,39` declare only `complete` and `structured_output`), and `agent.py:42` assigns `provider.model = model` | D17. This is the leak §8's A-list is built around. The wrapper must proxy **undeclared methods and attribute writes**, or streaming either breaks or escapes metering |
| **F9** | **`ai_conversations.tokens_used` is dead — always NULL.** Zero `tokens_used=` assignments anywhere; `claude_provider.py:65-71` discards `response.usage` entirely. Only **5 of ~12** AI paths write a row at all — gateway reviews, assessors, resume ranking and weather tasks log nothing | The meter is fully greenfield with **no history to backfill**. Confirms P3-b and goes past it |
| **F10** | `services/archive.py:120` **DELETEs** `ai_conversations` rows when archiving. Also: **no AI provider instance is cached module-level** — all 12 factory sites assign to a function-local (the cached `_get_provider` in `calendar_sync.py:39`, `staff_pto.py:140`, `routes/calendar.py:113` is **Google Calendar**, unrelated) | D18 (materialized counters). The absence of caching makes construction-time wrapping equivalent to per-call metering — true today, so **A12 asserts it** rather than trusting it to stay true |

---

## 2. Doc-fix prerequisites

Contradictions this phase would otherwise inherit. Each is a real edit to a source doc, not a
note.

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `PRICING:143` says `billing_status`; `MULTITENANCY:80` and `BILLING:365` say `subscription_status` | Rename to **`subscription_status`** — SPEC-002 §4.2 is the schema of record. (`PRD_REVIEW` A3, still unfixed as of today) |
| **B2** | `SAAS_PRD:154` + `BILLING` §2.6 vs `SAAS_PRD:182/185/187` — the four billing emails are in both Phase 3 and Phase 4 | Phase 3 ships all four; Phase 4 ships lifecycle polish and multi-step dunning. `:185`'s own note ("3→4, dunning emails need webhook events") supports the split. **State the rationale as 3 webhook-triggered + 1 scheduler-triggered** (F3) — "all four are webhook-triggered" is false |
| **B3** | `PRICING:250` — "The Free tier, gates, and billing UI ship in **Phase 3**" contradicts `SAAS_PRD:179` | Apply the split SPEC-003 recorded: *service* in Phase 2, *gates* in Phase 3. SPEC-003 catalogued this as its B3 and did not land the edit |
| **B4** | `PRICING:27` and `:87` — `work_order_scheduling` names a capability that does not exist | Define the key as **"setting a due date on a work order"** (D13). Without this the key is unenforceable by anyone who did not read this spec |
| **B5** | `PRICING` §3.2 rules 2 and 5 — no category for read gates, and rule 2 reads as forbidding them | Admit **read gates** as a category; scope rule 2's read clause explicitly to *indeterminate billing status*, which is what it was written for; add reads to rule 5's list of where `can()` fires (D14) |
| **B6** | `PRICING` §4.3 describes two downgrade paths; three more states reach the same over-limit condition | Add rows for **trial expiry** (`PRD_REVIEW` B2) and **arrived-over-limit via import** (D16/F2). Both currently fall through to oldest-home-wins with no home-picker |
| **B7** | `PRD_REVIEW` A5 — the baseline migration omits `processed_webhook_events`, and it is **global, no RLS** | Record the table as an explicit RLS carve-out alongside `sessions`. A naive `app.current_account` policy makes every webhook silently reprocess (§4.1) |

---

## 3. File manifest

### New — billing

```
src/mihomes/services/billing/__init__.py
src/mihomes/services/billing/provider.py         Protocol, exceptions, SubscriptionState, NormalizedEvent, factory
src/mihomes/services/billing/stripe_provider.py  StripeProvider — the only Stripe-aware module
src/mihomes/services/billing/service.py          BillingService — mapping, persistence, orchestration
src/mihomes/services/billing/prices.py           plan+interval -> price id, from env (D3)
```

### New — metering

```
src/mihomes/services/metering/__init__.py
src/mihomes/services/metering/meter.py           record_usage / current_usage / reset boundary
src/mihomes/services/metering/ai_wrapper.py      MeteredProvider — proxies the full provider surface (F8)
```

### New — web

```
src/mihomes/web/routes/billing.py                checkout, portal, plan page (owner-only, D8)
src/mihomes/web/routes/webhooks.py               POST /webhooks/stripe — no session auth, no tenant scoping
src/mihomes/web/templates/billing.html
src/mihomes/web/templates/partials/upgrade_prompt.html
```

### New — scheduled jobs

```
src/mihomes/cli/jobs.py                          mihomes jobs trial-sweep | reconcile (D15)
```

### New — models / migration

```
src/mihomes/models/processed_webhook_event.py    global, no RLS (B7)
src/mihomes/models/ai_usage.py                   AIUsageEvent + AIUsageRollup (TenantOwned)
alembic/versions/xxxx_phase3_billing.py          three tables + RLS policies. NO accounts changes
```

### New — email templates (the package itself already exists — SPEC-001 §3, F-note)

```
src/mihomes/services/email/templates/receipt.html + .txt
src/mihomes/services/email/templates/payment_failed.html + .txt
src/mihomes/services/email/templates/trial_ending.html + .txt
src/mihomes/services/email/templates/subscription_cancelled.html + .txt
```

### Modified

| File | Change |
|---|---|
| `entitlements/limits.py` | Real per-plan limits from config, replacing SPEC-003 D7's "free, unlimited" |
| `entitlements/service.py` | `can()` reads `subscription_status`; `usage()` stops being a stub (P3-b) |
| `services/email/service.py` | Four `send_*` methods. **Not** the `EmailProvider` Protocol — it is deliberately transport-only (SPEC-001 §5.1) |
| `services/ai/provider.py` | Declare `stream` on the Protocol (F8); factory returns a metered wrapper |
| `services/ai/agent.py` | **Route `agent_stream` through `get_provider()`** — close the `:79` bypass (D17) |
| `services/vendor.py` | `get_vendor_ratings:262` — gate (F6, the live path) |
| `services/vendor_rating.py` | Gate all three functions (F6, currently dead — so a future caller inherits it) |
| `services/work_order.py` | `create_work_order`/`update_work_order` — gate `due_date` (D13) |
| `services/importer.py` | Assert `can("home.create")` per home (D16) |
| `web/routes/vendors.py` | `:56` — do not eager-load ratings for a Free account |
| `web/templates/{dashboard,property_detail}.html` + 4 vendor partials | Conditional rendering; the page must still load (F6) |
| `pyproject.toml` | `stripe`; coverage `omit` for `stripe_provider.py` (§9) |

**No migration touches `accounts`.** SPEC-002 §4.2 pre-ships every Stripe column —
`stripe_customer_id`, `stripe_subscription_id`, `subscription_status`, `current_period_end`,
`trial_ends_at`, `trial_used_at` — all nullable and tagged `DEFERRED (Phase 3)`. That is the cheap
half of this phase and the reason it needs no `ALTER` on a live table.

---

## 4. Schemas as code

### 4.1 `processed_webhook_events` — global, no RLS

```python
# src/mihomes/models/processed_webhook_event.py
from datetime import datetime

from sqlalchemy import DateTime, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.db import Base
from mihomes.ids import new_id


class ProcessedWebhookEvent(Base):
    """Webhook idempotency ledger. Deliberately NOT TenantOwned (B7).

    A raw webhook arrives before we know which account it belongs to — mapping
    provider_customer_id -> account is BillingService's job (D2). Attaching an
    account_id RLS policy here would make every lookup return zero rows under the
    webhook route's session, and every Stripe event would be reprocessed silently.
    Same carve-out shape as `sessions` (SPEC-002 §7).
    """

    __tablename__ = "processed_webhook_events"
    __table_args__ = (
        # THE idempotency guarantee. Not a bare index — the insert relies on the
        # unique violation itself as the dedup signal (Step 5).
        UniqueConstraint("provider", "provider_event_id",
                         name="uq_processed_webhook_provider_event"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)          # "stripe"
    provider_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)

    # For out-of-order handling (BILLING §6): the provider's timestamp, not ours.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set when the event resolved to an account. NULL is legitimate and expected: an
    # event for an unknown customer is still recorded, so it is not retried forever.
    account_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 4.2 The AI usage meter — event log + monthly rollup

```python
# src/mihomes/models/ai_usage.py
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.db import Base
from mihomes.ids import new_id
from mihomes.models.mixins import TenantOwned


class AIUsageEvent(Base, TenantOwned):
    """One row per *user-initiated* AI call (D11, PRICING §5.2).

    Separate from `ai_conversations` deliberately (D18): that table is DELETEd on
    archive (F10) and only ~5 of ~12 AI paths write to it (F9). A usage counter that
    resets when a customer archives is a billing defect, not a rounding error.
    """

    __tablename__ = "ai_usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Which dispatch path: "web.agent", "cli.ai", "gateway.telegram", ... . Not used
    # for billing — used to prove at audit time that every entry point is metered (A11).
    entry_point: Mapped[str] = mapped_column(String(50), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False)   # complete | structured_output | stream

    # Nullable because this is telemetry, not the billing unit: PRICING §5.1 meters
    # *calls*, not tokens, and some providers return no usage at all.
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AIUsageRollup(Base, TenantOwned):
    """Materialized monthly counter — the number usage() actually returns (D18).

    Incremented in the same transaction as the event row, so two concurrent calls at
    the cap cannot both pass (PRICING §3.2 rule 5).
    """

    __tablename__ = "ai_usage_rollups"
    __table_args__ = (
        UniqueConstraint("account_id", "period_start",
                         name="uq_ai_usage_rollup_account_period"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    # First day of the billing month — NOT the calendar month when a subscription
    # exists. PRICING §5.1 resets on the billing anniversary, so a Pro customer who
    # subscribed on the 20th resets on the 20th. Free accounts have no anniversary
    # and use the calendar month.
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Set when the 80% / 100% nudges are sent, so each fires once (PRICING §5.3).
    warned_80_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    warned_100_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### 4.3 Migration — three tables, two RLS policies, one deliberate carve-out

```python
# alembic/versions/xxxx_phase3_billing.py
def upgrade() -> None:
    op.create_table(
        "processed_webhook_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("account_id", sa.String(36), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_processed_webhook_provider_event",
        "processed_webhook_events", ["provider", "provider_event_id"],
    )
    op.create_index("ix_processed_webhook_account", "processed_webhook_events", ["account_id"])

    # ... ai_usage_events and ai_usage_rollups, with the account_id FK and the
    # (account_id, period_start) unique constraint ...

    # RLS: the two tenant tables get the standard policy; the webhook ledger gets none.
    for table in ("ai_usage_events", "ai_usage_rollups"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (account_id = current_setting('app.current_account', true))
        """)

    # processed_webhook_events is deliberately NOT RLS-enabled (B7). If a later
    # migration adds a policy here, every Stripe event silently reprocesses.
    # A6 is the test that catches that regression.
```

---

## 5. Function signatures

### 5.1 Billing provider — `services/billing/provider.py`

`BILLING` §4.1 **already specifies** `BillingProviderError` / `BillingAuthError` /
`WebhookVerificationError`, the frozen `SubscriptionState` and `NormalizedEvent` dataclasses, and
the `BillingProvider` Protocol (`create_customer`, `create_checkout_session`, `get_subscription`,
`cancel`, `create_portal_session`, `handle_webhook_event`). **Reuse them verbatim** — do not
redeclare, rename, or "improve" them. Only the factory is new:

```python
def get_billing_provider(provider_name: str = "stripe") -> BillingProvider:
    """Mirrors services/ai/provider.py:51 exactly: string dispatch, lazy per-branch
    import, explicit else: raise. No registry, no ABC (F-note on precedent).

    Unlike get_provider(), takes no api_key — BILLING §9 requires the key to come
    from the environment, never from a caller and never from configurations.value
    (§10, SPEC-003 N11).
    """
    if provider_name == "stripe":
        from mihomes.services.billing.stripe_provider import StripeProvider
        return StripeProvider()
    raise BillingProviderError(
        f"Unknown billing provider: {provider_name}. Supported: stripe"
    )
```

### 5.2 Billing service — `services/billing/service.py`

```python
class BillingService:
    """Owns everything the adapter must not: the DB, account mapping, idempotency (D2)."""

    def __init__(self, session: Session, provider: BillingProvider) -> None: ...

    def start_checkout(self, account: Account, plan: str, interval: str) -> str:
        """Returns a hosted checkout URL. Creates the Stripe Customer on first use and
        persists stripe_customer_id.

        Takes `plan` and `interval` only — never a price id (D3). Resolution happens
        internally via prices.price_id_for(plan, interval), read from env.
        """

    def handle_webhook_event(self, raw_body: bytes, signature: str) -> None:
        """The whole webhook path, in this order (Step 5):

        1. provider.handle_webhook_event(raw_body, signature) -> NormalizedEvent.
           Signature verified against RAW BYTES before any parse (N3).
        2. INSERT the ledger row. A unique violation means already-processed -> return.
           Insert-first, never check-then-insert.
        3. Map event.provider_customer_id -> Account. Unknown -> record and return.
        4. Drop the event if occurred_at predates the state already applied
           (BILLING §6 out-of-order delivery).
        5. Apply the BILLING §5 status mapping; send this event type's email.

        Steps 2-5 run in one transaction.
        """

    def reconcile(self, account: Account) -> bool:
        """Re-fetch from the provider and correct drift. Returns True if anything
        changed. Idempotent — the daily sweep's unit of work (D15).
        """

    def apply_subscription_state(self, account: Account, state: SubscriptionState) -> None:
        """The SINGLE place accounts.plan / subscription_status / current_period_end
        are written — SPEC-002 §4.2 requires it ("written ONLY by the billing webhook
        handler"). Called by both handle_webhook_event and reconcile, so live events
        and drift-correction cannot diverge.
        """
```

### 5.3 Entitlements — `entitlements/service.py`

**`can()` and `usage()` keep SPEC-003 §5's signatures character-for-character.** Only the
docstrings and bodies change. A signature edit here puts the two specs in contradiction.

```python
def can(account: Account, action: str, context: dict | None = None) -> Decision:
    """Allowed | Denied(reason, upgrade_target). PRICING §3.2 rules 1-5.
    Separate gate from RBAC (D10) — both must pass.

    Phase 3: subscription_status is now a real input (D5), mapped per BILLING §5.
    Read gates are in scope as of D14 — rule 5's list is not exhaustive.
    """

def usage(account: Account, meter: str) -> UsageReport:
    """{used, limit, resets_at} — real as of Phase 3, closing P3-b.

    `used` reads the materialized AIUsageRollup, never ai_conversations
    (D18: archive.py:120 DELETEs those rows). `limit` is the plan's
    ai_calls_per_month from config. `resets_at` is the billing anniversary, or the
    calendar month for an account with no subscription.
    """
```

### 5.4 The meter — `services/metering/`

```python
# meter.py
def record_usage(session: Session, account: Account, *, entry_point: str,
                 provider: str, method: str,
                 tokens_in: int | None = None,
                 tokens_out: int | None = None) -> None:
    """Insert the event and increment the rollup in ONE transaction, so two
    concurrent calls at the cap cannot both pass (PRICING §3.2 rule 5).
    """

def check_and_reserve(session: Session, account: Account, *, entry_point: str) -> Decision:
    """Called BEFORE dispatch. Denied once the rollup reaches the hard ceiling
    = ai_calls_per_month * (1 + ai_overage_buffer_pct/100) (PRICING §5.3).
    Fires the 80% / 100% nudges once each, via the rollup's warned_* columns.
    """

# ai_wrapper.py
class MeteredProvider:
    """Wraps a concrete AIProvider and meters every invocation (D17).

    MUST proxy the provider's FULL surface, not merely the declared Protocol (F8):
      - stream() is called at agent.py:45 but is NOT declared on AIProvider
      - agent.py:42 assigns provider.model = model — an attribute WRITE
    So: __getattr__ for undeclared methods (metering any that dispatch to the API),
    and __setattr__ passing through to the wrapped instance. Implementing only
    complete/structured_output either breaks streaming or lets it escape the meter —
    and streaming is the second-highest-token path in the app.

    Metering happens per method invocation, not at construction. Construction-time
    counting is equivalent only while no caller caches a provider instance (F10) —
    true today, asserted by A12, and not something to depend on silently.
    """
```

### 5.5 The three feature gates

```python
# services/vendor.py — the LIVE ratings path (F6)
def get_vendor_ratings(session: Session, id_or_slug: str, account: Account) -> dict:
    """Now takes `account`: can(account, "vendor.ratings.view") must be Allowed.
    A read gate, per D14.

    The three functions in services/vendor_rating.py get the same gate despite
    having zero callers today — so whoever wires them up inherits it instead of
    reopening the hole.
    """

# services/work_order.py — due_date ONLY (D13)
#   create_work_order / update_work_order: if a due_date is supplied AND
#   can(account, "work_order.schedule") is Denied -> raise EntitlementError.
#   A work order with no due date is allowed on every plan.
#   gateways/telegram/responder.py:601 passes no due_date (F5), so no bot path
#   trips this gate: user action only, never a background job.

# services/importer.py — D16
#   Assert can(account, "home.create") per home before creating it. Refuse the
#   import rather than creating an over-limit account that PRICING §4.3 has no
#   language for.
```

---

## 6. Sequenced steps

Each step ends in a green test or an observable behaviour. Three ordering constraints are
load-bearing and are called out where they bind: **Step 9 before Step 10** (close the bypass
before metering), **Step 12 before Step 13** (the scheduler exists before the trial needs it), and
**Step 3 before Step 4** (the ledger exists before the webhook writes to it).

**Step 1 — provider skeleton + factory.** `provider.py` reusing `BILLING` §4.1's declarations
verbatim; `stripe_provider.py` with the SDK calls; `get_billing_provider` mirroring
`ai/provider.py:51`'s shape. *Verify:* the factory returns a `StripeProvider` for `"stripe"` and
raises with the supported-list message for anything else; a `FakeBillingProvider` in the test suite
satisfies the Protocol structurally with no subclassing.

**Step 2 — the price map.** `prices.py` resolving `(plan, interval) -> price_id` from the four
`STRIPE_PRICE_*` env vars, following `ai_config.py:11-34`'s precedent (env → config → raise).
*Verify:* a missing env var raises at resolution with a message naming the missing var; no price id
appears in any signature (D3).

**Step 3 — the ledger table + migration.** §4.1 and §4.3, including the RLS carve-out.
**Before Step 4**, so the webhook handler never runs without its idempotency guarantee. *Verify:*
the migration applies and reverts; a second insert of the same `(provider, provider_event_id)`
raises a unique violation; and the table has **no** RLS policy (A6).

**Step 4 — the webhook route.** `POST /webhooks/stripe`, excluded from session auth *and* tenant
scoping (`BILLING:414`). Reads the **raw body**, verifies the signature before any parse.
*Verify:* a tampered body is rejected with no DB write; a valid one reaches the service.

**Step 5 — idempotency and out-of-order handling.** The §5.2 sequence. **Insert-first, treating a
unique violation as the dedup signal** — never check-then-insert, which races under concurrent
delivery on both engines. Drop events whose `occurred_at` predates the applied state.
*Verify:* the same event delivered twice applies once (A5); two concurrent deliveries of the same
event apply once; an out-of-order `subscription.updated` does not resurrect a stale plan (A7).

**Step 6 — checkout + portal.** `start_checkout`, the Customer Portal session, and
`web/routes/billing.py` as owner-only (D8). *Verify:* a non-owner gets 403; a returning customer
reuses `stripe_customer_id` rather than creating a second Stripe Customer.

**Step 7 — status → entitlement mapping.** `apply_subscription_state` implementing `BILLING` §5's
table, called by both the webhook and `reconcile`. *Verify:* each of the eight Stripe statuses maps
to the documented behaviour (A2); `past_due` retains full access and `unpaid` restricts (A8).

**Step 8 — real limits.** `entitlements/limits.py` with per-plan values from config, replacing
SPEC-003 D7's "free, unlimited". Includes the 13 `PRICING` §3.1 keys. *Verify:* `can()` denies a
2nd home / 4th seat / staff invite on Free, and each `Denied` names an upgrade target (A1, A3).
**This is the exit criterion's first half.**

**Step 9 — close the `agent_stream` bypass.** Refactor `agent.py:79` to obtain its client through
`get_provider()`, and declare `stream` on the `AIProvider` Protocol (F8). **Strictly before Step
10** — metering a factory that one path circumvents produces a green suite and an uncapped bill.
*Verify:* zero direct `anthropic.Anthropic(` / `openai.OpenAI(` constructions outside
`services/ai/*_provider.py`, asserted statically (A10); the agentic tool-loop and streaming still
work end to end.

**Step 10 — the meter.** §4.2's tables, `record_usage`, `check_and_reserve`, and `MeteredProvider`
proxying the full surface. `get_provider()` returns the wrapper. *Verify:* **every** AI entry point
increments the counter, enumerated from the tree, not from a list in this document (A11) — the
phase's definition of done. Plus: no provider instance is cached module-level (A12), and archiving
does not reduce `calls_used` (A13).

**Step 11 — overage behaviour.** The soft cap, the 80% / 100% nudges (once each), and the hard
ceiling from `PRICING` §5.3. System-initiated calls are exempt (D11). *Verify:* a Free account at
200 calls still functions up to the ceiling and is denied past it (A14); a nightly job is never
denied (A15).

**Step 12 — the scheduled-job entrypoints.** `mihomes jobs trial-sweep` and `mihomes jobs
reconcile`, both idempotent and safe to run twice (D15). **Before Step 13**, because the trial has
no other trigger (F3). *Verify:* each is a no-op on the second consecutive run (A16); `reconcile`
corrects a deliberately drifted account (A9).

**Step 13 — the trial state machine.** App-managed `trial_ends_at` / `trial_used_at`; entitlements
treat it as `trialing`; expiry downgrades via the sweep. One trial per account, ever. *Verify:* a
trial grants Pro entitlements with **no Stripe subscription existing** (A17); a second trial is
refused (A18); expiry downgrades and produces the over-limit state B6 documents rather than
silently dropping a home (A19).

**Step 14 — downgrade and restricted mode.** `PRICING` §4.3: surplus homes/seats read-only, core
home fully usable, nothing deleted. Covers all three arrival paths — past-due, voluntary, and
trial expiry. *Verify:* a downgraded account can still read every home and edit the core one; no
row is deleted by any downgrade path (A20).

**Step 15 — the four emails.** Template pairs plus four `send_*` methods on the **existing**
`EmailService` (SPEC-001 §3). Three fire from webhooks; `trial_ending` fires from Step 12's sweep
(F3, B2). Do **not** extend the `EmailProvider` Protocol — it is transport-only. *Verify:* each
renders subject/html/text through `ConsoleProvider`; each fires exactly once per triggering event
(A21).

**Step 16 — the feature gates.** §5.5's three gates. **Ratings needs two separate pieces of work:**
(a) the CLI and `routes/vendors.py:56` paths return `Denied`; (b) `dashboard.html` and
`property_detail.html` must still **load** for a Free user, so their gate sits at **context
assembly** — do not populate the rating data — plus template conditionals. Gating the route would
403 the dashboard. *Verify:* Free denies ratings at every surface in F6's table while both pages
still render (A22); `due_date` is denied on Free while an undated work order succeeds (A23); the
Telegram path is unaffected (A24).

**Step 17 — the importer gate.** D16: assert per home, refuse an over-limit import.
*Verify:* importing more homes than the plan allows fails cleanly and leaves no partial account
(A25).

**Step 18 — the reconciliation sweep in anger.** Wire Step 12's `reconcile` over all accounts with
a Stripe customer. *Verify:* a dropped webhook is detected and corrected within one sweep (A9).

**Exit criterion check.** With Steps 1–18 green: a Free account hits a gate, upgrades through
Checkout, and the gate flips **from the webhook** — not the redirect (D1). That is `SAAS_PRD:180`.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not grant entitlements on the `success_url` redirect.** The user controls that URL: they
can reach it without paying, replay it, or never arrive at all after a successful payment. Only a
signature-verified webhook changes state (D1). This is the single most common Stripe integration
defect and it fails *open*.

**N2 — Do not put a price id in the interface or accept one from the client.** `create_checkout_session`
takes `(plan, interval)` (D3). A client-supplied price id is a self-service discount, and a price id
in the Protocol makes the adapter's config leak into every caller.

**N3 — Do not parse the webhook body before verifying the signature.** Verification is over **raw
bytes**; any framework that hands you a parsed body has already re-serialized it and the signature
will not match — or worse, will match after you have acted on unverified input. Read the raw body
first, verify, then parse.

**N4 — Do not check-then-insert for idempotency.** `SELECT` then `INSERT` races: two concurrent
deliveries of the same event both see "not present" and both process. Insert first and treat the
unique violation as the dedup signal (Step 5). Correct on Postgres and SQLite alike, so no dialect
branch is needed in the webhook path.

**N5 — Do not let the provider adapter touch the database.** `BILLING` §4.1 is explicit:
`NormalizedEvent` carries *provider* identifiers, never an `account_id`. The moment the adapter
resolves an account it stops being swappable and starts being untestable (D2).

**N6 — Do not enforce any limit only in the UI.** `PRICING` §3.2 rule 5: UI gating is a courtesy,
the service check is the enforcement. A hidden "Add home" button is not a gate — the route is.

**N7 — Do not meter only the web path.** The CLI, the Telegram gateway, the assessors, the
orchestrator, and `agent_stream` all dispatch to a provider. A meter on one of them caps nothing
(§8's definition of done). This is why Step 9 precedes Step 10.

**N8 — Do not derive usage from `ai_conversations`.** `archive.py:120` DELETEs those rows (F10), and
only ~5 of ~12 AI paths write one at all (F9). A derived counter resets when a customer archives.
Materialize (D18).

**N9 — Do not gate `Appointment` / `/calendar` as `work_order_scheduling`.** D13 scopes the key to
`WorkOrder.due_date`. Gating the calendar would paywall the Telegram bot's appointment creation and
`services/recurring.py:158`'s nightly automation — which would present to a Free user as a broken
background job, not as an upgrade prompt.

**N10 — Do not gate a system-initiated action.** Every gate and the meter fire on **user action
only** (D11, `PRICING` §5.2). A limit that trips a scheduled job is a bug: the user cannot upgrade
their way out of something they did not do.

**N11 — Do not 403 the dashboard to enforce the ratings gate.** `dashboard.html` and
`property_detail.html` render ratings but must remain loadable on Free. Gate at context assembly,
not at the route (Step 16).

**N12 — Do not write the Stripe secret key to `configurations.value`.** `BILLING` §9 requires
environment variables and a **restricted** key scoped to the operations the provider performs.
SPEC-003's **N11** still stands: no new secret-write paths to that plaintext column until SPEC-003's
O1 is answered.

**N13 — Do not add an `accounts` migration.** SPEC-002 §4.2 pre-ships every column. Adding one here
means SPEC-002 was implemented differently than specified — stop and reconcile (§0.1) rather than
patching around it.

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| Metered / usage-based billing (`report_usage`) | 4+ | `BILLING` §8 — captured so `BillingProvider` can grow the method without disrupting callers. §4.2's event log is already the data source it would read |
| Full dunning sequence (multi-step, escalating) | 4 | `SAAS_PRD:185`. Phase 3 sends one `payment_failed` email; the retry ladder is Phase 4 (B2) |
| Email lifecycle polish — onboarding drips, re-engagement | 4 | `SAAS_PRD:182/187`. The four transactional templates ship here |
| Card-first Stripe-native trials | 4+ | The `customer.subscription.trial_will_end` handler is written (Step 5) but unreachable while the trial is card-less (F3). Switching trial styles then needs no new handler |
| Tax / VAT collection (Stripe Tax) | 4+ | `BILLING` §10. A hosted Checkout session can enable it later with no interface change |
| Refunds and proration UI | 4+ | The Customer Portal covers cancellation today; refunds stay a manual Stripe-dashboard action |
| Annual↔monthly switching mid-period | 4+ | `create_checkout_session` already takes `interval`; the proration policy is the missing decision, not the plumbing |
| Per-seat pricing | 4+ | `PRICING` Q3. Seats are a *limit* here, not a billed quantity |
| At-rest secret encryption | ? | **SPEC-003's O1, still open.** Phase 3 does not widen it (N12) and does not close it (§10) |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | Free denies a 2nd home, a 4th seat, and a staff invite | `test_limits.py::test_free_gates` |
| A2 | All eight Stripe statuses map to `BILLING` §5's documented behaviour | `test_billing_mapping.py::test_status_table` |
| A3 | Every `Denied` names an `upgrade_target` (`PRICING` rule 4) | `test_limits.py::test_denied_names_target` |
| A4 | A tampered webhook body is rejected and writes nothing | `test_webhooks.py::test_bad_signature_no_write` |
| A5 | The same event delivered twice applies exactly once | `test_webhooks.py::test_idempotent_replay` |
| A6 | `processed_webhook_events` has **no** RLS policy | `test_webhook_tenancy.py::test_ledger_not_rls` |
| A7 | An out-of-order `subscription.updated` does not resurrect a stale plan | `test_webhooks.py::test_out_of_order_dropped` |
| A8 | `past_due` retains full access; `unpaid` restricts per §4.3 | `test_downgrade.py::test_grace_then_restrict` |
| A9 | A dropped webhook is corrected by one reconciliation sweep | `test_reconcile.py::test_drift_corrected` |
| A10 | **No direct SDK client construction outside `services/ai/*_provider.py`** | `test_ai_metering.py::test_no_factory_bypass` |
| A11 | **Every AI entry point increments the usage counter** — enumerated from the tree | `test_ai_metering.py::test_all_entry_points_metered` |
| A12 | No AI provider instance is cached module-level | `test_ai_metering.py::test_no_module_level_cache` |
| A13 | Archiving does not reduce `calls_used` | `test_ai_metering.py::test_archive_preserves_usage` |
| A14 | The hard ceiling denies; the soft cap does not; each nudge fires once | `test_overage.py::test_ceiling_and_nudges` |
| A15 | A system-initiated AI call is never metered or denied | `test_overage.py::test_system_calls_exempt` |
| A16 | Both scheduled jobs are no-ops on a second consecutive run | `test_jobs.py::test_idempotent` |
| A17 | A trial grants Pro entitlements with **no Stripe subscription existing** | `test_trial.py::test_cardless_trial_entitlements` |
| A18 | A second trial on the same account is refused | `test_trial.py::test_one_trial_ever` |
| A19 | Trial expiry downgrades and surfaces the over-limit state, dropping nothing | `test_trial.py::test_expiry_is_nondestructive` |
| A20 | No downgrade path deletes a row; the core home stays editable | `test_downgrade.py::test_nothing_deleted` |
| A21 | Each of the four emails renders and fires once per triggering event | `test_billing_emails.py::test_four_templates` |
| A22 | Free denies ratings at every F6 surface **and** both dashboard pages still load | `test_feature_gates.py::test_ratings_gated_pages_load` |
| A23 | `due_date` is denied on Free; an undated work order succeeds | `test_feature_gates.py::test_due_date_gate` |
| A24 | The Telegram work-order path is unaffected by the scheduling gate | `test_feature_gates.py::test_bot_path_ungated` |
| A25 | An over-limit import fails cleanly and leaves no partial account | `test_importer_gate.py::test_over_limit_refused` |
| A26 | Two concurrent AI calls at the cap: exactly one proceeds | `test_overage.py::test_concurrent_at_cap` |
| A27 | Two concurrent webhook deliveries of one event: one applies | `test_webhooks.py::test_concurrent_delivery` |
| A28 | A non-owner is denied every billing route | `test_billing_routes.py::test_owner_only` |
| A29 | No price id appears in any signature or arrives from a request | `test_prices.py::test_no_price_id_in_interface` |
| A30 | The Phase 3 migration applies and reverts cleanly | `test_migration_phase3.py::test_up_down` |
| A31 | **Upgrade end to end: a Free gate flips to Pro via webhook, not redirect** | `test_upgrade_flow.py::test_exit_criterion` |

**A11 is the phase's definition of done.**

> **The AI meter binds at every entry point, or it bounds nothing.** A meter on the web route but
> not the Telegram bot, the CLI, or `agent_stream` does not cap Claude spend — the limit is only as
> strong as its leakiest dispatch path.

This is the cost-control analogue of SPEC-003's A15. The survey already found the leak (F8:
`agent.py:79` constructs its own client), which is why A10 and A11 are written as *enumeration*
tests rather than as a design intention: A11 must discover entry points **from the tree** and assert
each one increments the counter. A hand-maintained list in a test file rots the first time someone
adds a dispatch path, and it rots silently.

A31 is the exit criterion. If A31 is red, the phase has not shipped regardless of what else is
green.

---

## 9. Test manifest

```
tests/unit/test_limits.py                  per-plan limits, Free gates, upgrade targets
tests/unit/test_billing_mapping.py         all 8 Stripe statuses -> behaviour (BILLING §5)
tests/unit/test_prices.py                  env resolution, missing var, no id in interface
tests/unit/test_ai_metering.py             THE enforcement tests (A10-A13) — largely static
tests/unit/test_overage.py                 ceiling, nudges, system-exempt, concurrency
tests/unit/test_webhook_tenancy.py         the RLS carve-out (A6) — static schema assertion
tests/integration/test_webhooks.py         signature, idempotency, out-of-order, concurrency
tests/integration/test_billing_routes.py   checkout, portal, owner-only
tests/integration/test_trial.py            card-less trial, one-per-account, expiry
tests/integration/test_downgrade.py        grace -> restricted, non-destructive, all 3 paths
tests/integration/test_reconcile.py        drift detection and correction
tests/integration/test_jobs.py             both entrypoints, idempotency
tests/integration/test_billing_emails.py   four templates via ConsoleProvider
tests/integration/test_feature_gates.py    ratings (+ pages still load), due_date, bot path
tests/integration/test_importer_gate.py    over-limit refusal, no partial account
tests/integration/test_migration_phase3.py up/down — see the note below
tests/integration/test_upgrade_flow.py     THE exit criterion (A31)
```

**Fixtures.** Extend SPEC-002's `account_a` / `account_b` with `account_free`, `account_pro`,
`account_trialing` and `account_past_due`, plus:

- **`FakeBillingProvider`** — satisfies the Protocol structurally (no subclassing, per `AIProvider`'s
  precedent), with settable `SubscriptionState` and a queue of `NormalizedEvent`s. Every test above
  except the ones that specifically exercise Stripe payload parsing uses this.
- **A Stripe fixture-payload set** — real captured webhook bodies for the six `BILLING` §6 events,
  stored as raw bytes so signature verification is exercised on the real thing (A4).
- **`ConsoleProvider` for email**, not a Resend mock — SPEC-001 §3 ships it for exactly this
  purpose.

**Coverage.** Add `stripe_provider.py` to `pyproject.toml`'s `omit` list, following the existing
precedent for the AI provider HTTP implementations (`pyproject.toml:66-76`). Testing that the Stripe
SDK works is Stripe's job; the seam worth testing is the Protocol boundary, and
`FakeBillingProvider` tests it.

**One gap to close deliberately.** `tests/conftest.py` builds schema from `Base.metadata`, not
Alembic, so **migrations are never exercised by any existing test** — the only conftest has just two
fixtures, `engine` (`:18-23`) and `session` (`:26-33`). `test_migration_phase3.py` therefore needs
its own engine and must run the real migration up and down (A30), or this phase's DDL ships
unverified. Reuse the `session` fixture name and semantics everywhere else, per SPEC-002 §9.

**The adversarial pattern for A11.** Not "does the meter increment when called" — that passes
trivially. Instead: enumerate every module that dispatches to an AI provider **by walking the tree**,
then assert each one produces an `AIUsageEvent`. The test must fail when someone adds a nineteenth
dispatch path without metering it. Pair it with A10's static assertion that no module outside
`services/ai/*_provider.py` constructs an SDK client directly — that is the check `agent.py:79`
would have failed.

---

## 10. What this phase does not make safe

Stated so the next spec inherits it honestly.

- **Secrets at rest — SPEC-003's O1, still open.** Provider API keys remain plaintext in
  `configurations.value` and `mihomes config list` still prints them unredacted
  (`cli/config.py:39-50`). Phase 3 does **not** widen this: `STRIPE_SECRET_KEY` comes from the
  environment and never touches that column (N12). But it does not close it either, and Phase 3 adds
  a second class of high-value secret to the deployment. **Note this is SPEC-003's O1, not
  SPEC-002's (closed) O1 and not this spec's O1** (§0.2).
- **Revenue correctness.** Every criterion here proves the *mechanism* — gates flip, webhooks apply
  once, the meter counts. None proves the **prices are right**: they are all `PLACEHOLDER` until O1
  (§1.3). A perfectly correct billing system charging the wrong amount is still wrong, and no test
  in §9 will tell you.
- **The Stripe account's own configuration.** Products, prices, tax settings, the webhook endpoint
  secret, and whether the restricted key is actually scoped correctly all live in the Stripe
  dashboard. Nothing in this repo verifies them, and a mis-scoped key fails at runtime in production.
- **Cost attribution below the account.** The meter counts calls per account, which is what
  `PRICING` §5.1 bills. It does not attribute cost per user, per property, or per token — so an
  account can exhaust its own quota through one member's usage with no visibility into who.
- **Inference cost vs. price.** `ai_calls_per_month` caps *calls*, not tokens. A user sending very
  long contexts costs materially more per call than the pricing model assumes. The event log records
  `tokens_in`/`tokens_out` so this becomes measurable, but nothing acts on it until metered billing
  (`BILLING` §8, Phase 4+).
- **The four items SPEC-003 §10 declared, unchanged:** mis-declared action keys (the harness proves
  a route declares *something*, not the right thing); the Telegram bot's transport; and aggregate
  inference by scoped staff.

