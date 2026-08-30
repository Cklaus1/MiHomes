# SPEC-008 Build Loop — Vendor Discovery D0: AI research for a private shortlist

> **Input spec:** `docs/specs/SPEC-008-vendor-discovery-d0.md` (Ready to build, **2 open
> decisions** — O1 counsel sign-off, O2 per-run cost ceiling)
> **Conventions:** `tasks/build-loop-conventions.md` — stop condition, poison ceiling, circuit
> breaker, artifact routing inherited **unchanged**.
> **Branch:** `worktree-spec-build-harness`. **Target ref:** HEAD `98a48c9` (SPEC-006 complete).
> **Status: AUTHORED, NOT RUN.**

**The stake, in the spec's own words:**

> Every other spec in this set moves data the user already owns. **This one puts words about
> real, named businesses in front of a customer** — phone numbers, licence claims, quality
> judgements — assembled by a model from the open web. **A confidently wrong profile** sends a
> stranger to a business that has closed, or attaches the wrong phone number to a real name.
> **An uncited claim** about a named business is a defamation surface. Both fail *plausibly*: a
> fabricated vendor looks exactly like a researched one.

**Exit criterion:** A15 — an owner researches a category, receives ≥3 cited candidates, promotes
one into their private vendor list, and the run is metered as **more than one AI call**.

---

## 0. Prerequisites

| # | Prerequisite | State |
|---|---|---|
| P1 | Reachable Postgres, all four DB env vars set | ✅ PostgreSQL 18.x, `mihomes_test` + `mihomes_phase0` |
| P2 | `MIHOMES_SECRET_KEY` set | ✅ required since SPEC-003 U1 |
| P3 | SPEC-002's tenancy: `account_id`, RLS, scoped session, `StorageProvider` | ✅ shipped |
| P4 | SPEC-003's `can()` / `require_permission` | ✅ shipped |
| P5 | SPEC-004's AI meter — `AIUsageEvent`, `MeteredProvider`, entitlements limits | ✅ **now real code**, not spec — see §0.6 F6 |
| P6 | SPEC-005 D18's three purge dispositions | ✅ shipped; §4.2 is the first table to exercise `PRESERVE` on a global row |
| P7 | **The 181-commit merge to `main`** | ❌ **OPEN, and not this run's job** — see §0.2 |
| P8 | Counsel sign-off on publishing researched claims (O1) | ❌ absent. **Blocks shipping, not building** — §0.7 |

**Environment — pass inline; the worktree guard rejects `export` chains:**

```
DATABASE_URL               postgresql+psycopg://postgres@localhost:5432/mihomes_test
MIGRATION_DATABASE_URL     postgresql+psycopg://postgres@localhost:5432/mihomes_test
TEST_DATABASE_URL          postgresql+psycopg://postgres@localhost:5432/mihomes_test
LANDING_TEST_DATABASE_URL  postgresql+psycopg://postgres@localhost:5432/mihomes_phase0
MIHOMES_SECRET_KEY         <Fernet key — `mihomes config generate-key`>
```

`py -m pytest`, never `python`. **Without these, DB-backed tests self-skip and the suite reads
green.** Conventions §0 makes a new skip red for exactly this reason.

**Two engine hazards SPEC-006 paid for, restated so this run does not re-learn them:**

1. `cli_database` (root conftest, session-scoped) **repoints `DATABASE_URL` for the whole
   session** once any of its consumers runs, and `test_cli.py` collects early. Any test whose
   *production code* calls `get_session()` must pin the global engine to `TEST_DATABASE_URL` —
   see `test_dedup.py::isolated_db` and `test_gateway_webhook.py::_pin_engine_to_test_db`. The
   symptom is a write landing in another database and reading as a broken feature.
2. **`session` + `web_client_factory` together leak tenant context.** Both enter
   `account_context`; teardown unwinds reverse-of-setup, so the later token resets last and
   restores its own `old_value` — re-binding the tenant for every later test. Take one or the
   other, not both.

---

## 0.2 P7 — the merge is open, and this harness does not discharge it

`worktree-spec-build-harness` is **181 commits ahead of `origin/main`, 0 behind** — SPEC-002
through SPEC-006 in their entirety. The merge is a clean fast-forward and was offered to the
founder; it is a human action a build loop does not take.

