# MiHomes — Go-To-Market & Launch Plan

**Purpose:** The single source of truth for how MiHomes goes to market — positioning, the promotional landing page, waitlist mechanics, domain setup, and the phased launch from waitlist to public GA.

**Status: Draft — 2026-07-27**

Related docs:
- `PRICING_AND_PACKAGING.md` — plan tiers, prices, seat/home limits (this doc references, does not duplicate)
- `../architecture/BILLING_AND_EMAIL.md` — Stripe billing + Resend email/DNS records

---

## 1. Positioning & Messaging

### One-line value prop
**MiHomes is the AI-first command center for running every home you own — one place for your properties, staff, vendors, tasks, and money.**

### Elevator pitch
Owning more than one home should feel like a privilege, not a second job. Today it's the opposite: maintenance runs over text threads, the cleaner's schedule lives in someone's head, vendor invoices pile up in email, and the "system" is three spreadsheets and a good memory. MiHomes replaces that chaos with a single, AI-first command center. Add your homes, invite your team, and let an AI estate manager watch every property — surfacing what needs attention, in what order, and why. It's the difference between reacting to problems and running your homes like an operation.

### Who it's for (ICP)
| Segment | Description | Why they buy |
|---|---|---|
| **Multi-home owners** | 2–6 properties, no full-time staff, DIY coordination | Stop juggling texts/spreadsheets; one dashboard across homes |
| **Families with household staff** | Cleaners, nannies, groundskeepers, handymen on rotation | Coordinate a team, assign tasks, keep accountability |
| **Family offices / estate managers** | Manage homes on behalf of principals; report upward | Professional-grade oversight, delegation, audit trail |

Primary beachhead: **multi-home owners with 1–3 recurring staff** — enough pain to pay, small enough to self-serve onboard.

### Against the alternatives (the differentiation line)
Consumer home-maintenance apps are single-home and have no concept of a team. Landlord/property-management suites are tenant-side, per-door priced, and heavyweight. Spreadsheets don't prioritize. MiHomes is the only **owner-side, multi-home, team-aware, AI-first** option — say this explicitly on the page; "command center" alone is not a differentiator.

### The core pain → the promise
- **Pain:** Running multiple homes is chaos spread across texts, spreadsheets, email, and memory. Nothing is in one place; nothing is prioritized; things fall through the cracks (a lapsed inspection, an unpaid vendor, a safety issue nobody flagged).
- **Promise:** One AI-first command center that knows every home, coordinates every person, and tells you what matters most — safety first.

### Tagline (proposed + alternates)
- **Primary:** *Every home, under control.*
- Alt 1: *Run your homes like an operation.*
- Alt 2: *One command center for every home you own.*
- Alt 3: *Your AI estate manager, on duty 24/7.*

### Messaging pillars
1. **An AI estate manager, always on.** Not another to-do app — an advisor that reviews every home and tells you what needs attention and why.
2. **One place for every home.** Properties, tasks, issues, vendors, inventory, and finances — unified, not scattered.
3. **Invite your team.** Bring staff and vendors into shared, role-scoped workspaces. Delegation with accountability.
4. **Safety-first prioritization (SPACE).** MiHomes ranks what matters using the SPACE framework — **S**afety, **P**resence, **A**sset protection, **C**ompliance, **E**conomy — so the important things never lose to the loud things.

**Tone:** calm, competent, understated. We sell control and peace of mind, not hype. Avoid "revolutionary," "game-changing," exclamation marks, and stock-photo cheer.

---

## 2. Promotional Landing Page — Section-by-Section Spec

Design constraints: **responsive (mobile-first), fast (<1.5s LCP on 4G), no heavy JS.** Single scroll page. Every CTA points to **Google sign-in → waitlist join**. Domain served from **mihomes.ai** (apex, marketing).

```
+==========================================================+
|  NAV                                                     |
|  [MiHomes logo]        Features  Pricing  FAQ   [Join →] |
+==========================================================+
```
Copy direction: sticky, minimal nav. Single primary CTA button ("Join the waitlist") reused everywhere.

