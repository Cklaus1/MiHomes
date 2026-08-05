# SPEC-008 — Vendor Discovery **D0**: AI research for a private shortlist

**Phase:** 4+ growth bet (canon — `../product/SAAS_PRD.md` §10; **not** a Phase 5 — see §0.2)
**Stage:** **D0 only** of `VENDOR_DISCOVERY_PRD.md` §9's D0–D3. No public directory, no reviews, no moderation, no claim flow — see §0.3
**Status:** Ready to build — **2 open decisions** (O1: counsel sign-off, blocks *shipping* not building; O2: per-run cost ceiling)
**Written:** 2026-08-05
**Verified against:** `origin/main` @ **`be8d398`** (2026-07-30). Every code claim below was checked with `git show be8d398:<path>`. **Not the branch the doc set was written on** — see SPEC-006 §0.1.
**Source PRDs:** `../product/VENDOR_DISCOVERY_PRD.md` (primary — §3 model, §4 pipeline, §7 legal, §9 staging), `../product/PRICING_AND_PACKAGING.md` §5 (AI metering — a research run is not one AI call), `../architecture/MULTITENANCY.md` §3.3 (the global-table exception)
**Depends on:** SPEC-002 (Phase 1) — `account_id`, RLS, the scoped session, `StorageProvider`. SPEC-003 (Phase 2) — `can()`, `require_permission`. SPEC-004 (Phase 3) — the AI meter, `MeteredProvider`, and the entitlements limits module. SPEC-005 (Phase 4) — **D18's three purge dispositions**, which this spec's §4 is the first table to exercise.

**Goal.** Let an owner ask *"find me a pool guy in Ibiza"* and get back a cited, confidence-scored
shortlist of real businesses — researched by the agent, saved to their own private vendor list, and
metered honestly.

**Exit criteria:** an owner runs a research query, receives ≥3 candidate vendors each carrying
source citations and a confidence score, promotes one into their private `Vendor` list, and the
run is metered as **more than one AI call**.

**The stake.** Every other spec in this set moves data the user already owns. **This one puts words
about real, named businesses in front of a customer** — phone numbers, licence claims, quality
judgements — assembled by a model from the open web. Two failure modes have no analogue elsewhere
in the product. **A confidently wrong profile** sends a stranger to a business that has closed, or
attaches the wrong phone number to a real name, and the customer acts on it. **An uncited claim**
about a named business is a defamation surface, which is why `VENDOR:295` gates this on counsel
before D0 and not before D1. Both fail *plausibly*: a fabricated vendor looks exactly like a
researched one.

---

## 0. Four things a reader must know before trusting this spec

**0.1 — Everything this spec builds is greenfield, including the part the PRD assumes exists.**
Verified on `be8d398`:

- `services/ai/tools.py` declares **18 functions, and every data tool is a read-only `_query_*`
  against the local database** — `_query_vendors`, `_query_assets`, `_query_tasks`, and so on
  (F1). **There is no web-search tool, no fetch tool, no HTTP tool of any kind.** The agent
  cannot currently reach the internet.
- `httpx` is in `pyproject.toml`'s **`dev` extra**, not its runtime dependencies (F2). The
  outbound HTTP this spec depends on has no sanctioned client yet.
- `agent.py:12` sets `MAX_TOOL_ROUNDS = 5` as a module constant (F3) — a research run needs more
  rounds than a chat answer, and the constant is not per-call.

So "the agent searches the web, visits vendor sites, aggregates public reputation signals"
(`VENDOR` §4) describes **zero lines of existing code**. §3–§5 build it.

**0.2 — Phase 4+ growth bet. There is no Phase 5.** `SAAS_PRD` §10's table ends at Phase 4 (GA)
and the row beneath is `4+ | Growth bets (separate PRDs) | Per-PRD`. `VENDOR` §9 already says
"Discovery is **Phase 4+** (post-GA)". Inventing a Phase 5 repeats the collision `PRD_REVIEW` **G4**
catches — see SPEC-006 §0.2, which had to correct exactly that in another growth-bet PRD.

**0.3 — This is D0 of four stages, and the boundary is load-bearing.** `VENDOR` §9 stages the work:

| Stage | Ships | This spec |
|---|---|---|
| **D0** | AI research for the owner's **own private shortlist** — no public directory | **← here** |
| D1 | Public `GlobalVendor` directory + browse/search | SPEC-009 |
| D2 | `VendorReview` public stars, verified-hire gated | SPEC-010 |
| D3 | Vendor-side monetization | SPEC-011 |