**Consequence for this run:** SPEC-008 builds on this branch, because every module it consumes
was verified present *here* by execution (§0.6). That is not the same as main having it. A
future session must not read "SPEC-008 built successfully" as "the branch question was
answered". Carried in §0.8 as **U1**.

Same shape as SPEC-006's §0.2, and worth noting the pattern is now two specs old: the tree that
gets built on and the tree that is canon have not been the same tree since SPEC-002.

---

## 0.6 PRE-FLIGHT RE-VERIFICATION — six findings re-measured, one is false

Conventions §3.1 requires this before task 1 and **halts on mismatch**. SPEC-008 was verified
against `origin/main @ be8d398` on 2026-08-05; HEAD is 181 commits past that, so every claim was
re-run rather than trusted.

| # | Spec's claim | Measured at HEAD | Verdict |
|---|---|---|---|
| **F1** | "All **18** AI tools are read-only local queries… **no web, search, fetch or HTTP tool exists**" | `ai/tools.py` now declares **20** functions. Grep for `web`/`fetch`/`http`/`search` in its `def` lines: **zero matches** | ✅ **Holds.** The count moved, the load-bearing half did not. The agent still cannot reach the internet |
| **F2** | "`httpx` is a `dev` extra, not a runtime dependency" | `pyproject.toml:52`, inside `[project.optional-dependencies]` (line 36) | ✅ **Holds** — and it is now a **live defect**, see below |
| **F3** | "`MAX_TOOL_ROUNDS = 5` is a module constant at `agent.py:12`" | `agent.py:12` exactly, used at `:120` | ✅ Holds, line number included |
| **F4** | The private `Vendor` model already carries most research fields | present | ✅ Holds |
| **F5** | "**`Vendor.id` is `Integer, autoincrement=True`** … §4 will be wrong if SPEC-002's remap lands differently" | `models/vendor.py:49` — **`PGUUID(as_uuid=True)`, `default=new_id`** | ❌ **FALSE, and resolved in the spec's favour.** SPEC-002 landed and remapped it. The caveat can be struck: §4 is written against the post-SPEC-002 design and the tree now matches |
| **F6** | The meter "already exists **in spec form**" | `services/metering/ai_wrapper.py:52 MeteredProvider`, `models/ai_usage.py:43 AIUsageEvent` — **real code** | ✅ Holds, upgraded. A6 gates a bypass of machinery that exists rather than one that is planned |

**No halt.** F5 is the only false finding and it fails in the safe direction — a caveat about a
migration that has since landed correctly.

### The F2 defect this pre-flight surfaced

F2 holding is not merely a note: **four modules import `httpx` at runtime while it is
dev-only.** `whatsapp/cloud_client.py:201`, `services/ha_sync.py` (4 sites), and the orphan
`services/webhook.py:13` — the last at *module level*, so its 52 tests fail outright without the
dev extra. A clean `pip install -e .` raises `ImportError` at first use, which for
`cloud_client.py` is the moment someone sends a WhatsApp message (`_post` is `pragma: no cover`,
so nothing in CI executes it).

**Step 1 fixes this as its own work** — it promotes `httpx` to a runtime dependency because
`web_tools.py` needs it — and the same one-line edit closes all four call sites. Logged in
`opportunities.md`; **do not amend SPEC-006 for it.** That spec is complete, pushed and
reported, and reopening a delivered artifact for an import CI never executes is scope creep.

### §9's fixtures do not exist under the names it gives

§9 asks for **`account_pro` and `account_estate` from SPEC-004**. Measured: neither fixture
exists in `tests/conftest.py`. What exists is `_create_account(engine, *, prefix, name, plan)`
with `DEFAULT_FIXTURE_PLAN = "estate"`, plus `account_a`/`account_b` built on it.

This is C6's shape from SPEC-006 — §9 naming something that does not resolve — and it is caught
here rather than at G6.1. **A13 needs a `free` account explicitly**, and conftest's own comment
says why: *"Tests about Free must say so explicitly … a limit test that silently inherited its
plan from a shared default would pass without ever exercising the limit."* Build the two plan
fixtures on `_create_account`, and treat "the fixture exists" as part of A13's task rather than
a prerequisite.

