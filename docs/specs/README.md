# MiHomes Implementation Specs

Executable specs for the SaaS re-platform. Each spec turns one phase of the PRD set into
something a developer (or an AI agent) can build **without asking a question**.

**Status:** The **launch path is complete** — SPEC-001 through SPEC-005 cover Phases 0–4. Phase 3
is the MVP cut line; Phase 4 is GA. SPEC-006 begins the **Phase 4+ growth bets**, which are
post-GA and gate nothing above them.

---

## The spec set

| Spec | Phase | Status |
|---|---|---|
| [SPEC-001](SPEC-001-phase0-landing-waitlist.md) | **0** — Landing + waitlist | Ready to build |
| [SPEC-002](SPEC-002-phase1-multitenant-foundation.md) | **1** — Multitenant foundation | Ready to build — **no open decisions** |
| [SPEC-003](SPEC-003-phase2-onboarding-team-rbac.md) | **2** — Onboarding + team + RBAC | Ready to build — **1 open decision** (O1: secret encryption) |
| [SPEC-004](SPEC-004-phase3-billing-freemium.md) | **3** — Billing / freemium | Ready to build — **1 open decision** (O1: launch prices/limits, blocks config only) |
| [SPEC-005](SPEC-005-phase4-polish-email-ga.md) | **4** — Polish + email lifecycle + GA | Ready to build — **2 open decisions** (O1: drip content/cadence; O2: deletion grace length) |
| [SPEC-006](SPEC-006-gateways-tenancy-webhook-cloud-api.md) | **4+** — Gateways: tenancy, webhook, Cloud API | Ready to build — **2 open decisions** (O1: Cloud API tier/groups; O2: webhook host locally). **Verified against `origin/main`, not `telegram-bot`** — see its §0.1 |

**Five unrelated `O1`s are open across the set.** Label namespaces are per-spec-local (see
*Working on a spec* below), so the numbering restarts in every spec. SPEC-001's O1 is the ToS +
Privacy Policy; SPEC-003's O1 is at-rest encryption of provider API keys; SPEC-004's O1 is the
launch prices and limits; SPEC-005's O1 is the drip sequence; SPEC-006's O1 is the WhatsApp Cloud
API tier. SPEC-002's O1 **closed** on 2026-07-31 (→ D13). Always resolve an `O`-label inside the
spec that raised it.

**Three of those gate GA rather than a build** — SPEC-001 O1, SPEC-003 O1 and SPEC-004 O1 are all
carried in SPEC-005 §1.6 under their original labels, because `SAAS_PRD:189-196` cannot be
satisfied until their owners decide. SPEC-005 §8's A33 asserts they are visibly tracked, not that
they are resolved.

**Locked across the set:** hosting is Fly.io, single region, on **managed Postgres**
(`../architecture/MULTITENANCY.md` §11, §11.1). The CLI is an **operator tool, not a user
interface** — local SQLite mode is dropped and the CLI becomes an admin client against hosted
Postgres (SPEC-002 D1). Primary keys are UUIDv7, app-side, no DB-side default (SPEC-001 §4.1,
reused by SPEC-002). Uploads go to S3-compatible object storage behind a `StorageProvider`
Protocol — never a Fly volume, which is single-machine local disk. The `EmailProvider` Protocol is
**transport-only** — it ships in Phase 0 and is reused, never rebuilt (SPEC-001 §5.1); SPEC-005
D11 makes the set's only widening of it, one additive `headers` kwarg for RFC 8058 unsubscribe,
and explains at length why that is not a violation.

**"Locked" means decided, not built.** None of the above exists in the tree: `config.py:14` still
hardcodes `DB_URL = f"sqlite:///{DB_PATH}"`, and no Postgres driver is installed on any branch
(verified 2026-08-04). Every spec here targets the decided architecture, so a spec that cites
Postgres, RLS, or `account_id` is describing SPEC-002's design. SPEC-004 §0.1 states the
consequence: **divergence compounds** — if SPEC-002 is implemented differently than specified,
every spec above it inherits the difference.

Phase numbering is canon across the whole doc set — see `../product/SAAS_PRD.md` §10.

---