**Scoping to D0 is what makes this spec writable.** `VENDOR` §10 lists ten open questions — cold
start density, moderation budget, cannibalization, match quality, review deletion — and **eight of
them are about D1–D3**. At D0 there is no public row to moderate, no review to delete, no directory
to be sparse. Two decisions remain, and they are §1.3's.

**0.4 — This spec is the first consumer of SPEC-005 D18's `anonymize` disposition, and it must
declare its columns accordingly.** SPEC-005 §5.4 defines three purge dispositions and tags the
anonymize category `DEFERRED (SPEC-008)` because no table qualified yet. **D0 still does not create
one** — anonymize applies to `VendorReview`, which is D2. What D0 *does* create is
`ResearchSnapshot`, and §4.2 states its disposition explicitly rather than leaving the next reader
to guess. The rule is stated here so SPEC-010 inherits a settled answer instead of rediscovering
`VENDOR:299` the hard way.

---

## 1. Decisions

### 1.1 Locked — inherited or doc-derivable

| # | Decision | Source |
|---|---|---|
| D1 | **D0 writes only to the account's own private `Vendor` rows.** No global table is written | `VENDOR` §9 — "no public directory yet" |
| D2 | **`GlobalVendor` and `ResearchSnapshot` carry no `account_id`** and are the documented exception to strict scoping | `VENDOR` §3.4, `MULTITENANCY` §3.3 (amended by the 2026-08-05 doc-fix pass to name them) |
| D3 | **A research run is metered as more than one AI call** | `VENDOR:164` self-flags this; `PRICING` §5.1 meters *calls*, and a run is many. See D9 |
| D4 | **Every extracted fact carries a source citation, or it is not stored** | `VENDOR` §7. An uncited claim about a named business is the defamation surface counsel is being asked about |
| D5 | **The shared research cache is keyed on `(business identity, category)` — never on anything tenant-derived** | `VENDOR:161`. This is what makes a cross-tenant cache safe: inputs and outputs are public-web facts only |
| D6 | **Promotion to the private list is an explicit owner action**, never automatic | `VENDOR:57`. The agent proposes; the human adopts |
| D7 | **Research is Pro/Estate only** | `VENDOR` §9's D0 gate. Free accounts get the upgrade prompt, and every `Denied` names its target (`PRICING` §3.2 rule 4) |

### 1.2 Locked — founder decisions, 2026-08-05

| # | Decision | Rationale |
|---|---|---|
| **D8** | **Web access is a new, narrow tool pair — `web_search` and `web_fetch` — added to `services/ai/tools.py`, not a general HTTP capability** | The 18 existing tools are all read-only local queries (F1); adding "the agent can make HTTP requests" is a materially larger change than adding a data source. Two named tools with fixed shapes keep the blast radius at what the feature needs. `httpx` moves from the `dev` extra to runtime (F2) — a spec whose core is web research cannot depend on a test-only client |
| **D9** | **A research run is metered as one `AIUsageEvent` per model call, plus a `research_run` marker event** | `VENDOR:164` flags that a run ≠ one call but does not resolve it. Metering per model call is the honest unit — it is what actually costs money, and SPEC-004's meter already counts exactly that. The marker event exists so `usage()` can show "3 research runs" to a human while billing the real call count underneath. Anything else either under-bills a 20-call run or invents a second accounting system |
| **D10** | **`MAX_TOOL_ROUNDS` becomes a per-call argument, defaulting to today's 5** | `agent.py:12` is a module constant (F3). A research run legitimately needs more rounds than a chat reply; raising the constant globally would loosen every other AI path in the product at the same time. Parameterise, default unchanged |
| **D11** | **Confidence is a stored number with a stated meaning, and low-confidence profiles are shown as such** | `VENDOR` §4 emits `ai_confidence` without saying what it gates. At D0 it gates **presentation, not storage**: a low-confidence candidate is still returned, visibly marked, because the owner is the human gate at this stage. D1 needs a publish threshold; that is SPEC-009's problem |
| **D12** | **The agent may not invent a vendor.** A candidate with no fetched source is dropped, not returned with an empty citation list | The single highest-value guard in the phase. A fabricated business is indistinguishable from a researched one at the UI, and the customer will phone the number. A5 asserts it |
| **D13** | **`ResearchSnapshot` is PRESERVED by the account-deletion purge, not deleted** | It carries no `account_id` (D2) so SPEC-005's `Base.metadata` sweep never sees it — but that is an accident of the sweep, not a decision, and §0.4 requires stating it. Snapshots are public-web facts about a *business*, retained for provenance and diffing; they contain nothing about the requesting account (D5). **The requesting `account_id` is recorded only in that account's own usage metering**, which the purge does delete |

