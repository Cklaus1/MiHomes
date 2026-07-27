# MiHomes — Product & Launch Documentation

> The document set for turning **MiHomes** from a single-user local tool into a multi-tenant SaaS at **mihomes.ai**.
> **Status:** Draft — 2026-07-27 · **Owner:** Chris Klaus (Founder/PM)

---

## Start here

**[SAAS_PRD.md](SAAS_PRD.md)** is the master PRD and the anchor for everything else. Read it first — it defines the vision, scope, locked decisions, phases, and how the other docs fit together.

---

## The doc set

### Product (`docs/product/`)
| Doc | What it answers |
|---|---|
| **[SAAS_PRD.md](SAAS_PRD.md)** | The whole picture: vision, scope, phases 0–4, success metrics, risks |
| **[PRICING_AND_PACKAGING.md](PRICING_AND_PACKAGING.md)** | Free / Pro / Estate plans, limits, entitlements, upgrade & downgrade behavior |
| **[ONBOARDING_AUTH_RBAC.md](ONBOARDING_AUTH_RBAC.md)** | Google sign-in, first-run onboarding, staff invites, owner/admin/staff roles |
| **[GTM_LAUNCH_PLAN.md](GTM_LAUNCH_PLAN.md)** | Positioning, the promo landing page, waitlist, DNS/email, launch timeline |
| **[TELEGRAM_PRD.md](TELEGRAM_PRD.md)** | Current Telegram gateway analysis + capability roadmap (growth bet) |
| **[TWILIO_PRD.md](TWILIO_PRD.md)** | New Twilio gateway: SMS/MMS/Voice + official WhatsApp Business (growth bet) |
| **[VENDOR_DISCOVERY_PRD.md](VENDOR_DISCOVERY_PRD.md)** | AI vendor research + public star ratings marketplace (growth bet) |

### Architecture (`docs/architecture/`)
| Doc | What it answers |
|---|---|
| **[MULTITENANCY.md](../architecture/MULTITENANCY.md)** | Shared Postgres, accounts/users/memberships, tenant scoping, RLS, SQLite→PG migration |
| **[BILLING_AND_EMAIL.md](../architecture/BILLING_AND_EMAIL.md)** | Stripe Billing + Resend, both behind internal provider interfaces |

---

## Locked decisions (canon)

Every doc in this set is written against these. If one changes, update the master PRD first, then ripple out.

| Area | Decision |
|---|---|
| **Domain** | mihomes.ai (marketing on apex, app on `app.mihomes.ai`, email on `send.mihomes.ai`) |
| **Tenancy** | Shared PostgreSQL, `account_id` on every tenant table, query-scoped, RLS backstop |
| **Identity** | `accounts` (tenant) ← `memberships` → `users` (global); a user can join many accounts |
| **Auth** | Google OAuth (OIDC) only at launch, keyed on Google `sub` |
| **Roles** | owner (billing) / admin (ops) / staff (scoped external help) |
| **Pricing** | Free (1 home + 3 seats, no staff invites) / Pro / Estate |
| **Upgrade trigger** | 2nd home, 4th seat, or inviting external staff |
| **Payments** | Stripe Billing behind `BillingProvider` |
| **Email** | Resend behind `EmailProvider` (failover to Postmark/SES) |
| **Launch** | Landing + waitlist first, then the multi-tenant MVP |

---

## Phases (canon across all docs)

| Phase | Name | Delivers |
|---|---|---|
| **0** | Landing + Waitlist | Promo page, waitlist + confirmation email, Google sign-in **stub** (email capture only), DNS/email setup |
| **1** | Multitenant Foundation | Postgres, accounts/users/memberships, tenant scoping + RLS, auth, sessions |
| **2** | Onboarding + Team + RBAC | Onboarding flow, staff invites, role enforcement, account switcher |
| **3** | Billing / Freemium | Stripe Checkout + Portal, webhooks, entitlements service, plan gates |
| **4** | Polish + Email + GA | Full email lifecycle, dunning, hardening, public launch |
| **4+** | Growth bets | Vendor Discovery, Twilio gateway, expanded Telegram |

Each phase gates the next; exit criteria live in the master PRD §10 and duration estimates in [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) §6. The MVP cut line and the GA definition of done are defined in the master PRD §10.

---

## How these relate to the existing repo

- The **root [`PRD.md`](../../PRD.md)** remains authoritative for the *single-user / CLI* product and the domain model. This set describes the *hosted SaaS* built on top of it.
- **Growth-bet PRDs** (Telegram, Twilio, Vendor Discovery) are grounded in real code that already exists (`src/mihomes/services/gateways/`, `src/mihomes/models/vendor*.py`, `src/mihomes/services/ai/`) — they extend, not replace.
- All docs are **Draft** — dollar figures and quantities marked `PLACEHOLDER` need validation before launch.