## How these relate to the PRDs

The PRDs say **what and why**. The specs say **exactly what to build, in what order, and how
you know it worked**.

```
docs/product/*.md          product intent, locked decisions, open questions
docs/architecture/*.md     system design, schemas, provider contracts
docs/PRD_REVIEW.md         cross-document review — contradictions found before speccing
        │
        ▼
docs/specs/SPEC-NNN.md     buildable: file paths, real schemas, signatures, tests
```

A spec never re-derives product intent — it cites the PRD section and moves on. Where the PRDs
**contradict each other**, the spec picks one, says why, and lists the resulting PRD edit in its
*Doc-fix prerequisites* section. `docs/PRD_REVIEW.md` is the catalogue of those contradictions
(A0–A6, B1–B4, C, E) and is the input to every spec.

---

## Anatomy of a spec

Nine sections, in this order. The order matters: decisions before schemas, schemas before
steps, steps before tests.

| # | Section | Purpose |
|---|---|---|
| 1 | **Decisions** | Choices this phase depends on, with rationale |
| 2 | **Doc-fix prerequisites** | PRD contradictions this phase would otherwise inherit |
| 3 | **File manifest** | Exact paths, new vs. modified |
| 4 | **Schemas as code** | Real SQLAlchemy / Alembic source — never prose |
| 5 | **Function signatures** | Real Python signatures, so call sites are unambiguous |
| 6 | **Sequenced steps** | Each independently verifiable and committable |
| 7 | **Non-goals + deferred scope** | Likely wrong turns, named |
| 8 | **Acceptance criteria** | Each paired with the test that proves it |
| 9 | **Test manifest** | File path per test, and what it asserts |

Sections 4, 5 and 7 are what make a spec *codeable* rather than merely readable. A prose
description of a schema leaves room for interpretation; a column definition does not. And §7
earns its place because most defects come from a plausible-but-wrong choice, not from a missing
instruction.

---

## The two kinds of TBD

Both belong in a spec. They call for different action, so they are labelled differently and
never left bare:

**`DEFERRED (Phase N)`** — future scope. This phase deliberately does not build it, and the
*interface* is already settled so nothing has to change later. It tells you what shape to leave
room for.

> Example: `../architecture/BILLING_AND_EMAIL.md` §8 defers metered AI billing, "captured so
> the `BillingProvider` interface can grow a `report_usage` method without disrupting callers."

**`OPEN — needs decision: <owner>`** — a genuine gate. Someone must decide before the affected
line can be written. Always names **who decides** and **what it blocks**, so the rest of the
phase can proceed around it.

> Cautionary example: `../architecture/MULTITENANCY.md` §5.3 step 3 says "remap PKs *per the PK
> decision in §10.1*" — while §10.1 still lists that decision as open. A forward reference to
> nothing. That is the shape to avoid: not the uncertainty, but leaving it **untagged**.

An untagged ambiguity is the only thing that fails review. Uncertainty that announces itself is
fine.

---

## Phases 2–4 were written ahead of Phase 1's outcome — deliberately

This section previously argued that Phases 2–4 should stay unwritten until Phase 1 shipped,
because "speccing the phases that sit on top of it before it exists means writing rework." The
override condition it named was to read Phase 1's *outcome* first.

**SPEC-003 was written anyway, by founder decision (2026-08-03), with no Phase 1 outcome to
read.** Phase 1 is spec-only: `account_id` appears zero times in any `.py` file on any branch,
the DB is still hardcoded SQLite, and there is no Postgres driver installed. The reasoning is
recorded here rather than quietly dropped, because it changes how SPEC-003 should be read:

- SPEC-003 §0.1 states the assumption explicitly. Every reference in it to `account_id`,
  `TenantOwned`, `memberships`, or the scoped session describes **SPEC-002's design, not code**.
- **If SPEC-002's implementation diverges from its spec, SPEC-003 inherits the divergence.**
  Re-verify its §4 and §5 against the tree before building.
- The prediction was half right. Writing SPEC-003 surfaced three PRD conflicts that are exactly
  the rework this section warned about — `membership_home_scopes` vs `membership_property_scopes`,
  `accounts.owner_user_id` vs the partial unique index, and entitlements assigned to three
  different phases. They were fixed in the doc layer (SPEC-003 §2, B1–B12) instead of being
  discovered mid-implementation, which is cheaper than either alternative.