### 1.3 `OPEN — needs decision: founder`

| # | Question | Why it cannot be defaulted | What it blocks |
|---|---|---|---|
| **O1** | **Counsel sign-off on third-party research aggregation** — storing AI-written summaries of named real businesses, even for a private shortlist | `VENDOR:295` requires this **before D0**, not before the public directory: external summaries are stored even for private shortlists. It is a legal judgement about defamation and data-provenance posture, and `VENDOR` §7's links-not-content stance is described in its own text as "a recommendation, not a ruling" | **Shipping D0 to a customer — not building it.** Every step below is testable against fixtures with no live research. Same shape as SPEC-004 O1: the code completes, the launch waits. Do not put researched profiles in front of a real user until this closes |
| **O2** | **The per-run cost ceiling** — how many model calls and fetches one research run may consume before it stops | A run is unbounded by construction: more rounds find more sources. `PRICING` §5.3's overage buffer governs an account's monthly total, not a single run, so one pathological query can burn a month's quota in a minute. The number is a cost/quality tradeoff nobody has made | **One config value.** D10's per-call `max_rounds` and D9's metering both read it; the pipeline is complete and testable with any value |

Everything else this phase depends on is settled.

### 1.4 What this spec resolves for the specs above it

| Item | Resolution |
|---|---|
| `VENDOR:299` — account deletion vs. published reviews | **Already resolved** by founder decision 2026-08-05 (anonymize), recorded in SPEC-005 **D18** and answered in `VENDOR` §10. **D0 creates no reviews**, so nothing here exercises it; SPEC-010 inherits a settled rule instead of an open question |
| SPEC-005 D18's empty `anonymize` category | Still empty after D0 (§0.4). `ResearchSnapshot` is **preserve** (D13), stated rather than left to the sweep's accident |
| `VENDOR:164` — a run is not one AI call | **Closed by D9** |
| `VENDOR:129` — `agent_stream` bypasses the provider abstraction; `MAX_TOOL_ROUNDS` is a constant | The bypass was **already closed by SPEC-004 Step 9** (its D17/F8). The constant is closed here by **D10** |
| `VENDOR:207` — moderation acts on global rows but `audit_log` is per-account | **Not this stage.** D0 has no moderation because it has no public rows. SPEC-009 needs the global/ops audit table; §7 names it |

### 1.5 Survey findings that shaped this spec

Six findings, verified against `origin/main` @ `be8d398` on 2026-08-05. Negatives stated as
negatives, per `README.md:154`.

| # | Finding | Consequence |
|---|---|---|
| **F1** | **All 18 AI tools are read-only local queries.** `services/ai/tools.py` exposes `execute_tool`, `tool_label`, `_parse_enum` and 15 `_query_*` functions against the local DB. **No web, search, fetch or HTTP tool exists** | D8. The flagship capability has no foundation in the tree; §5.1 builds it. Also confirms SPEC-004 F7's finding from the other direction — that survey noted no AI tool exposes a gated feature; this one adds the first tool that costs money per invocation |
| **F2** | **`httpx` is a `dev` extra, not a runtime dependency** (`pyproject.toml` `[project.optional-dependencies] dev`) | D8. Shipping web research on a test-only HTTP client would work in CI and fail in production — the worst failure ordering |
| **F3** | **`MAX_TOOL_ROUNDS = 5` is a module constant** at `services/ai/agent.py:12`, not a parameter | D10. `VENDOR:129` flagged this and `PRD_REVIEW:56` verified it; it is one of the few gateway-adjacent claims in the doc set that held up |
| **F4** | **The private `Vendor` model already carries most of what research produces** — `company_name`, `contact_name`, `phone`, `email`, `service_categories` (JSON), `service_areas` (JSON), `contacts` (JSON), `website`, `license_number`, `insurance_info`, `notes` (`models/vendor.py:20-33`) | Promotion (D6) is mostly a field copy, not a schema problem. What it lacks is provenance — hence `global_vendor_id` and the citation trail in §4 |
| **F5** | **`Vendor.id` is `Integer, autoincrement=True`** (`models/vendor.py:22`) | The whole tree is pre-UUIDv7; SPEC-002 D2 remaps every PK. §4 is written against the post-SPEC-002 design and will be wrong if that migration lands differently — the standard divergence caveat, restated because this spec sits five phases up |
| **F6** | **The AI meter and its wrapper already exist in spec form** — SPEC-004 §4.2's `AIUsageEvent`/`AIUsageRollup` and `MeteredProvider` proxying the full provider surface | D9 needs no new metering machinery: a research run's model calls flow through `get_provider()` and are counted, provided §5's pipeline does not construct its own client. A6 asserts it, because that is exactly the bypass SPEC-004 F8 found in `agent_stream` |