### 2.1 Hero
```
+----------------------------------------------------------+
|                                                          |
|   Every home, under control.                             |
|                                                          |
|   The AI-first command center for multi-home owners.     |
|   Your properties, staff, vendors, and money — in one    |
|   place, prioritized by an AI estate manager.            |
|                                                          |
|   [ Continue with Google ]   [ See how it works ↓ ]      |
|                                                          |
|   [ hero visual: unified dashboard across 3 homes ]      |
+----------------------------------------------------------+
```
- **Headline:** the tagline. **Subhead:** the value prop in one sentence naming the ICP.
- **Primary CTA:** "Continue with Google" (leads to Google sign-in, then waitlist join). **Secondary:** anchor scroll to "How it works." Keep a plain email field one click away (§2.8) — a Google-only hero risks losing the privacy-cautious slice of this ICP; A/B "Join the waitlist" (email-first) vs Google-first once traffic allows.
- **Hero visual:** a clean product mock — one screen showing multiple homes with an AI "priorities" panel down the side (SPACE-ranked). Static image/SVG, no video. Show real UI direction, not abstract art.

### 2.2 Social proof (placeholder)
```
+----------------------------------------------------------+
|  "Trusted by owners managing homes across __ properties" |
|  [ logo ] [ logo ] [ logo ]   ★★★★★  early-access quotes |
+----------------------------------------------------------+
```
Pre-launch: swap logos for a subtle **"Built by people who've managed estates"** credibility line + a signup counter ("Join 300+ on the waitlist") once numbers justify it. Never fabricate testimonials — use real early-access quotes or leave it out.

### 2.3 The problem
```
+----------------------------------------------------------+
|  Running multiple homes is chaos.                        |
|                                                          |
|  • Maintenance lives in text threads                     |
|  • The schedule is in someone's head                     |
|  • Invoices pile up in email                             |
|  • Your "system" is three spreadsheets                   |
|                                                          |
|  Something always falls through the cracks.              |
+----------------------------------------------------------+
```
Copy direction: name the pain in the reader's own words. Short, punchy, empathetic — no product yet.

### 2.4 How it works (3 steps)
```
+------------------+  +------------------+  +------------------+
| 1. Add your homes|  | 2. Invite your   |  | 3. Let AI run    |
|                  |  |    team          |  |    point         |
| Properties,      |  | Staff & vendors, |  | Your AI estate   |
| rooms, inventory |  | role-scoped      |  | manager ranks    |
| in minutes.      |  | access.          |  | what matters.    |
+------------------+  +------------------+  +------------------+
```
Copy direction: three verbs — Add, Invite, Relax/Run. Each with a one-line outcome. Icons, not screenshots.

### 2.5 Feature highlights (tie to real features)
```
+----------------------------------------------------------+
|  FEATURE GRID (2 cols desktop / 1 col mobile)            |
+----------------------------------------------------------+
| 🏠 Properties      | ✅ Tasks & Issues                   |
|  One profile per   |  Track work and problems to         |
|  home; rooms,      |  resolution, per home.              |
|  systems, docs.    |                                     |
+--------------------+-------------------------------------+
| 👥 Staff coord.    | 🔧 Vendors                          |
|  Assign, schedule, |  Contacts, jobs, and history in     |
|  hold accountable. |  one rolodex.                       |
+--------------------+-------------------------------------+
| 💰 Finances        | 🤖 AI Estate Manager                |
|  Costs & vendor    |  SPACE-ranked priorities and        |
|  spend per home.   |  plain-language advice.             |
+--------------------+-------------------------------------+
| 💬 Message intake (WhatsApp / Telegram)                  |
|  Forward a text — MiHomes turns it into a task or issue. |
+----------------------------------------------------------+
```
Copy direction: feature name + one benefit line each. The **AI Estate Manager** card gets slight visual emphasis — it's the differentiator. **Caution on the chat-intake card:** per SAAS_PRD.md §6.2, expanded Telegram and the Twilio/WhatsApp Business gateways are *post-GA growth bets*, and Baileys WhatsApp pairing is currently broken — either label the card "coming soon" or drop WhatsApp and show Telegram only. Keep to real, shipped-or-planned capabilities; no vaporware.