---

## 0.7 O1 and O2 — one blocks ship, one blocks a single step

Conventions §3.3: classify each `O` as blocks-build or blocks-ship, and poison only on
blocks-build.

- **O1 — counsel sign-off** on publishing researched claims about named businesses.
  **Blocks-ship, absolutely.** `VENDOR:295` gates D0 on it, and §10 is explicit that an uncited
  claim about a named business is a defamation surface. Nothing in §5–§6 waits on it: the
  pipeline, the citation trail and A5's enforcement are all buildable and testable now. **Carried
  as U2, never silently satisfied.**
- **O2 — the per-run cost ceiling.** Blocks **Step 7's number only**, not its mechanism. Build
  the ceiling reading a config key with a defensible default; the value is a founder call.
  Same split SPEC-004's O1 used for prices: *"every step targets config keys, never literals, so
  the code is complete and testable before the numbers exist."*

---

## 0.8 UNMET LAUNCH GATES — carried forward, not silently satisfied

| # | What | Owner |
|---|---|---|
| **U1** | **181 commits unmerged to `main`.** This run builds on the branch; canon has none of it | founder |
| **U2** | **O1 — counsel sign-off.** The stage cannot ship to a customer without it, whatever the suite says | founder |
| **U3** | **O2 — the per-run cost ceiling's value.** Mechanism built, number unset | founder |
| **U4** | **No captured page set exists yet.** §9's fixtures are real HTML from real vendor sites; assembling them is human work with its own licence questions | founder |
| **U5** | Everything SPEC-005 §10 and SPEC-006 §0.8 carried, unchanged. **This spec adds research to that list rather than subtracting from it** | founder |

---

## 1. Task DAG

Conventions §1.3: one step per group; the group commit is the resume point.
`py scripts/spec008_reconcile.py --collect` joins §8 → §9 → §1 and **runs after every group
commit**.

**Ordering the spec names as load-bearing:** **Step 1 before Step 3** (web access before the
loop that uses it); **Step 3 before Step 5** (candidates exist before promotion); **Step 2
before Step 4** (the tables exist before the pipeline writes snapshots).