---

## 2. Doc-fix prerequisites

`VENDOR_DISCOVERY_PRD.md` is one of the **trustworthy** documents — `PRD_REVIEW:56-57` verified its
code claims and they held. Two edits only, both small.

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `VENDOR` §9's D0 row — "reuses agent loop + new web tools" | Say plainly that **the web tools do not exist** (F1) and neither does a runtime HTTP client (F2). "Reuses" implies a foundation that is absent, and that is the same word `PRD_REVIEW` **G6** caught misleading in another PRD ("*reuse* `require_permission`", which was a spec, not code) |
| **B2** | `VENDOR:164` — "a vendor research run ≠ one AI call under `PRICING` §5's unit" | Record **D9**'s resolution: metered per model call, plus a `research_run` marker for human-readable usage. The PRD flags the problem and stops |

**Already landed** (2026-08-05 doc-fix pass, commit `8790fd2`): `VENDOR:92`'s stale cross-reference
to `MULTITENANCY` deleted, and `VENDOR` §10's account-deletion question answered. Neither is
reopened here.

---

## 3. File manifest

### New — research pipeline

```
src/mihomes/services/discovery/__init__.py
src/mihomes/services/discovery/research.py       run_research — the agent loop, citations, confidence
src/mihomes/services/discovery/extract.py        fetched page -> structured candidate + citations
src/mihomes/services/discovery/cache.py          (business identity, category) lookup — D5
src/mihomes/services/discovery/promote.py        candidate -> the account's private Vendor row (D6)
```

### New — web access (D8)

```
src/mihomes/services/ai/web_tools.py             web_search + web_fetch, the ONLY outbound HTTP
```

### New — models / migration

```
src/mihomes/models/research_snapshot.py          ResearchSnapshot — global, no account_id (D2)
src/mihomes/models/vendor_candidate.py           VendorCandidate — TenantOwned, the shortlist
alembic/versions/xxxx_discovery_d0.py            two tables. NO GlobalVendor, NO VendorReview (D0)
```

### New — web

```
src/mihomes/web/routes/discovery.py              research request, results, promote (Pro/Estate, D7)
src/mihomes/web/templates/discovery.html
```

### Modified

| File | Change |
|---|---|
| `services/ai/tools.py` | Register `web_search` / `web_fetch` in `execute_tool`'s dispatch and `tool_label` |
| `services/ai/agent.py` | `MAX_TOOL_ROUNDS` becomes a per-call argument, default 5 (D10) |
| `entitlements/limits.py` | A `vendor_research` key, Pro/Estate `true` (D7) |
| `pyproject.toml` | **`httpx` moves from the `dev` extra to runtime** (F2); coverage `omit` for `web_tools.py` |

**No migration touches `vendors`.** Promotion writes existing columns (F4). A `global_vendor_id`
FK is D1's, not D0's — adding it here would create a column pointing at a table no spec has built.

---

## 4. Schemas as code

### 4.1 `vendor_candidate` — the shortlist, tenant-owned

