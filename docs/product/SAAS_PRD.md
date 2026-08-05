# MiHomes SaaS — Master Product Requirements Document

> **Purpose:** Define the transformation of MiHomes from a single-user, local-first CLI/web tool into a multi-tenant, subscription SaaS at **mihomes.ai** — with a promotional landing page, Google authentication, a household/estate account model, team roles, staff invites, freemium pricing, and onboarding.
> **Status:** Draft — 2026-07-27
> **Owner:** Chris Klaus (Founder/PM)
> **Companion docs:** see [§13 Document Set](#13-document-set) — this PRD is the anchor; each subsystem has its own deep-dive.

---

## 1. Executive Summary

MiHomes today is a mature, well-tested **single-user** estate-management system: ~38k LOC of Python (incl. tests), 28 model modules defining 36 tables, 23 web-route modules (FastAPI + htmx), an AI advisory layer (Claude/OpenAI/NIM/Ollama behind a provider abstraction), and two chat gateways (WhatsApp, Telegram). It runs against **one global SQLite database with no concept of a user, account, or tenant**.

This document specifies the pivot to a **hosted, multi-tenant SaaS** that anyone can sign up for at mihomes.ai. The strategy is **de-risked and phased**: ship a promotional landing page + waitlist + Google sign-in *first* to validate demand, then build the multi-tenant foundation, onboarding, team roles, and freemium billing behind it.

**The core product promise:** *One AI-first command center for every home you run — and everyone who helps you run it.*

**The freemium wedge:** a household runs its own home **free forever** (1 home, 3 seats). The moment you operate like a business — a second home, or inviting external **staff** — you upgrade.

---

## 2. Problem & Opportunity

### 2.1 The user problem
Managing multiple homes is coordination chaos: maintenance, staff, vendors, bills, inventory, documents, and calendars — scattered across text threads, spreadsheets, sticky notes, and memory. Existing tools are either single-property (consumer home-maintenance apps) or heavyweight (commercial property-management suites built for landlords, not owners). Nothing is **AI-first**, **multi-home**, and **team-aware** for the owner/estate-manager persona. Category-by-category competitive anchoring (and what each adjacent category implies for our pricing) lives in [`PRICING_AND_PACKAGING.md`](PRICING_AND_PACKAGING.md) §6; per-category feature teardowns are still to be done before GA positioning is finalized.

### 2.2 Why now
- The single-user product already proves the domain model and the AI advisory value.
- AI agents can now do real estate-management reasoning (prioritization, drafting, research) cheaply enough to be a core feature, not a gimmick.
- The founder owns **mihomes.ai** and wants to launch.

### 2.3 The opportunity
A subscription SaaS for multi-home owners and the people who help them run their homes, with a clear free→paid wedge and multiple future revenue surfaces (team seats, AI usage, and a **vendor-discovery marketplace** — see companion PRD).

---

## 3. Target Users & Personas

| Persona | Description | Plan fit | Key jobs |
|---|---|---|---|
| **The Household Owner** | Owns/runs 1 home; wants it organized; maybe a partner + kid also help | **Free** | Track tasks, issues, bills, documents; ask the AI advisor |
| **The Multi-Home Owner** | 2–5 homes (primary + vacation); a housekeeper or two | **Pro** | Coordinate across homes; invite staff; delegate |
| **The Estate Manager / Family Office** | Runs several properties with a team, on behalf of a principal | **Estate** | Full team, all homes, reporting, priority AI |
| **The Staff Member** | Housekeeper, property manager, handyman coordinator — *invited* | (seat on a paid plan) | See assigned home(s), complete tasks, log issues, chat via Telegram/WhatsApp |

Primary launch ICP: **multi-home owners and families with household staff** — they feel the pain most and can pay.

---

## 4. Current State (Honest Baseline)

> This section is deliberately blunt: it defines the size of the lift. Detail in [`../architecture/MULTITENANCY.md`](../architecture/MULTITENANCY.md).

**What exists and is solid:**
- 28 model modules defining 36 tables, layered architecture (CLI → Service → Model), 780+ tests, 36 Alembic migrations.
- FastAPI + htmx web app (23 route modules). Note: every one of the 36 tables (incl. child tables like `transactions`, `task_schedules`, `guests`) needs `account_id` + RLS — this is the true size of the isolation work.
- AI advisory layer with a clean provider abstraction (Claude/OpenAI/NIM/Ollama) and 5 roles; SPACE prioritization framework (Safety > Presence > Asset Protection > Compliance > Economy).
- Two chat gateways (WhatsApp via Baileys bridge, Telegram native) + calendar (Google/iCal). Watchdog supervision.

**What does NOT exist (the gap to close):**
- ❌ No user / account / tenant concept anywhere. One global SQLite DB, one unscoped `get_session()`.
- ❌ No authentication. No login. No sessions.
- ❌ No billing, no plans, no entitlements.
- ❌ No onboarding, no invites, no roles.
- ❌ No hosted deployment story for many customers (it's built to run as one always-on instance for one owner).

**Conclusion:** This is a **re-platform**, not a bolt-on. The domain logic is reusable; the identity, isolation, hosting, and monetization layers are net-new. The plan below sequences that lift to keep risk low and ship value early.

---

## 5. Product Vision & Principles

1. **AI-first, not AI-garnish.** The estate manager AI is the headline, surfaced in the web app and every chat gateway.
2. **Household free, business paid.** The free line is generous enough that a family never *has* to pay to run their own home; you pay when you scale or bring in a team.
3. **Team-aware from the core.** Owner/admin/staff and per-home scoping are foundational, not afterthoughts.
4. **Tenant isolation is sacred.** Cross-tenant data leakage is the cardinal sin; defense-in-depth (query scoping + Postgres RLS).
5. **Vendor-neutral infrastructure.** Payments, email, AI, and messaging all sit behind internal provider interfaces — no business logic touches a vendor SDK directly. (This mirrors the codebase's existing AI-provider pattern.)
6. **Ship the landing first.** Validate demand before finishing the big build.

---

## 6. Scope

### 6.1 In scope (this PRD + companion docs)
- Promotional landing page at mihomes.ai + waitlist.
- Google OAuth (OIDC) sign-in.
- Multi-tenant data model (accounts / users / memberships; `account_id` on all tenant-owned tables; SQLite→Postgres).
- Onboarding (create account → first home → dashboard).
- Roles (owner / admin / staff) + RBAC enforcement.
- Staff invitations (email via Resend).
- Freemium billing (Free / Pro / Estate) via Stripe + an entitlements service.
- Transactional email (Resend) behind an `EmailProvider` interface.

### 6.2 Explicitly out of scope for initial GA (future bets, have their own PRDs)
- **Vendor Discovery marketplace** (AI research + public ratings) — [`VENDOR_DISCOVERY_PRD.md`](VENDOR_DISCOVERY_PRD.md).
- **Twilio gateway** (SMS/MMS/Voice, official WhatsApp Business API) — [`TWILIO_PRD.md`](TWILIO_PRD.md).
- **Expanded Telegram capabilities** (interactive keyboards, photo/voice intake, per-tenant linking) — [`TELEGRAM_PRD.md`](TELEGRAM_PRD.md).
- Native mobile apps; non-Google auth; marketing automation; multi-language.

---

## 7. Key Product Decisions (Locked)

| Decision | Choice | Rationale / detail |
|---|---|---|
| **Tenancy model** | Shared Postgres + `account_id` on every tenant table; RLS backstop | Cheapest at free-tier scale; one schema; [`MULTITENANCY.md`](../architecture/MULTITENANCY.md) |
| **Auth** | Google OAuth (OIDC) only at launch; keyed on Google `sub` | No passwords to secure; fast; [`ONBOARDING_AUTH_RBAC.md`](ONBOARDING_AUTH_RBAC.md) |
| **Account shape** | `accounts` (tenant) ← `memberships` → `users` (global); a user can join many accounts | Supports estate managers serving multiple families |
| **Roles** | owner / admin / staff | Owner holds billing; staff is scoped external help |
| **Pricing** | Free / Pro / Estate | Free = 1 home + 3 seats forever; [`PRICING_AND_PACKAGING.md`](PRICING_AND_PACKAGING.md) |
| **Free line** | 1 home, 3 seats, **no staff invites** | Household free; 2nd home, 4th seat, or inviting staff = upgrade |
| **Billing** | Stripe Billing behind `BillingProvider` | [`BILLING_AND_EMAIL.md`](../architecture/BILLING_AND_EMAIL.md) |
| **Email** | Resend (default) behind `EmailProvider`; failover to Postmark/SES | Same doc |
| **Launch strategy** | Landing + waitlist FIRST, then MVP | De-risk the re-platform; [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) |
| **Domain** | mihomes.ai (registered) | Marketing on apex; app on `app.mihomes.ai`; email on `send.mihomes.ai` |
| **Hosting** | Fly.io, single region | Scale-to-zero fits launch traffic; TLS/deploys are platform features. Postgres managed-vs-unmanaged still to confirm — [`MULTITENANCY.md`](../architecture/MULTITENANCY.md) §11 |

---

## 8. Functional Requirements

### 8.1 Landing & waitlist (Phase 0)
- Promotional page: value prop, how-it-works, feature highlights, pricing teaser, FAQ, waitlist CTA.
- Waitlist capture (email + light qualification: # of homes, has staff?), Resend confirmation email, founding-member offer.
- Google sign-in **stub** — the OAuth flow verifies the email and adds it to the waitlist; full sessions/tenancy arrive in Phase 1 (there is no `users` table before then). See [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) §4.
- **Exit criteria:** waitlist gate proposed in the GTM plan — **≥250 signups at ≥3% sustained landing conversion** *(PLACEHOLDER — founder to confirm)*.

### 8.2 Authentication (Phase 1)
- "Sign in with Google" → OIDC authorization-code flow → create/lookup `users` row on Google `sub` → resolve memberships → route to account or onboarding.
- Secure server-side session (httpOnly cookie). Sign-out. Revocation handling.

### 8.3 Multi-tenant foundation (Phase 1)
- New tables: `accounts`, `users`, `memberships`.
- `account_id` (non-null FK) added to all 36 tenant-owned tables; every query scoped to the current account.
- Postgres RLS policies as fail-closed backstop.
- Migration from SQLite: the 36 existing Alembic revisions are **squashed to a Postgres baseline** (no hosted history to preserve); existing **local single-user installs** (including the founder's production DB) migrate via a one-shot importer — one SQLite DB becomes exactly one account, local user promoted to owner. The founder's own DB is the migration rehearsal and first dogfood tenant. Local single-tenant CLI mode is preserved (one local DB = one account). Detail: [`MULTITENANCY.md`](../architecture/MULTITENANCY.md) §5–6.

### 8.4 Onboarding (Phase 2)
- First sign-in with no membership → guided flow: name your account (household/estate) → add first home (name, address, type) → optional rooms → land on dashboard. Mandatory steps minimal; time-to-value prioritized.

### 8.5 Team, roles & invites (Phase 2)
- Owner/admin can invite by email → tokenized Resend invite → invitee signs in with Google → membership created with role (+ optional home scoping).
- RBAC enforced at the service/route layer, composed with tenant scoping.
- Free plan blocks staff invites (upgrade prompt). Seat limits enforced at invite time. **Sequencing note:** these gates need the entitlements *check* before Stripe exists — Phase 2 ships the entitlements service reading plan limits from config (every account is `free`); Phase 3 only adds Stripe as an *input* to it. Without this, Phase 2 would secretly depend on Phase 3.
- Account switcher for multi-account users. Owner transfer; member offboarding; last-owner protection.

### 8.6 Billing & entitlements (Phase 3)
- Stripe Checkout for Free→Pro/Estate; Stripe Customer Portal for self-serve management.
- Webhooks are the **source of truth** for entitlement changes (signature-verified, idempotent).
- Central **entitlements service** (introduced in Phase 2 with config-only plans, see §8.5) gains billing status as an input: given an account's plan + subscription state, what's allowed (max homes, max seats, staff invites, AI usage). Every gated action checks it.
- Humane downgrade/past-due policy (grace → read-only, not data loss).

### 8.7 Email lifecycle (Phases 0/2/3/4)
- Transactional emails via `EmailService` → Resend, phased per [`BILLING_AND_EMAIL.md`](../architecture/BILLING_AND_EMAIL.md) §2.6: waitlist confirmation (0); welcome, staff invite, invite accepted (2); receipt/invoice, trial ending, payment failed/dunning, cancellation (3); full lifecycle polish (4). DKIM/SPF/DMARC on `send.mihomes.ai`.

---

## 9. Non-Functional Requirements

| Category | Requirement |
|---|---|
| **Isolation** | No cross-tenant reads/writes, ever. Automated A-vs-B isolation test in CI. RLS fail-closed. |
| **Security** | OIDC best practices; signed/rotated sessions; Stripe/Twilio webhook signature verification; secrets in env, never in code (repo already gitignores `.env`, `*_token.json`, `*.pem`). |
| **Availability** | Hosted, always-on; target **99.5% monthly uptime** at GA *(PLACEHOLDER — no formal SLA on Free)*. The current single-instance watchdog/monitor model must be rethought for hosting (per-tenant workers don't scale; prefer webhook-driven gateways + shared schedulers). |
| **Performance** | Web pages fast (htmx keeps payloads small); AI calls async where possible; landing page must be fast/responsive. |
| **Cost control** | AI is the main variable cost — metered per account, gated by plan. Shared research cache where cross-tenant safe (see Vendor Discovery). |
| **Compliance** | GDPR/CCPA data handling; data export & deletion; email opt-out; (future) A2P 10DLC for Twilio SMS. |
| **Observability** | Per-tenant audit log; billing/webhook event log; email delivery tracking. |

---

## 10. Implementation Phases

> Phase numbers are **canon across the entire doc set.**

| Phase | Name | Delivers | Exit criteria |
|---|---|---|---|
| **0** | **Landing + Waitlist** | Promo page at mihomes.ai, waitlist + Resend confirmation, Google sign-in stub, DNS/email setup | Target waitlist signups reached |
| **1** | **Multitenant Foundation** | SQLite→Postgres, accounts/users/memberships, `account_id` scoping + RLS, Google auth, sessions | A user can sign in and see only their own account's data; isolation test green |
| **2** | **Onboarding + Team + RBAC** | Onboarding flow, staff invites (Resend), owner/admin/staff enforcement, account switcher, entitlements service (config-only: all accounts Free) | An owner can onboard, invite an admin/staff, and roles + Free limits are enforced |
| **3** | **Billing / Freemium** | Stripe Checkout + Portal, webhooks, billing status wired into entitlements, plan gates (homes/seats/staff/AI) | A Free user can upgrade to Pro and gates flip via webhook |
| **4** | **Polish + Email Lifecycle + GA** | Full email lifecycle, dunning, hardening, docs, public launch | GA definition of done (below) met |
| **4+** | **Growth bets** (separate PRDs) | Vendor Discovery, Twilio gateway, expanded Telegram | Per-PRD |

**Phase dependencies (explicit):** Each phase gates the next — 0→1 (demand validated before re-platform spend), 1→2 (invites/RBAC need `memberships` + tenant scoping), 2→3 (Stripe Checkout needs an owner who onboarded; entitlements service already exists from Phase 2 in config-only form, Phase 3 wires billing state into it), 3→4 (dunning emails need webhook events). The only Phase-0 infrastructure dependency shared with later phases is the `EmailProvider`/Resend abstraction — it ships in Phase 0 (waitlist confirmation) and is reused, not rebuilt, in Phases 2–4. Duration estimates and per-phase gates live in [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) §6.

**MVP cut line.** The MVP (end of Phase 3) is: Google sign-in → onboard an account + first home → invite an admin/staff → upgrade Free→Pro via Stripe — on the *existing* domain feature set (tasks, issues, vendors, inventory, documents, AI advisor). Anything not on that path — full email lifecycle, dunning, data export/deletion tooling, account deletion self-serve, Estate-only features (predictive maintenance, weekly AI reports, audit export) — is Phase 4 or later. Chat gateways (Telegram/WhatsApp) remain **single-tenant/founder-only until made tenant-aware** (a 4+ growth bet); they are not part of the hosted MVP.

**GA definition of done (Phase 4 exit):**
- All Phase 1–3 exit criteria still green (isolation test in CI, RBAC enforced, Free→Pro upgrade + webhook reconciliation).
- Full transactional email lifecycle live (welcome → invite → receipt → dunning → cancellation) with DKIM/SPF/DMARC passing.
- Downgrade/past-due grace policy implemented per [`PRICING_AND_PACKAGING.md`](PRICING_AND_PACKAGING.md) §4.3.
- Data export and account-deletion paths exist (GDPR/CCPA baseline from §9).
- Terms of Service + Privacy Policy published; support channel staffed (even if it's the founder).
- Waitlist invited in; public signup open at mihomes.ai.

---

## 11. Success Metrics

| Funnel stage | Metric | Notes |
|---|---|---|
| Demand | Waitlist signups; landing conversion rate | Phase 0 gate |
| Acquisition | Signups; signup→onboarding completion | Google sign-in friction check |
| Activation | % who create their 1st home; time-to-first-home | Core "aha" |
| Engagement | WAU/MAU; AI advisor usage; issues/tasks created (incl. via chat) | |
| Team | % accounts that invite ≥1 member; staff activation | Drives Free→Pro |
| Monetization | Free→Paid conversion; MRR; trial→paid | |
| Retention | Logo & revenue retention; churn reasons | |

Initial target proposals (waitlist ≥250, landing conversion ≥3%, Free→Paid 3–8%) live in [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) §8 — all PLACEHOLDER until the founder ratifies them against the Phase 0 baseline.

---

## 12. Risks & Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| **Cross-tenant data leak** | Catastrophic (trust) | Query scoping + RLS backstop + CI isolation test |
| **Re-platform scope creep** | Delays launch | Phase 0 landing ships independently; strict phase gates |
| **SQLite→Postgres migration bugs** | Data integrity | Squash+backfill plan, staging rehearsal, dual-mode support |
| **AI cost blowout** | Margin | Per-account metering, plan gates, caching |
| **Low demand** | Wasted build | Validate via waitlist BEFORE finishing the big build |
| **Email deliverability** | Invites/receipts fail | Proper DKIM/SPF/DMARC; dedicated sending subdomain; provider failover |
| **Google-only auth excludes some** | Friction | Abstraction allows adding email/password + IdPs later |

---

## 13. Document Set

This PRD is the anchor. Each subsystem has a dedicated doc:

| Doc | Path | Covers |
|---|---|---|
| **Master PRD** (this) | `docs/product/SAAS_PRD.md` | Vision, scope, phases, the whole picture |
| Multitenancy Architecture | [`docs/architecture/MULTITENANCY.md`](../architecture/MULTITENANCY.md) | Postgres, accounts/users/memberships, tenant scoping, RLS, migration |
| Pricing & Packaging | [`docs/product/PRICING_AND_PACKAGING.md`](PRICING_AND_PACKAGING.md) | Free/Pro/Estate, limits, entitlements, upgrade/downgrade |
| Onboarding, Auth & RBAC | [`docs/product/ONBOARDING_AUTH_RBAC.md`](ONBOARDING_AUTH_RBAC.md) | Google auth, onboarding, invites, roles |
| Billing & Email | [`docs/architecture/BILLING_AND_EMAIL.md`](../architecture/BILLING_AND_EMAIL.md) | Stripe + Resend behind provider interfaces |
| GTM & Launch | [`docs/product/GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) | Landing page, waitlist, DNS, launch plan |
| Telegram PRD | [`docs/product/TELEGRAM_PRD.md`](TELEGRAM_PRD.md) | Current gateway + roadmap (growth bet) |
| Twilio PRD | [`docs/product/TWILIO_PRD.md`](TWILIO_PRD.md) | SMS/MMS/Voice + official WhatsApp (growth bet) |
| Vendor Discovery PRD | [`docs/product/VENDOR_DISCOVERY_PRD.md`](VENDOR_DISCOVERY_PRD.md) | AI vendor research + public ratings marketplace (growth bet) |
| Omnichannel Gateway PRD | [`docs/product/OMNICHANNEL_GATEWAY_PRD.md`](OMNICHANNEL_GATEWAY_PRD.md) | One core behind WhatsApp/Telegram/Twilio (growth bet). Partially repaired 2026-08-05 — see `../PRD_REVIEW.md` §G |
| WhatsApp Gateway PRD | [`docs/product/WHATSAPP_GATEWAY_PRD.md`](WHATSAPP_GATEWAY_PRD.md) | WhatsApp Business Cloud API gateway (growth bet). Partially repaired 2026-08-05 — see `../PRD_REVIEW.md` §G |

> Note: the existing single-user product PRD remains at repo-root `PRD.md`. This SaaS PRD supersedes it for the hosted product; the root PRD stays authoritative for the local/CLI domain model.

---

## 14. Open Questions

- Waitlist target number that gates Phase 1 investment — GTM plan proposes ≥250 @ ≥3% conversion; founder to ratify.
- Exact price points and trial policy — Pricing doc leans **14-day no-card Pro trial** (§4.2 there); dollar figures remain PLACEHOLDER.
- What happens to the founder's live chat gateways (WhatsApp/Telegram) during the re-platform — keep running against local mode until the tenant-aware versions ship?
- Do we keep a first-class **local/self-hosted** edition long-term, or is hosted the only future?
- ~~Data residency / region for Postgres at launch?~~ **Resolved 2026-07-31:** Fly.io,
  single region, US-first unless an EU customer appears — [`MULTITENANCY.md`](../architecture/MULTITENANCY.md) §11.5.
  Still open underneath it: **managed vs. unmanaged Postgres** and the RPO/RTO targets (§11.1).
- How aggressively to pursue the Vendor Discovery marketplace vs. core SaaS depth?
