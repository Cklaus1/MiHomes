# MiHomes Implementation Specs

Executable specs for the SaaS re-platform. Each spec turns one phase of the PRD set into
something a developer (or an AI agent) can build **without asking a question**.

**Status:** SPEC-001 through SPEC-004 written — Phase 3 is the MVP cut line. Phase 4 not written
yet — see below.

---

## The spec set

| Spec | Phase | Status |
|---|---|---|
| [SPEC-001](SPEC-001-phase0-landing-waitlist.md) | **0** — Landing + waitlist | Ready to build |
| [SPEC-002](SPEC-002-phase1-multitenant-foundation.md) | **1** — Multitenant foundation | Ready to build — **no open decisions** |
| [SPEC-003](SPEC-003-phase2-onboarding-team-rbac.md) | **2** — Onboarding + team + RBAC | Ready to build — **1 open decision** (O1: secret encryption) |
| [SPEC-004](SPEC-004-phase3-billing-freemium.md) | **3** — Billing / freemium | Ready to build — **1 open decision** (O1: launch prices/limits, blocks config only) |
| *SPEC-005* | **4** — Polish + email lifecycle + GA | Not written |

**Two different O1s are open, and they are unrelated.** Label namespaces are per-spec-local (see
*Working on a spec* below), so the numbering restarts in every spec. SPEC-003's O1 is at-rest
encryption of provider API keys; SPEC-004's O1 is the launch prices and limits. SPEC-002's O1
**closed** on 2026-07-31 (→ D13). Always resolve an `O`-label inside the spec that raised it.

**Locked across the set:** hosting is Fly.io, single region, on **managed Postgres**
(`../architecture/MULTITENANCY.md` §11, §11.1). The CLI is an **operator tool, not a user
interface** — local SQLite mode is dropped and the CLI becomes an admin client against hosted
Postgres (SPEC-002 D1). Primary keys are UUIDv7, app-side, no DB-side default (SPEC-001 §4.1,
reused by SPEC-002). Uploads go to S3-compatible object storage behind a `StorageProvider`
Protocol — never a Fly volume, which is single-machine local disk.

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

## Phase 2 was written ahead of Phase 1's outcome — deliberately

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

**For Phases 3–4, the argument still stands** — with claim 2 downgraded. A PRD that reads as
decided can still be unbuildable: verify the source is a *specification* and not a prose sketch
before assuming a phase is nearly specced.

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
