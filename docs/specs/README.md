# MiHomes Implementation Specs

Executable specs for the SaaS re-platform. Each spec turns one phase of the PRD set into
something a developer (or an AI agent) can build **without asking a question**.

**Status:** SPEC-001 and SPEC-002 written. Later phases deliberately not written yet — see below.

---

## The spec set

| Spec | Phase | Status |
|---|---|---|
| [SPEC-001](SPEC-001-phase0-landing-waitlist.md) | **0** — Landing + waitlist | Ready to build |
| [SPEC-002](SPEC-002-phase1-multitenant-foundation.md) | **1** — Multitenant foundation | Ready to build (O1 open, does not block) |
| *SPEC-003* | **2** — Onboarding + team + RBAC | Not written |
| *SPEC-004* | **3** — Billing / freemium | Not written |
| *SPEC-005* | **4** — Polish + email lifecycle + GA | Not written |

**Locked across the set:** hosting is Fly.io, single region (`../architecture/MULTITENANCY.md`
§11). The CLI is an **operator tool, not a user interface** — local SQLite mode is dropped and the
CLI becomes an admin client against hosted Postgres (SPEC-002 D1). Primary keys are UUIDv7,
app-side, no DB-side default (SPEC-001 §4.1, reused by SPEC-002).

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

## Phases 2–4: deliberately not written yet

Not an oversight, and not blocked on anything. Two reasons:

1. **Phase 1 will teach us things Phase 2–4 specs would have to absorb.** The tenant-scoping
   layer — the `TenantOwned` mixin, the `with_loader_criteria` hook, RLS behaviour under
   PgBouncer — is the load-bearing part of the whole re-platform. Speccing the phases that sit
   on top of it before it exists means writing rework. SPEC-002 §7 already lists what Phase 2
   inherits (`require_permission`, the entitlements service, the per-tenant config UI) as
   `DEFERRED` items with their interface room reserved.
2. **The Phase 2–4 surface is already well specified in the PRDs.** The entitlements contract
   (`../product/PRICING_AND_PACKAGING.md` §3.2), AI metering (§5), the billing status→behaviour
   mapping (`../architecture/BILLING_AND_EMAIL.md` §5), and the RBAC capability matrix
   (`../product/ONBOARDING_AUTH_RBAC.md` §9.2) are all decided. There is less spec-shaped work
   left there than the phase count suggests.

To override this, read SPEC-001 and Phase 1's outcome first — the reasoning is here so the
decision can be made on evidence rather than by asking.

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