### [ ] G0 — pre-flight and the doc repairs — *dep: none*
- [ ] G0.1 · §0.6 · — · confirm the six findings re-measured above still hold at build time; **F5 is false and struck** · verify: this section
- [ ] G0.2 · §2 · — · B1 (say plainly the web tools do **not** exist) and B2 (record D9's metering resolution) in `VENDOR_DISCOVERY_PRD.md` · verify: `tests/unit/test_docs_vendor_prd.py::test_repairs_landed`

### [ ] G1 — Step 1: web access — *dep: G0 — MUST precede G3*
- [ ] G1.1 · §6 Step 1 · A1 · `web_tools.py`; **private-IP, non-http scheme and off-internet redirect each refused** — SSRF is the whole risk of giving a model a fetcher · verify: `tests/unit/test_web_tools.py::test_url_refusals`
- [ ] G1.2 · §6 Step 1 · A2 · a fetch returns the **final** post-redirect URL, and that is what gets cited — citing the pre-redirect URL attributes a claim to a page that did not make it · verify: `tests/unit/test_web_tools.py::test_final_url_cited`
- [ ] G1.3 · §0.6 · — · **promote `httpx` to a runtime dependency**; the same edit closes the four existing runtime importers · verify: `tests/unit/test_web_tools.py::test_httpx_is_a_runtime_dependency`

### [ ] G2 — Step 2: the two tables — *dep: G1 — MUST precede G4*
- [ ] G2.1 · §6 Step 2 · A3 · `vendor_candidate` (tenant) + `research_snapshot` (**global**) + migration; its own engine running real Alembic up→down→up · verify: `tests/integration/test_migration_discovery.py::test_up_down`
- [ ] G2.2 · §6 Step 2 · A9 · `research_snapshots` has **no RLS policy** and is **not writable through a request-path session** — the `EmailSuppression` precedent, and the second half is what makes the carve-out safe rather than merely declared · verify: `tests/unit/test_discovery_tenancy.py::test_global_readonly`
- [ ] G2.3 · C8 · — · two `ENTITY_CLASSES` entries, `TENANT_TABLES` +1 / `GLOBAL_TABLES` +1, **three pinned counts** — see §2 below for exact locations · verify: `tests/unit/test_matrix.py::TestEntityClassification::test_every_model_is_classified`

### [ ] G3 — Step 3: the research loop — *dep: G1, G2*
- [ ] G3.1 · §6 Step 3 · A4 · `run_research` against fixture pages yields ≥3 candidates, each with ≥1 citation · verify: `tests/integration/test_research.py::test_cited_candidates`
- [ ] G3.2 · §6 Step 3 · A5 · **G-provenance — the phase's definition of done.** A candidate with no fetched source is **dropped, never returned**. Enumerate every customer-visible field and assert each traces to a URL the fetcher *actually retrieved* · verify: `tests/integration/test_research.py::test_no_uncited_candidates`
- [ ] G3.3 · §6 Step 3 · A6 · **G-metering** — every model call flows through `get_provider()`; **no provider constructed in-pipeline**, which is the bypass SPEC-004 F8 already found once in `agent_stream` · verify: `tests/integration/test_research.py::test_all_calls_metered`

### [ ] G4 — Step 4: the shared cache — *dep: G2, G3*
- [ ] G4.1 · §6 Step 4 · A7 · a second account researching the same business hits the cache and makes **zero** model calls · verify: `tests/unit/test_cache.py::test_cross_account_hit`
- [ ] G4.2 · §6 Step 4 · A8 · the cache key contains **nothing tenant-derived**, asserted statically — a key that mixed in `account_id` would silently disable the cache; one that leaked it across accounts is worse · verify: `tests/unit/test_cache.py::test_key_has_no_tenant_data`

### [ ] G5 — Step 5: promotion — *dep: G3*
- [ ] G5.1 · §6 Step 5 · A10 · a promoted candidate becomes a `Vendor` **in the promoting account only** · verify: `tests/integration/test_promote.py::test_tenant_scoped`
- [ ] G5.2 · §6 Step 5 · A11 · citations **survive promotion** into the vendor record — provenance that stops at the shortlist is provenance the customer never sees · verify: `tests/integration/test_promote.py::test_provenance_preserved`
- [ ] G5.3 · §6 Step 5 · A12 · a non-owner cannot promote · verify: `tests/integration/test_promote.py::test_owner_only`

### [ ] G6 — Step 6: the entitlement gate — *dep: G3*
- [ ] G6.1 · §6 Step 6 · A13 · `vendor_research` in the limits module, Pro/Estate only; **Free is denied and the `Denied` names its upgrade target**. Needs a `free` account fixture, which **does not exist** — §0.6 · verify: `tests/unit/test_discovery_gates.py::test_free_denied`

### [ ] G7 — Step 7: the cost ceiling — *dep: G3*
- [ ] G7.1 · §6 Step 7 · A14 · a pathological query stops at the ceiling rather than consuming the account's monthly quota. **Mechanism now, O2's number later** (U3) · verify: `tests/integration/test_research.py::test_cost_ceiling`

### [ ] G8 — the exit criterion — *dep: all*
- [ ] G8.1 · §6 exit · A15 · **end to end** — research → cited shortlist → promote → metered as **>1 call**. Pair the negative with the positive (§0.5b) · verify: `tests/integration/test_discovery_e2e.py::test_exit_criterion`

### [ ] G-Final — Compound-stop verification (conventions §4.1)
- [ ] F.1 · full-suite `py -m pytest -q` green (condition C) — baseline **2538 passed**; a new skip is red
- [ ] F.2 · every §8 criterion green by its own named test (condition E) — **all 15**, run by node id
- [ ] F.3a · walk §6 top-to-bottom: every step has a task (condition B) — **7 steps**
- [ ] F.3b · `py scripts/spec008_reconcile.py --collect` exits 0, with `PENDING_TESTS_IN_EXISTING_FILES` **empty**
- [ ] F.4 · smoke green (condition D)
- [ ] F.5 · write `tasks/build-loop-spec008-report.md`

---

## 2. C8 — the schema gates, with their exact locations

SPEC-006 G1 mapped these; carried forward so this run does not re-derive them. **Two tables, one
tenant and one global**, so the two halves land in different registries.

| Gate | Location | Change |
|---|---|---|
| `ENTITY_CLASSES` | `authz/actions.py` `_entity_classes()` | **two** entries — `VendorCandidate` and `ResearchSnapshot` |
| `TENANT_TABLES` | `tenancy/registry.py` | `vendor_candidates` **only** |
| `GLOBAL_TABLES` | `tenancy/registry.py` | `research_snapshots` — the `EmailSuppression` precedent: global because the *fact* belongs to the business, not to whoever researched it |
| pinned count | `tests/integration/test_isolation.py:80` | `EXPECTED_TENANT_TABLE_COUNT` **50 → 51** |
| pinned count | `tests/unit/test_tenancy_registry.py:256` | **50 → 51** |
| pinned count | `tests/integration/test_pg_baseline.py:146` | tables **56 → 58** (both), enums **22 → ?** if §4.1 adds one |
| `test_u7_enforcement.py` | `tests/integration/` — **not `tests/unit/`** | **Expect it to fire** on whichever model is `ACCOUNT_LEVEL`. SPEC-006 D3: I searched the wrong directory and wrongly called the file missing |
| `test_composite_indexes_lead_with_account_id` | `tests/unit/test_tenant_indexes.py` | **A `UniqueConstraint` in `__table_args__` needs no `EXPECTED_NON_LEADING` entry** — it emits a constraint, not an index, so an entry would be stale on arrival (SPEC-006 D2, measured) |

**A9 is the one to get right.** "No RLS policy" is half the claim; the other half — *not
writable through a request-path session* — is what stops a global table becoming a cross-tenant
write surface. Assert both, or the carve-out is declared rather than enforced.

---

## 3. Gates this spec cannot close by itself

Conventions §0: *"a stub can satisfy A+B+C+D."* Four criteria define their own scope.

| Gate | Check | Closes |
|---|---|---|
| **G-provenance** | Record every URL `web_fetch` actually retrieved, then assert **every customer-visible field on every candidate** traces to one. Not "citations is non-empty" | **A5** — a model that invents plausible URLs passes the weak form trivially |
| **G-metering** | Assert **no provider is constructed inside the pipeline**, statically, as well as counting calls | **A6** — SPEC-004 F8 found exactly this bypass in `agent_stream`; a call count alone passes on a pipeline that meters some calls and not others |
| **G-refusals** | A1's three refusal classes must fail **distinctly**, and a *valid* URL must still fetch | **A1** — "everything is refused" satisfies a refusal test perfectly and ships a dead fetcher |
| **G-cache-key** | A8 asserted on the key-building function's **source**, not just on two equal keys | **A8** — two accounts producing the same key proves nothing if the function ignores its inputs |

---

## 4. Recurring hazards, pre-declared

**`PENDING_TESTS_IN_EXISTING_FILES` will be needed at least twice.** §8 groups criteria by
**file**, not by group — `test_research.py` holds A4, A5, A6 **and** A14 (G3 and G7);
`test_cache.py` holds A7 and A8; `test_web_tools.py` holds A1, A2 and G1.3. So the first group
to land creates the file a later group writes into, `--collect` starts checking every node id in
it, and the later ones correctly do not resolve yet. This happened **three times** during
SPEC-006 and was rediscovered each time. Add the entry, annotate why, **delete it the moment its
group lands** — `TestPendingSetExpires` fails if it outlives it.

**Every negative assertion needs a positive twin** (§0.5b). This spec is unusually full of them —
"refused", "dropped, never returned", "zero model calls", "no RLS policy", "nothing
tenant-derived". Each is trivially satisfied by a component that does nothing at all. SPEC-006
shipped three criteria green over a webhook that dispatched nothing, because the rows being
counted came from a different code path; the fix each time was to assert **content**, not shape.

**Fixture rows that `commit()` escape the `session` fixture's rollback** and pollute every later
test. Both SPEC-006 fixtures that committed needed explicit cleanup, and `users` is GLOBAL so an
account-scoped `DELETE` loop cannot reach it.