### 2.6 Pricing teaser
```
+----------------+  +----------------+  +----------------+
|     FREE       |  |      PRO       |  |     ESTATE     |
|                |  |   ★ popular     |  |                |
| 1 home         |  | Multi-home     |  | Everything in  |
| 3 seats        |  | + staff/team   |  | Pro, at scale  |
| Free forever   |  | seats          |  | for family     |
|                |  |                |  | offices        |
| [Get started]  |  | [Join waitlist]|  | [Talk to us]   |
+----------------+  +----------------+  +----------------+
        See full pricing →  (PRICING_AND_PACKAGING.md)
```
Copy direction: three cards, **Free / Pro / Estate**. Free = **1 home + 3 seats, free forever** (a household on us). Pro/Estate = multi-home + staff/team. **Do not hardcode dollar amounts here** — prices are PLACEHOLDER until validated (`PRICING_AND_PACKAGING.md`); show the shape of the plans and link out for exact numbers. Mark Pro as the default/"popular" choice. Note: in Phase 0 there is no product to "get started" with — all three CTAs resolve to the waitlist (Estate's may open a contact/"talk to us" form); swap in real CTAs at GA.

### 2.7 FAQ
```
+----------------------------------------------------------+
|  ▸ Do I need technical skills?         (No.)             |
|  ▸ Can I invite my cleaner / handyman? (Yes — role-based)|
|  ▸ Is my data private?                 (Yes — per-tenant)|
|  ▸ What does the AI actually do?       (SPACE priorities)|
|  ▸ How do I sign in?                   (Google)          |
|  ▸ When does it launch?                (Waitlist now)    |
|  ▸ Is there a free plan?               (Yes, forever)    |
+----------------------------------------------------------+
```
Copy direction: accordion, 6–8 items, answer the buying objections (privacy, ease, price, timing).

### 2.8 Waitlist / sign-up CTA (closing)
```
+----------------------------------------------------------+
|  Get early access.                                       |
|  Join the waitlist and lock in a founding-member offer.  |
|                                                          |
|  [ email________________ ]  [ Join the waitlist ]        |
|  How many homes? (1 / 2–3 / 4+)   Do you have staff? Y/N |
|                                                          |
|  We'll email you when your spot opens. No spam.          |
+----------------------------------------------------------+
```
Copy direction: repeat the primary conversion. Email required; the two light-qualification fields optional (see §3). Reassure on spam. Google button as an alternate one-click path.

### 2.9 Footer
```
+----------------------------------------------------------+
|  MiHomes            Product · Pricing · FAQ · Contact    |
|  © 2026 MiHomes     Privacy · Terms      mihomes.ai      |
+----------------------------------------------------------+
```
Copy direction: minimal. Include Privacy/Terms links (required before collecting emails), contact, and social if any.

---

## 3. Waitlist Mechanics

### Capture
- **Required:** email address.
- **Optional (light qualification):** number of homes (`1 / 2–3 / 4+`), has household staff (`yes/no`). One extra click max — never gate the signup behind them.
- Google sign-in path pre-fills email (name + verified address) with zero typing.

### Store
- A `waitlist` table (see §4). One row per email; upsert on repeat.

### Confirm (Resend)
- On signup, send a **double opt-in / confirmation email via Resend** ("You're on the list — here's what's next"). Sets expectations on timing and the founding offer. See `../architecture/BILLING_AND_EMAIL.md` for the Resend setup and templates.

### Position & referral (future-optional)
- Store a queue position; optionally show it ("You're #312"). A **referral bump** ("move up 20 spots per friend") is a *Phase 4* nice-to-have — do not build for launch; leave a `referred_by` column so it's cheap to add later.

### Founding-member offer (propose)
- **Founder early-bird:** waitlist members who convert in the first cohort get **one of**: (a) an extended free trial (e.g. 60 days of Pro), or (b) a founding discount on the first annual plan (e.g. 30% off year one), locked for life while active. Pick one and state exact terms in `PRICING_AND_PACKAGING.md`; the landing page just promises "a founding-member offer."

### Data to collect for later segmentation
| Field | Use |
|---|---|
| email | contact, dedupe, identity |
| name (from Google) | personalization |
| num_homes | ICP tiering, Pro vs Estate targeting |
| has_staff | feature messaging (team/RBAC) |
| source / utm | channel attribution |
| referred_by | future referral program |
| created_at | cohorting, queue order |
| confirmed_at | opt-in status, deliverability |

---

## 4. Tech Approach for Phase 0 (ship fast)

The landing page must **not** block on the full multitenant build. Two options:

| Option | Pros | Cons |
|---|---|---|
| **A. Route in the existing FastAPI + htmx + Jinja app** | Reuses stack, one deploy, one repo, waitlist table lives with future app DB, Google sign-in wiring is reused later | Couples marketing uptime to app; app churn risks the landing |
| **B. Dedicated static marketing site** (static HTML/SSG on CDN) | Fastest possible, cheap, independent uptime, easy to hand to a designer | Separate deploy; waitlist needs its own endpoint/DB or a form service; duplicate auth stub |

**Recommendation: Option A.** Add a public, unauthenticated landing route to the existing FastAPI + Jinja app, plus:
- a `GET /` marketing page (Jinja template, inlined critical CSS, one static hero asset),
- a `POST /waitlist` endpoint writing to the `waitlist` table,
- a **Google sign-in stub** (OAuth "Continue with Google" that, in Phase 0, just captures the verified email into the waitlist — full session/tenant wiring comes in Phase 1).

Rationale: the stack is already FastAPI + htmx + Jinja, the waitlist table is the seed of the real DB, and the Google OAuth work is reused verbatim when auth goes live. Keep it lightweight — static hero image, no SPA, aggressive caching, deploy behind the CDN/TLS at the apex. If marketing-site iteration speed later becomes a bottleneck, split to Option B; the waitlist API stays put.

**Deployment caution (Option A):** the existing web app is the **single-user product with 23 route modules and no authentication** (SAAS_PRD.md §4). The Phase 0 deploy must be a *stripped instance* mounting only the landing, waitlist, and OAuth-stub routes against a fresh DB — never the full single-user UI exposed publicly. If stripping the app is more work than a static page, Option B quietly becomes the faster path; re-evaluate at build time.

`waitlist` table (minimal):
```
waitlist(
  id, email UNIQUE, name, num_homes, has_staff,
  source, utm_campaign, referred_by,
  created_at, confirmed_at
)
```

---

## 5. DNS & Domain Setup — mihomes.ai

Marketing on the **apex** (`mihomes.ai`), app on **`app.mihomes.ai`**, transactional email on the dedicated sending subdomain **`send.mihomes.ai`** (per `../architecture/BILLING_AND_EMAIL.md` §3 — that doc is authoritative for the email records; do not fabricate values, they come from Resend at domain verification).

| # | Record | Host | Type | Points to | Notes |
|---|---|---|---|---|---|
| 1 | Apex → marketing | `mihomes.ai` | A / ALIAS | CDN / host IP | ALIAS/ANAME if host supports apex flattening |
| 2 | www redirect | `www.mihomes.ai` | CNAME | `mihomes.ai` | 301 www → apex |
| 3 | App subdomain | `app.mihomes.ai` | CNAME/A | app host | the authenticated SaaS (Phase 1+) |
| 4 | TLS | all hosts | — | — | auto-provision (Let's Encrypt / host-managed); force HTTPS + HSTS |
| 5 | SPF | `send.mihomes.ai` | TXT | `v=spf1 include:<resend-provided> ~all` | exact include from Resend; on the **sending subdomain**, not the apex |
| 6 | DKIM | Resend selector on `send.mihomes.ai` | CNAME/TXT | Resend keys | per BILLING_AND_EMAIL.md §3 |
| 7 | MX (bounce) | `send.mihomes.ai` | MX | Resend | return-path/bounce handling, provided by Resend |
| 8 | DMARC | `_dmarc.mihomes.ai` | TXT | `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai` | start `p=none`, tighten to `quarantine` → `reject` after alignment is clean |
| 9 | CAA (optional) | `mihomes.ai` | CAA | issuer | restrict cert issuance |

**Checklist:**
- [ ] Apex resolves to marketing landing over HTTPS
- [ ] `www` 301-redirects to apex
- [ ] `app.mihomes.ai` reserved and routable (can 503/placeholder until Phase 1)
- [ ] TLS valid on apex, www, and app; HTTP→HTTPS + HSTS
- [ ] `send.mihomes.ai` verified in Resend; SPF, DKIM, MX present and passing
- [ ] Send a test waitlist confirmation from `no-reply@send.mihomes.ai`; confirm inbox placement (not spam)
- [ ] DMARC starts at `p=none`, review reports, move to `quarantine` (then `reject`)

---

## 6. Launch Timeline & Milestones

Durations are **ESTIMATES — founder-led, not commitments**; cumulative best case is ~3 months and slippage on Phase 1 is the most likely (SAAS_PRD.md §4 calls it a re-platform: SQLite→Postgres, `account_id` on all 36 tables, RLS — 3–5 weeks is aggressive, treat it as the optimistic bound). Each phase has an exit metric that gates the next. The ≥250 signup gate below is this doc's **proposal**; SAAS_PRD.md §8.1 leaves the number to the founder — confirm and record it here.

| Phase | Scope | Est. duration | Entry criteria | Exit criteria (gate) |
|---|---|---|---|---|
| **Phase 0** | Promo landing + waitlist + Google sign-in | 1–2 wks | Domain live, copy approved | **≥ 250 confirmed (double-opted-in) signups** & ≥ 3% landing→waitlist conversion over a trailing 2-week window with ≥ 500 sessions |
| **Phase 1** | Multitenant foundation (tenants, data isolation) | 3–5 wks | Phase 0 gate met; demand validated | First tenant can be created + isolated; internal dogfood |
| **Phase 2** | Onboarding + staff invites + RBAC | 3–4 wks | Phase 1 stable | A new owner self-onboards a home + invites 1 staff, role-scoped |
| **Phase 3** | Billing / freemium (Stripe) | 2–3 wks | Onboarding works end-to-end | A user upgrades Free→Pro via Stripe; webhooks reconcile |
| **Phase 4** | Polish + email lifecycle + GA public launch | 2–4 wks | Paid flow proven | Public launch; waitlist drained into product; lifecycle emails live |

```
NOW ──▶ P0 landing+waitlist ──(≥250 signups)──▶ P1 multitenant ──▶
        P2 onboarding+RBAC ──▶ P3 Stripe billing ──▶ P4 GA public launch
```

Rule (per CLAUDE.md workflow): if a phase goes sideways, **stop and re-plan** — don't push a broken gate forward.

---

## 7. Launch Channels & Tactics

Low-budget, founder-led first. Sequence: warm network → communities → content/SEO → paid/partnerships.

| Channel | Tactic | Why it fits the ICP |
|---|---|---|
| **Warm network** | Direct outreach to known multi-home owners / estate managers | Highest-trust first cohort; real feedback |
| **Communities** | Family-office forums, r/fatFIRE, HOA & property-owner groups, high-net-worth/expat groups, estate-manager associations (e.g. DEMA/UKAHMA-type) | Where owners and managers already talk shop |
| **Product Hunt** | Launch at GA (Phase 4), not before | Reach + credibility; needs a working product |
| **Content / SEO** | "How to manage multiple homes," "hiring & coordinating household staff," "second-home maintenance checklist," "family office home ops" | Captures high-intent search; evergreen; cheap |
| **Referrals** | Founding members refer peers (waitlist bump later) | Owners know other owners; trusted intros |
| **Partnerships** | Property managers, luxury real-estate agents, relocation/concierge services, high-end home-service vendors | They sit next to the buyer at the moment of need |

Founder motion: publish 1 useful article/week, personally onboard the first ~20–50 signups, collect quotes → feed §2.2 social proof.

---

## 8. Success Metrics / KPIs

Targets are **illustrative** ranges to steer, not commitments — recalibrate against real data.

| Funnel stage | Metric | Illustrative target |
|---|---|---|
| Awareness | Landing sessions | grow week-over-week |
| **Waitlist** | Landing → waitlist conversion | **3–6%** |
| Waitlist health | Confirmation (double opt-in) rate | > 70% |
| Phase 0 gate | **Confirmed** signups before build | **≥ 250** (proposed; founder to ratify — SAAS_PRD.md §8.1) |
| Activation | Invited users who create their **1st home** | **> 40%** |
| Activation depth | New owners who invite ≥ 1 teammate | > 25% |
| Monetization | Free → paid conversion | **3–8%** |
| Retention | Month-1 logo retention (paid) | **> 85%** |
| Retention | Weekly active / signed-up | trend upward |

Instrument from day one: UTM on every link, event on waitlist submit + confirm, cohort by `num_homes` / `has_staff`.

---

## 9. Open Questions & Risks

**Open questions**
- Founding offer: extended trial **or** annual discount? (Decide in `PRICING_AND_PACKAGING.md`.)
- Show queue position publicly, or keep the waitlist opaque?
- Marketing on apex vs. app on apex — confirm apex = marketing (assumed here).
- Do we need Terms/Privacy counsel-reviewed before collecting emails? (Likely yes.)
- Estate tier: self-serve or sales-assisted ("Talk to us")? Assumed sales-assisted at launch.

**Risks**
| Risk | Impact | Mitigation |
|---|---|---|
| Weak waitlist signal | Build the wrong thing | Hard Phase 0 gate (≥250) before heavy build |
| Landing couples to app uptime (Option A) | Marketing down = signups lost | Cache/CDN the landing; keep route dependency-light; fallback to static |
| Email deliverability (spam folder) | Lost confirmations | Full SPF/DKIM/DMARC; warm domain; test inboxing |
| Privacy/compliance on staff data | Legal exposure | Per-tenant isolation, RBAC (Phase 2), reviewed Terms/Privacy |
| Google-only sign-in excludes some users | Lost signups | Email capture always available as fallback |
| Small, hard-to-reach ICP | Slow top-of-funnel | Founder-led warm outreach + SEO before paid |
| AI advice quality (SPACE) underwhelms | Churn, trust loss | Keep advice explainable; safety-first; human-in-the-loop |

---

*End of GTM Launch Plan (Draft). Update the status line and Phase 0 gate metric as real numbers arrive.*
