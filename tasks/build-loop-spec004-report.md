# SPEC-004 Build Loop — End-of-Run Report

> **Spec:** `docs/specs/SPEC-004-phase3-billing-freemium.md` — Phase 3: Billing / Freemium
> **Harness:** `tasks/build-loop-spec004.md` · **Conventions:** `tasks/build-loop-conventions.md`
> **Branch:** `worktree-spec-build-harness` · **Baseline:** `4178286` (SPEC-003 complete)
> **Run:** 2026-08-24 → 2026-08-25

## Status: **COMPLETE** — all five stop conditions hold

| | Condition | Evidence |
|---|---|---|
| **A** | every checkbox `[x]` or `[!]` | §1 of the harness — **18 groups, 0 poisoned** |
| **B** | every §6 step tasked **and** every §8 criterion gated | F.3a: 18/18 · F.3b: 31/31 |
| **C** | full suite green *including this spec's new tests* | `2184 passed, 3 skipped, 2 xfailed, 0 failed` |
| **D** | smoke green | `test_smoke_all_tools.py` — `18 passed` |
| **E** | every §8 criterion green **by its own named test** | 31 node ids run explicitly — `44 passed, 0 skipped` |

**Suite: 1945 → 2184** (+239 tests). The 3 skips and 2 xfails are inherited from SPEC-002/003 and
unchanged; **no new skip was introduced**, which conventions §0 treats as a red gate.

**Phase 3 is the MVP cut line.** `SAAS_PRD:180`'s exit criterion — *a Free user can upgrade to Pro
and gates flip via webhook* — is green and mutation-verified.

---

## Per-group

| Group | Step(s) | Commit | Tests | Suite after |
|---|---|---|---|---|
| harness + pre-flight | — | `36eca9b` | — | 1945 |
| G1+G2 | 1–2 provider seam, price map | `d4ae614` | 21 | 1966 |
| G3 | 3 idempotency ledger | `b672571` | 19 | 1985 |
| G4 | 4 webhook route | `682f0e7` | 10 | 1995 |
| G5 | 5 idempotency + out-of-order | `38c3633` | 12 | 2007 |
| G6 | 6 checkout + portal | `6aae48a` | 11 | 2018 |
| G7 | 7 status → entitlement mapping | `5995d9b` | 26 | 2044 |
| G8 | 8 real limits — **gates live** | `4cd5043` | 11 | 2055 |
| G9 | 9 close the `agent_stream` bypass | `9402cd7` | 11 | 2066 |
| G10 | 10 the meter — **A11** | `61812f2` | 29 | 2095 |
| G11 | 11 overage | `408e43b` | 13 | 2108 |
| G12 | 12 scheduled jobs | `44f1d35` | 13 | 2121 |
| G13 | 13 trial state machine | `b69983a` | 13 | 2134 |
| G14 | 14 restricted mode | `681daa0` | 13 | 2147 |
| G15 | 15 the four emails | `3b11c17` | 20 | 2167 |
| G16 | 16 feature gates | `4e0af61` | 12 | 2179 |
| G17+G18 | 17–18 importer gate, **A31** | `a0146e5` | 5 | 2184 |

**Poisoned tasks: none.** The circuit breaker (>5 poisons, or any poison in G3/G4/G9/G10) never
fired.

---

## Criteria reconciliation — all 31 green by their own named test

Where a test landed in a different file from the one §9 predicted, the actual node id is given;
the criterion is unchanged.