```python
# src/mihomes/models/vendor_candidate.py
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.db import Base
from mihomes.ids import new_id
from mihomes.models.mixins import TenantOwned


class VendorCandidate(Base, TenantOwned):
    """One researched business, proposed to one account. NOT yet a Vendor (D6).

    TenantOwned: a shortlist is the account's own working set — which businesses it
    asked about is private, even though the facts inside are public. Deleted with the
    account by the default disposition (SPEC-005 D18).
    """

    __tablename__ = "vendor_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    # What the owner asked for, kept so a stale shortlist is interpretable later.
    query_category: Mapped[str] = mapped_column(String(100), nullable=False)
    query_area: Mapped[str] = mapped_column(String(200), nullable=False)

    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    website: Mapped[str | None] = mapped_column(String(500), nullable=True)
    service_categories: Mapped[list | None] = mapped_column(JSON, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 0-100. Gates PRESENTATION at D0, never storage (D11): a low-confidence candidate
    # is shown, marked, because the owner is the human gate at this stage.
    ai_confidence: Mapped[int] = mapped_column(Integer, nullable=False)

    # THE guard. [{"claim": "...", "url": "...", "fetched_at": "..."}]. A candidate
    # with an empty list is never persisted (D12) — a fabricated business looks exactly
    # like a researched one once it reaches the UI, and the customer phones the number.
    citations: Mapped[list] = mapped_column(JSON, nullable=False)

    researched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Set when the owner adopts it (D6). Keeps the shortlist honest about what was
    # actually useful, which is the D0 success metric VENDOR §9 asks for.
    promoted_vendor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
```

### 4.2 `research_snapshot` — global, and PRESERVED by the purge

```python
# src/mihomes/models/research_snapshot.py
class ResearchSnapshot(Base):
    """One research run's raw findings about one business. GLOBAL — no account_id (D2).

    The shared cache's backing store: the same local plumber is researched once and
    reused across every account (D5). Safe as a cross-tenant row because its inputs and
    outputs are public-web facts only, and the cache key is (business identity,
    category) — never anything tenant-derived.

    PURGE DISPOSITION: PRESERVE (D13). It carries no account_id, so SPEC-005's
    Base.metadata sweep never reaches it — but that is an accident of the sweep, and
    SPEC-005 D18 requires the disposition be stated rather than inferred. It is
    preserved on purpose: these are facts about a *business*, and they contain nothing
    about the account that triggered the run. The requesting account_id lives only in
    that account's own usage metering, which the purge does delete.
    """

    __tablename__ = "research_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    # The cache key (D5). Identity is name + phone + coarse area, per VENDOR §4's
    # dedupe rule — deliberately NOT a URL, which changes without the business changing.
    business_identity: Mapped[str] = mapped_column(String(300), nullable=False, index=True)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)

    raw_findings: Mapped[dict] = mapped_column(JSON, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, nullable=False)
    ai_confidence: Mapped[int] = mapped_column(Integer, nullable=False)

    # Gates refresh. VENDOR §4.4's ~90-day cadence is PLACEHOLDER; it reads config.
    researched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
```

### 4.3 Migration — two tables, one RLS policy, one carve-out

```python
# alembic/versions/xxxx_discovery_d0.py
def upgrade() -> None:
    # ... vendor_candidates (with account_id FK) and research_snapshots (without) ...

    op.execute("ALTER TABLE vendor_candidates ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY vendor_candidates_tenant_isolation ON vendor_candidates
        USING (account_id = current_setting('app.current_account', true))
    """)

    # research_snapshots is deliberately NOT RLS-enabled — it is the documented global
    # exception (VENDOR §3.4, MULTITENANCY §3.3). It must be READ-ONLY to request
    # handlers: writable only by the research pipeline, exactly as `users` is writable
    # only by auth flows. A9 asserts both halves.
    #
    # NO GlobalVendor and NO VendorReview here. Those are D1/D2 (SPEC-009/010).
```

---

## 5. Function signatures

### 5.1 Web access — `services/ai/web_tools.py` (D8)

```python
def web_search(query: str, *, limit: int = 10) -> list[SearchResult]:
    """Search the public web. THE first outbound-network tool in the AI surface.

    All 18 existing tools are read-only local queries (F1); this is a different kind of
    capability and is deliberately narrow. Two named tools with fixed shapes, not a
    general HTTP escape hatch — an AI tool that can make arbitrary requests is an SSRF
    surface pointed at the hosting network.
    """


def web_fetch(url: str) -> FetchedPage:
    """Fetch one page and return extracted text plus the final URL.

    Refuses: non-http(s) schemes, private/link-local address ranges, and redirects that
    leave the public internet. The model chooses these URLs from search results, so the
    caller is effectively untrusted input.

    Returns the FINAL url after redirects — that is what gets cited (D4), and citing a
    pre-redirect URL attributes a claim to a page that did not make it.
    """
```

### 5.2 The pipeline — `services/discovery/`

