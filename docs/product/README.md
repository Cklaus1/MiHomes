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
| **[OMNICHANNEL_GATEWAY_PRD.md](OMNICHANNEL_GATEWAY_PRD.md)** | Unifying WhatsApp/Telegram/Twilio behind one core (growth bet). **Partially repaired 2026-08-05** — see below |
| **[WHATSAPP_GATEWAY_PRD.md](WHATSAPP_GATEWAY_PRD.md)** | WhatsApp Business Cloud API gateway (growth bet). **Partially repaired 2026-08-05** — see below |

> **Read the two gateway PRDs with care.** They were added after the other ten and, unlike them,
> made factual claims about the existing code that did not hold — catalogued in
> [`../PRD_REVIEW.md`](../PRD_REVIEW.md) §G. The load-bearing errors were corrected on 2026-08-05
> (`docs/specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md` §2), and each correction is marked
> inline in the document it fixes. Sections not explicitly corrected have **not** been re-verified.
> The buildable statement of this work is SPEC-006, not these two documents.

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
| **Hosting** | Fly.io, single region; S3-compatible object storage for uploads (not Fly volumes) |
| **Launch** | Landing + waitlist first, then the multi-tenant MVP |

---

## Phases (canon across all docs)

| Phase | Name | Delivers |
|---|---|---|
| **0** | Landing + Waitlist | Promo page, waitlist + confirmation email, Google sign-in **stub** (email capture only), DNS/email setup |
| **1** | Multitenant Foundation | Postgres, accounts/users/memberships, tenant scoping + RLS, auth, sessions |
| **2** | Onboarding + Team + RBAC | Onboarding flow, staff invites, role enforcement, account switcher, **entitlements service (config-only — all accounts Free)** |
| **3** | Billing / Freemium | Stripe Checkout + Portal, webhooks, **billing status wired into entitlements**, plan gates |
| **4** | Polish + Email + GA | Full email lifecycle, dunning, hardening, public launch |
| **4+** | Growth bets | Vendor Discovery, Twilio gateway, expanded Telegram |

Each phase gates the next; exit criteria live in the master PRD §10 and duration estimates in [`GTM_LAUNCH_PLAN.md`](GTM_LAUNCH_PLAN.md) §6. The MVP cut line and the GA definition of done are defined in the master PRD §10.

---

## How these relate to the existing repo

- The **root [`PRD.md`](../../PRD.md)** remains authoritative for the *single-user / CLI* product and the domain model. This set describes the *hosted SaaS* built on top of it.
- **Growth-bet PRDs** (Telegram, Twilio, Vendor Discovery) are grounded in real code that already exists (`src/mihomes/services/gateways/`, `src/mihomes/models/vendor*.py`, `src/mihomes/services/ai/`) — they extend, not replace.
- All docs are **Draft** — dollar figures and quantities marked `PLACEHOLDER` need validation before launch.