| # | Criterion | Test (as run) |
|---|---|---|
| A1 | Free denies 2nd home / 4th seat / staff invite | `unit/test_limits.py::TestFreeGates::test_free_gates` |
| A2 | all 8 Stripe statuses map per `BILLING` §5 | `unit/test_billing_mapping.py::TestTheEightStatuses::test_status_table` |
| A3 | every `Denied` names an upgrade target | `unit/test_limits.py::TestUpgradeTargets::test_denied_names_target` |
| A4 | tampered webhook rejected, **no DB write** | `integration/test_webhooks.py::TestSignatureVerification::test_bad_signature_no_write` |
| A5 | same event twice applies once | `integration/test_webhook_idempotency.py::TestIdempotency::test_idempotent_replay` |
| A6 | ledger has **no** RLS policy | `unit/test_webhook_tenancy.py::TestLedgerIsNotTenantScoped::test_ledger_not_rls` |
| A7 | out-of-order event dropped | `…::TestOutOfOrder::test_out_of_order_dropped` |
| A8 | `past_due` full access, `unpaid` restricts | `unit/test_billing_mapping.py::TestGraceVersusRestricted::test_grace_then_restrict` |
| A9 | dropped webhook corrected in one sweep | `integration/test_jobs.py::TestReconcileIsIdempotent::test_drift_corrected` |
| A10 | no SDK client outside `*_provider.py` | `unit/test_ai_metering.py::TestNoFactoryBypass::test_no_factory_bypass` |
| **A11** | **every AI entry point metered** | `unit/test_ai_metering.py::TestEveryEntryPointIsMetered::test_all_entry_points_metered` |
| A12 | no provider cached module-level | `…::TestNoModuleLevelCache::test_no_module_level_cache` |
| A13 | archiving does not reduce `calls_used` | `integration/test_ai_usage.py::TestArchiveDoesNotResetUsage::test_archive_preserves_usage` |
| A14 | ceiling denies, soft cap does not, nudges once | `integration/test_overage.py::TestTheThreeRegions::test_ceiling_and_nudges` |
| A15 | system-initiated calls exempt | `…::TestSystemCallsAreExempt::test_system_calls_exempt` |
| A16 | both jobs no-op on a 2nd run | `integration/test_jobs.py::TestReconcileIsIdempotent::test_idempotent` |
| A17 | trial grants Pro with **no Stripe subscription** | `integration/test_trial.py::TestCardlessTrial::test_cardless_trial_entitlements` |
| A18 | one trial per account, ever | `…::TestOneTrialEver::test_one_trial_ever` |
| A19 | expiry non-destructive, surfaces over-limit | `…::TestExpiryIsNondestructive::test_expiry_is_nondestructive` |
| A20 | no downgrade deletes; core home editable | `integration/test_downgrade.py::TestNothingIsDeleted::test_nothing_deleted` |
| A21 | four emails render, fire once | `integration/test_billing_emails.py::TestTheFourTemplates::test_four_templates` |
| A22 | Free denies ratings **and** pages load | `integration/test_feature_gates.py::TestBothDashboardPagesStillLoad::test_ratings_gated_pages_load` |
| A23 | `due_date` denied on Free, undated succeeds | `…::TestDueDateGate::test_due_date_gate` |
| A24 | Telegram path unaffected | `…::TestTheBotPathIsUnaffected::test_bot_path_ungated` |
| A25 | over-limit import refused, no partial account | `integration/test_importer.py::test_over_limit_refused` |
| A26 | two concurrent calls at the cap | `integration/test_overage.py::TestConcurrentAtTheCap::test_concurrent_at_cap` |
| A27 | two concurrent deliveries apply once | `integration/test_webhook_idempotency.py::TestConcurrentDelivery::test_concurrent_delivery` |
| A28 | non-owner denied every billing route | `integration/test_billing_routes.py::TestOwnerOnly::test_owner_only` |
| A29 | no price id in any signature | `unit/test_prices.py::TestNoPriceIdInInterface::test_no_price_id_in_interface` |
| A30 | Phase 3 migration up/down clean | `integration/test_pg_baseline.py::test_upgrade_then_downgrade_is_clean` |
| **A31** | **Free gate flips to Pro via webhook, not redirect** | `integration/test_upgrade_flow.py::TestTheExitCriterion::test_exit_criterion` |