```python
# research.py
def run_research(session: Session, account: Account, *, category: str, area: str,
                 max_rounds: int | None = None) -> list[VendorCandidate]:
    """The flagship. can(account, "vendor_research") must be Allowed (D7).

    Order: cache lookup (D5) -> agent loop with web_search/web_fetch -> extract with
    citations -> confidence -> persist candidates + snapshot.

    Every model call goes through get_provider(), so SPEC-004's MeteredProvider counts
    it (D9, F6). Do NOT construct a provider here — that is the exact bypass SPEC-004
    F8 found in agent_stream, and it would make research the one uncapped path in the
    product. A6 asserts it statically.

    `max_rounds` defaults to O2's config value, not to agent.py's 5 (D10).
    """

# extract.py
def extract_candidate(page: FetchedPage, *, category: str) -> Candidate | None:
    """Structured facts + citations from one fetched page.

    Returns None rather than a citation-less candidate (D12). Every field that reaches
    a customer must trace to a URL that was actually fetched — a claim the model
    produced from its own weights is not research, it is invention wearing research's
    formatting.
    """

# cache.py
def lookup(session: Session, *, business_identity: str, category: str,
           max_age_days: int) -> ResearchSnapshot | None:
    """Shared across ALL accounts (D5). Never keyed on account_id, property, or
    anything else tenant-derived — that is the property that makes a cross-tenant
    cache safe rather than a leak."""

# promote.py
def promote(session: Session, account: Account, candidate: VendorCandidate) -> Vendor:
    """Copy a candidate into the account's private Vendor list. Owner action only (D6).

    Mostly a field copy — the Vendor model already carries company_name, contact_name,
    phone, email, service_categories, service_areas, website, license_number (F4).
    Citations travel into `notes` so provenance survives the copy; a promoted vendor
    that has lost its sources is indistinguishable from one the owner typed in.
    """
```

---

## 6. Sequenced steps

Each step ends in a green test or an observable behaviour. Three ordering constraints bind:
**Step 1 before Step 3** (web access before the loop that uses it), **Step 3 before Step 5**
(candidates exist before they can be promoted), and **Step 2 before Step 4** (the cache exists
before the pipeline writes snapshots to it).

**Step 1 — web access.** `web_tools.py`, `httpx` promoted to a runtime dependency, both tools
registered in `execute_tool`. *Verify:* a private-IP URL, a non-http scheme, and a redirect off the
public internet are each refused (A1); a fetch returns the **final** post-redirect URL (A2).

**Step 2 — the two tables.** §4.1, §4.2, §4.3 including the RLS carve-out. *Verify:* the migration
applies and reverts (A3); `research_snapshots` has **no** RLS policy and is not writable through a
request-path session (A9).

**Step 3 — the research loop.** `run_research` with per-call `max_rounds` (D10) and extraction with
citations. **After Step 1.** *Verify:* a run against fixture pages produces ≥3 candidates each
carrying ≥1 citation (A4); **a candidate with no fetched source is dropped, never returned** (A5);
every model call is metered (A6).

**Step 4 — the shared cache.** `cache.py`, snapshot writes, refresh cadence from config.
*Verify:* a second account researching the same business hits the cache and burns **zero** model
calls (A7); the cache key contains nothing tenant-derived, asserted statically (A8).

**Step 5 — promotion.** `promote.py` and the owner-only route. **After Step 3.** *Verify:* a
promoted candidate becomes a `Vendor` in the promoting account only (A10); its citations survive
into the vendor record (A11); a non-owner is refused (A12).

**Step 6 — the entitlement gate.** `vendor_research` in the limits module, Pro/Estate only.
*Verify:* Free is denied and the `Denied` names its upgrade target (A13).

**Step 7 — the cost ceiling.** O2's config value bounding rounds and fetches per run.
*Verify:* a pathological query stops at the ceiling rather than consuming the account's monthly
quota (A14).

**Exit criterion check.** With Steps 1–7 green: an owner researches a category, gets a cited
shortlist, promotes one, and the run is metered as more than one call. That is A15.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not build `GlobalVendor` or `VendorReview`.** They are D1 and D2 (§0.3). Creating either
here means creating moderation, claim flows, publish thresholds and the review-deletion machinery
that `VENDOR` §10's remaining eight questions are about.