- It was also half wrong in a more useful way. Claim 2 below — that the Phase 2 surface was
  "already well specified" — did not survive contact. The capability matrix turned out to have
  no machine-readable action keys despite §9.4 instructing implementers to look actions up in it;
  the vendor rule contradicted itself between §9.2 and §9.3; documents were left `scoped` with
  nothing to scope by; and money fields sit inside rows staff are permitted to see, which no PRD
  addresses at all. Six founder decisions (SPEC-003 D12–D17) were needed to close those gaps.

**The original reasoning, preserved:**

1. **Phase 1 will teach us things Phase 2–4 specs would have to absorb.** The tenant-scoping
   layer — the `TenantOwned` mixin, the `with_loader_criteria` hook, RLS behaviour under
   PgBouncer — is the load-bearing part of the whole re-platform. SPEC-002 §7 already lists what
   Phase 2 inherits (`require_permission`, the entitlements service, the per-tenant config UI)
   as `DEFERRED` items with their interface room reserved.
2. **The Phase 2–4 surface is already well specified in the PRDs.** The entitlements contract
   (`../product/PRICING_AND_PACKAGING.md` §3.2), AI metering (§5), the billing status→behaviour
   mapping (`../architecture/BILLING_AND_EMAIL.md` §5), and the RBAC capability matrix
   (`../product/ONBOARDING_AUTH_RBAC.md` §9.2) are all decided.

**All four remaining phases were written anyway, and claim 2 did not survive any of them.** A PRD
that reads as decided can still be unbuildable: verify the source is a *specification* and not a
prose sketch before assuming a phase is nearly specced.

**SPEC-005 is the strongest case against claim 2, and it inverts the failure mode.** SPEC-003 and
SPEC-004 found PRDs that were *contradictory* — two documents each confidently asserting something
different. Phase 4's central deliverable is not contradictory; it is **absent**. `SAAS_PRD:182`
makes "full email lifecycle" the phase's headline, and `BILLING` §2.6 — the only email catalogue in
the doc set — assigns **zero** templates to Phase 4, while the GA definition of done's own email
list names five that all ship in Phases 2 and 3 (SPEC-005 §0.4, F3). Read literally, the gate is
satisfied before the phase starts. So SPEC-005 §4 and §5 are original design rather than
transcription: the outbox, the suppression list, the delivery log, the drip machinery and the
deletion state machine are specified *here first*, and the two genuinely product-shaped questions
inside them are raised as O1 and O2 rather than defaulted.

**The lesson for a future doc set:** a phase whose PRD content is thin reads exactly like a phase
whose PRD content is complete, because absence has no line number to cite. Contradictions announce
themselves; gaps do not.

---

## Working on a spec

- **One spec per phase.** Each carries its own decisions; there is no global decisions doc,
  because reviewing every phase's choices at once is how they stop getting read.
- **A step that cannot be verified on its own gets split.** Every step in §6 should end in a
  green test or an observable behaviour.
- **Cite line numbers when referencing a PRD** (`SAAS_PRD.md:135`). They were accurate when the
  spec was written; re-check before relying on one.
- **Code claims must be verified against the tree, not remembered.** `docs/PRD_REVIEW.md` §G
  documents what happens when they are not: two gateway PRDs whose central factual claims about
  the existing code turned out to be false.
- **Name the ref you verified against.** SPEC-001 through SPEC-005 were written against
  `telegram-bot`; SPEC-006 is written against `origin/main`, because the two differ in ways that
  change the answer. `telegram-bot` is **13 commits behind main**, and the shared gateway core
  (`gateways/review_common.py`, 1,175 lines) exists only on main — so "does the shared core exist"
  has two correct answers depending on where you look. This cost real rework while SPEC-006 was
  being researched: the core was reported missing, twice, before anyone checked a second ref. A
  claim about "the code" without a ref is not a verified claim. SPEC-006 carries a
  **Verified against:** line in its front matter for this reason; later specs should too.