**Four file relocations from §9's manifest**, all because the test belongs where its subject lives
rather than where the manifest guessed: A5/A7/A27 sit in `test_webhook_idempotency.py` (Step 5's
own module, separate from Step 4's route tests); A8 is a unit test because the mapping is pure —
its *route-level* counterpart is A20's; A13 is in `test_ai_usage.py` with the meter's behavioural
tests, since `test_ai_metering.py` is static; A25 extends the existing `test_importer.py` rather
than a new file, so the eight fixtures it shares stay in one place; A30 is discharged by the
inherited `test_pg_baseline.py` round-trip, which C7 measured as already covering every migration.

---

## What the pre-flight caught (§0.6)

SPEC-004 was written 2026-08-04 against a tree where **Phases 0, 1 and 2 did not exist**. All
three had since landed, so §0.1's premise was false *in the build's favour* — and conventions §3.1
forbids proceeding on an unverified premise in either direction.

Thirteen corrections. The four that changed what got built:

- **C4** — Step 8 was described as *"real limits, replacing D7's free-unlimited"*. SPEC-003 had
  already pre-shipped `PLAN_LIMITS_PHASE3` inert **with a drift gate**, so the production change
  was one line. **But the total change was ~96 test outcomes** (15 failures + 81 errors), all
  fixtures encoding the pre-gate world. "One-line swap" was true of the code and false of the work.
- **C6** — §4's DDL specifies `String(36)` PKs. The tree went native UUID at SPEC-002;
  transcribing verbatim would have failed `test_baseline_matches_metadata`.
- **C8** — D8 requires owner-only billing, which reads like a new matrix key. `billing.manage`
  already existed at row 15; adding a 21st would have broken A1's row-set equality. **Saved a
  step's worth of wrong work.**
- **C9** — **SPEC-003's fail-closed gates actively reject SPEC-004's code**, and the spec predates
  all of them. Six live tests turn red the moment this phase's models land. Each was answered, not
  routed around.

---

## Bugs found in existing code

| Where | What | Fixed in |
|---|---|---|
| `web/security.py` | **The Host guard rejected every live Stripe webhook.** H30 400s any non-loopback `Host`; Stripe posts to the endpoint's public hostname. No test would ever have caught it — the test client's base URL is `localhost` | G4 |
| `cli/__init__.py` | **`mihomes jobs` could not be invoked** on a multi-account install: the root callback binds a tenant before any subcommand, and these sweep *across* accounts. Service tests were green; the entrypoint was unreachable | G12 |
| `test_pg_baseline.py` | Legacy-revision check used `startswith("000")` — correct for 0001–0009, **guaranteed to fail at 0010**, which is exactly when it did | G3 |
| `cli/jobs.py` | `_expire_trial` left `subscription_status="trialing"` on an expired account | G13 |
| `services/billing/stripe_provider.py` | `checkout.session.completed` missing from the event map — the only event linking a Checkout to an account before `stripe_customer_id` is stored | G5 |

---

## Tests that were green for the wrong reason

Every one of these was found by **mutation**, not by reading:

- **A27** (concurrent delivery) — **wrong twice.** v1 called the handler sequentially, so no race
  occurred and check-then-insert passed all 12 tests. v2 used two threads, which serialized by
  luck. v3 forces the overlap by holding an uncommitted row open. Then it passed alone and failed
  in the suite: the *rival* fixture was losing the race and its exception was being counted as the
  handler's.
- **A22** (ratings) — **vacuous three separate ways**: no vendor existed in the request's world;
  the seed then had to be *proven* to arrive; and the account was on `estate` by fixture default,
  so the gate could not fire whatever it did.
- **The 100% nudge** — a mutation re-marking it on every call passed all 12 tests, because the
  test asserted the 80% marker's stability and never the 100% one.
- **A6's first draft** failed on the migration's *own comment* warning readers not to add a policy.
  A source scan cannot tell a warning from a call; the fix was `ast`, not softer wording — a test
  that punishes the explanation trains the next author to delete it.

**Every security- and money-relevant arm was mutation-checked**: break it, confirm red, restore.

---

## The bug that cost the most to find

A31 passed alone and failed in the full suite. The webhook returned **200** and logged *"no
account for customer"* — which reads as a mapping bug in code just written.

1. *"The ledger deduplicated it."* Checked: zero rows. Wrong.
2. *"The `session` fixture's open transaction hides the row."* Plausible, and the same trap G16 hit
   an hour earlier. Rewrote onto a dedicated committed account — **still failed**, which is what
   falsified the theory rather than leaving it merely incomplete.
3. **Actual cause:** `get_session()` resolves `DATABASE_URL` **at call time**, and the
   session-scoped `cli_database` fixture rewrites that variable to its own database, restoring it
   only at teardown. Every test running after it looks in the wrong database.

Pinning the variable was **not enough** — `db._engine` is a module-level cache, so `dispose_engine()`
is required alongside. Both facts are now in the fixture's docstring.

---

## Unmet launch gates (§0.8) — carried forward, not silently satisfied

| # | What | Owner |
|---|---|---|
| **U1** | **O1 — the ~20 placeholder prices and limits.** The mechanism ships; the numbers do not. A correct billing system charging the wrong amount is still wrong, and no test here would say so | founder |
| **U2** | **No Stripe account or test keys exist.** Every criterion is proved against `FakeBillingProvider`; **nothing proves the live Stripe account's own configuration** — Products, prices, tax, the endpoint secret, whether the restricted key is scoped correctly | founder |
| **U3** | Revenue correctness — §8 proves the mechanism, never the amounts | founder |
| **U4** | Cost attribution below the account: the meter counts per account, so one member can exhaust the quota with no visibility into who | accepted |
| **U5** | Inference cost vs. price — `ai_calls_per_month` caps *calls*, not tokens. `tokens_in`/`tokens_out` are recorded so it becomes measurable; nothing acts on it until metered billing (Phase 4+) | Phase 4+ |
| **U6** | Inherited from SPEC-003 §10, unchanged: mis-declared action keys, aggregate inference, the Telegram bot's transport | as recorded |
| **U7** | ✅ **Resolved** — SPEC-003's O1 (secrets at rest) closed by U1 there. SPEC-004 §10's first bullet and N12's *reason* are stale; N12's *rule* still holds | resolved |
| **U8** | **A metering-infrastructure outage lifts the AI ceiling for its duration.** `_check` fails open when the *lookup* raises — measured first: the AI route reads its provider key from the database before a provider exists, so a dead database already fails the request. A `Denied` still raises; re-mutating the ceiling confirmed A14 kept its teeth | accepted |

---

## Deployment prerequisites

Beyond O1, the phase needs four environment variables that do not exist yet:
`STRIPE_SECRET_KEY` (a **restricted** key), `STRIPE_WEBHOOK_SECRET`, and the four
`STRIPE_PRICE_*` ids. The webhook endpoint must be registered in the Stripe dashboard at the
deployment's public hostname.

`mihomes jobs trial-sweep` and `mihomes jobs reconcile` need a scheduler. D15 defaults to a Fly
scheduled machine with a dedicated always-on machine as the named alternative — **Fly's mechanism
has not been verified against their documentation**, so that remains a default rather than an
asserted fact. Both commands are idempotent and safe to run twice, which is the half that is
proved.

---

## Verification evidence

```
F.1  full suite      2184 passed, 3 skipped, 2 xfailed, 0 failed   (303s)
F.2  31 criteria       44 passed, 0 skipped            (run by node id, -rs)
F.3a 18 steps        all tasked            (derived from §6, checked against the DAG)
F.3b 31 criteria     all gated             (derived from §8, checked against the DAG)
F.4  smoke             18 passed
     migrations        5 passed  (round-trip, metadata drift, single head)
     ruff              clean on all new code
```

Baseline for comparison: `1945 passed, 3 skipped, 2 xfailed, 0 failed` at `4178286`.