**N2 — Do not give the agent general HTTP.** D8: two named tools with fixed shapes. A tool that
takes an arbitrary method, headers and body is an SSRF surface aimed at the hosting network, chosen
by a model from text it read on the internet.

**N3 — Do not store a candidate without citations.** D12. This is the guard the whole legal posture
rests on, and a fabricated business is indistinguishable from a researched one at the UI.

**N4 — Do not construct an AI provider inside the pipeline.** Route through `get_provider()` so
SPEC-004's meter sees every call (F6). SPEC-004 F8 found exactly this bypass in `agent_stream`;
research is a far more expensive path to leave uncapped.

**N5 — Do not key the research cache on anything tenant-derived.** D5. The moment the key includes
an account, property or user, a shared row stops being public-web facts and becomes one customer's
data served to another.

**N6 — Do not raise `MAX_TOOL_ROUNDS` globally.** D10 parameterises it. Raising the constant
loosens every AI path in the product — chat, assessors, gateways — to solve a problem in one.

**N7 — Do not promote automatically, however confident the model is.** D6. The owner adopting a
vendor is the human gate that makes a wrong profile a rejected suggestion rather than a bad phone
call.

**N8 — Do not ship researched profiles to a real customer before O1 closes.** Building is fine;
`VENDOR:295` gates *shipping* on counsel, and it says before **D0**, not before the directory.

**N9 — Do not treat `ai_confidence` as a storage gate at D0.** D11: it marks presentation. A
publish threshold is D1's problem, when there is no human between the model and the reader.

**N10 — Do not add `Vendor.global_vendor_id` yet.** It would point at a table no spec has built.
D1 adds both together.

### `DEFERRED (Stage N)` — leave room, do not build

| Item | Stage | Interface room to leave |
|---|---|---|
| `GlobalVendor` directory, browse/search | D1 (SPEC-009) | `ResearchSnapshot` is already the compiled-profile source; D1 promotes snapshots into public rows |
| `VendorReview` public stars | D2 (SPEC-010) | **Its `account_id` and `author_membership_id` must be NULLABLE** — SPEC-005 **D18** anonymizes them on account deletion, and a `NOT NULL` column cannot be anonymized |
| Global/ops audit table for moderation | D1 | `VENDOR:207` — moderation acts on global rows while `audit_log` is per-account. Same shape, no `account_id` |
| Claim-this-listing, vendor monetization | D3 | `GlobalVendor.claimed_by_account_id` is nullable **and must be nulled on account deletion** (SPEC-005 A29b) — a global table the purge never sees |
| Private→public review opt-in | D2 | `VendorRating` stays private; opting in is a copy, not a migration |
| Open/click-style research telemetry, cache-hit dashboards | D1+ | `ResearchSnapshot.researched_at` and the meter already carry the data |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | Private-IP, non-http and off-internet-redirect URLs are all refused | `test_web_tools.py::test_url_refusals` |
| A2 | A fetch returns the final post-redirect URL, and that is what gets cited | `test_web_tools.py::test_final_url_cited` |
| A3 | The D0 migration applies and reverts cleanly | `test_migration_discovery.py::test_up_down` |
| A4 | A research run yields ≥3 candidates, each with ≥1 citation | `test_research.py::test_cited_candidates` |
| A5 | **A candidate with no fetched source is dropped, never returned** | `test_research.py::test_no_uncited_candidates` |
| A6 | **Every model call in a research run is metered** — no provider constructed in-pipeline | `test_research.py::test_all_calls_metered` |
| A7 | A second account researching the same business hits the cache and makes zero model calls | `test_cache.py::test_cross_account_hit` |
| A8 | The cache key contains nothing tenant-derived — asserted statically | `test_cache.py::test_key_has_no_tenant_data` |
| A9 | `research_snapshots` has no RLS policy **and** is not writable through a request-path session | `test_discovery_tenancy.py::test_global_readonly` |
| A10 | A promoted candidate becomes a `Vendor` in the promoting account only | `test_promote.py::test_tenant_scoped` |
| A11 | Citations survive promotion into the vendor record | `test_promote.py::test_provenance_preserved` |
| A12 | A non-owner cannot promote | `test_promote.py::test_owner_only` |
| A13 | Free is denied research and the `Denied` names its upgrade target | `test_discovery_gates.py::test_free_denied` |
| A14 | A pathological query stops at the cost ceiling | `test_research.py::test_cost_ceiling` |
| A15 | **End to end: research → cited shortlist → promote → metered as >1 call** | `test_discovery_e2e.py::test_exit_criterion` |

**A5 is the phase's definition of done.**

> **A fabricated vendor is indistinguishable from a researched one.** Both arrive as a company
> name, a phone number and a confident summary, rendered in the same template. The customer cannot
> tell them apart, and neither can any test that checks the *shape* of a candidate rather than its
> provenance. The only difference is whether a URL was actually fetched.

This is the research analogue of SPEC-004's A11 and SPEC-005's A15, and it is written as an
**enumeration**: A5 must walk every field that reaches the UI and assert each traces to a citation
whose URL appears in the run's fetch log. A test that asserts "citations is non-empty" passes on a
model that invents plausible URLs — the assertion has to be against what the fetcher actually
retrieved.

**A15 is the exit criterion.** If A15 is red the stage has not shipped, whatever else is green.

---

## 9. Test manifest

```
tests/unit/test_web_tools.py             URL refusals, redirect handling, final-URL citation
tests/unit/test_cache.py                 cross-account hit, key has no tenant data
tests/unit/test_discovery_gates.py       Pro/Estate only, Denied names its target
tests/unit/test_discovery_tenancy.py     the global carve-out (A9) — static schema assertion
tests/integration/test_research.py       THE enforcement tests (A5, A6) — fixture pages
tests/integration/test_promote.py        scoping, provenance, owner-only
tests/integration/test_migration_discovery.py  up/down — own engine, real Alembic
tests/integration/test_discovery_e2e.py  THE exit criterion (A15)
```

**Fixtures.**

- **A captured page set** — real HTML from a handful of vendor sites and a search-results page,
  stored as files. Every research test runs against these, so the suite never touches the network
  and never depends on a third party's uptime or their content changing under it.
- **`FakeSearchProvider`** — returns the fixture set for a known query, satisfying `web_search`
  structurally. Same no-subclassing precedent as `AIProvider`.
- **`account_pro` and `account_estate`** from SPEC-004, plus a **second populated account** — A7
  and A10 are meaningless with one tenant.

**Coverage.** Add `web_tools.py` to `pyproject.toml`'s `omit`, on the same reasoning that omits
`stripe_provider.py` and the AI provider HTTP implementations: the seam worth testing is the
refusal logic, which A1 covers directly, not `httpx`'s behaviour.

**The adversarial pattern for A5.** Not "does a candidate have citations" — a model that invents
plausible URLs passes that trivially. Instead: record every URL `web_fetch` actually retrieved
during the run, then assert **every customer-visible field on every returned candidate** traces to
one of them. The test must fail when the model produces a phone number from its own weights and
attaches a real-looking source to it.

---

## 10. What this stage does not make safe

- **Research accuracy is bounded by the open web, and nothing here measures it.** A5 proves every
  claim traces to a fetched page. It does not prove the page was *right* — a vendor site listing a
  disconnected number, a stale directory, an aggregator that never verified anything. Provenance is
  not truth, and a cited wrong answer is still a wrong answer the customer will act on.
- **Counsel has not signed off (O1).** `VENDOR:295` requires it before D0. Everything here is
  buildable and testable, but putting AI-written summaries of named real businesses in front of a
  customer before that closes is the risk the requirement exists to prevent.
- **The cost ceiling is a guess until O2.** A14 proves the ceiling *works*; it cannot prove the
  number is right. Set too low, research returns thin shortlists; too high, one query can consume
  a month's quota.
- **`web_fetch` is an outbound-request surface chosen by a model.** A1's refusals cover the known
  classes — private ranges, odd schemes, redirects off the public internet — but the URL still
  comes from text the model read on the internet. This is the first place in the product where
  that is true, and the refusal list will need revisiting as the threat model does.
- **The shared cache means one account's staleness is everyone's.** A snapshot older than the
  refresh cadence serves every tenant equally, and a business that closed yesterday is cached for
  all of them until the cadence expires (`VENDOR` §4.4's ~90 days is `PLACEHOLDER`).
- **Nothing here addresses D1–D3's ten open questions.** Cold-start density, moderation budget,
  match quality, cannibalization, and the rest are real and unresolved — D0 simply does not
  encounter them (§0.3). Whoever writes SPEC-009 inherits all of them at once.
- **Everything SPEC-005 §10 shipped GA with is unchanged**, and this stage adds a research
  pipeline to that list rather than subtracting from it.
