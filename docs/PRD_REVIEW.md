# MiHomes SaaS PRD Set — Cross-Document Review

**Reviewed:** 2026-07-29
**Scope:** all **12** planning docs — 10 in `docs/product/`, 2 in `docs/architecture/` — 4,304 lines
**Purpose:** verify the doc set is coherent and unambiguous before implementation specs are written against it

> **Sections A–F** cover the original 10 docs. **Section G** covers
> `OMNICHANNEL_GATEWAY_PRD.md` and `WHATSAPP_GATEWAY_PRD.md` (added later in commit
> `67252f3`), which are held to a **lower standard of trust** — their factual claims about
> the existing code do not hold up. **Section H** is the consolidated open-question
> inventory across all 12.

---

## Verdict

**The docs are unusually well written** — better than most funded-startup PRDs. They cite
real file paths with line numbers, mark placeholders honestly, name rejected alternatives
with rationale, and anticipate sharp edges most planning docs miss entirely:

- pooled-connection GUC leakage across requests (`MULTITENANCY.md` §7)
- `with_loader_criteria` not covering raw Core `text()` queries (§4.1)
- Postgres `SET LOCAL` not accepting bind parameters (§4.2)
- webhook out-of-order delivery and `construct_event` on raw bytes (`BILLING_AND_EMAIL.md` §6)
- DMARC relaxed-vs-strict alignment against Resend's return-path (§3)
- CDA §230 not shielding AI-authored profile text (`VENDOR_DISCOVERY_PRD.md` §7)

**The problem is not quality. It is that the docs disagree with each other on ~14 concrete,
code-shaped details**, and three migration decisions are written as forward references to
choices nobody has made. A spec written from the wrong side of any one of these produces a
wrong table or a wrong migration.

Everything below is hours of editing, not days.

### Correction to an earlier review

An earlier pass of this review claimed the **entitlements service** and **AI usage tracking**
specs were missing blockers. **Both claims were wrong and are retracted.** Both are fully
specified:

- Entitlements: `PRICING_AND_PACKAGING.md` §3.2 — the `can()` / `usage()` contract with five
  contract rules including fail-closed-on-paid and transactional race handling.
- AI metering: `PRICING_AND_PACKAGING.md` §5 — unit definition, what counts vs. doesn't,
  reset anchor, 80%/100% nudges, soft cap, hard-ceiling formula, and the pre-provider
  enforcement point.

### Verified against code

| Claim | Source | Actual |
|---|---|---|
| 28 model modules | `SAAS_PRD.md:12`, `MULTITENANCY.md:22` | ✅ 28 |
| 36 tables | both | ✅ 36 |
| 36 Alembic revisions | both | ✅ 36 |
| 23 web-route modules | `SAAS_PRD.md:12` | ✅ 23 |
| 24 route modules | `MULTITENANCY.md:32` | ❌ **wrong — 23** |
| `MAX_TOOL_ROUNDS = 5` module constant | `VENDOR_DISCOVERY_PRD.md:129` | ✅ `agent.py:12` |
| `agent_stream()` hardwired to Anthropic SDK | `VENDOR_DISCOVERY_PRD.md:129` | ✅ `agent.py:76,79` |
| `configurations` PK is bare `key` | `MULTITENANCY.md:150` | ✅ `configuration.py:12` |
| No `homes` table exists | — | ✅ confirmed: `properties` only |

Note the contrast with §G: every code claim in the original ten docs that I checked held up.
The two later gateway PRDs are where the citations break down.

---

## A. Blocking contradictions

### A0. The entitlements service has three different phase assignments ⚠ worst one

The most load-bearing new service in the build, and the docs disagree on when it ships:

| Says | Where |
|---|---|
| **Phase 3** | `README.md:61` — "Phase 3 \| Billing / Freemium \| … **entitlements service**, plan gates" |
| **Phase 3** | `MULTITENANCY.md:466` — "Phase 3 — billing/freemium (Stripe) + entitlements" |
| **Phase 2** | `SAAS_PRD.md:179` — Phase 2 delivers "entitlements service (config-only: all accounts Free)" |
| **Phase 1–2** | `PRICING_AND_PACKAGING.md:250` — "built alongside the multitenant foundation (Phase 1) and RBAC/invites (Phase 2)" |

`README.md:60` omits entitlements from Phase 2 entirely. And `SAAS_PRD.md:143` states the
consequence outright:

> "Phase 2 ships the entitlements service reading plan limits from config… **Without this,
> Phase 2 would secretly depend on Phase 3.**"

So README and MULTITENANCY schedule it into exactly the phase the master PRD warns is broken.
Anyone speccing Phase 2 from README — the doc that says "Start here" — builds seat/home/staff
gates with no entitlements service to call.

**Fix:** README Phase 2 row gains "entitlements service (config-only)"; Phase 3 row becomes
"billing status wired **into** entitlements". Same edit at `MULTITENANCY.md:466`.

### A1. `memberships.status` — does `invited` exist?

- `MULTITENANCY.md:112` — status is `active` | `revoked`
- `MULTITENANCY.md:118` — "This is why `status` has no `invited` value." (explicit)
- `PRICING_AND_PACKAGING.md:98` — "A **seat** = one `memberships` row … with status `active`
  **or** `invited` (pending)"

PRICING defines the seat-counting rule — the thing the entitlements service enforces —
against a status value MULTITENANCY says does not exist. Seat math is wrong on one side.

**Fix:** PRICING §3.1 becomes "active `memberships` + pending `invites` rows" (two tables,
not one status enum). Everything downstream already assumes this.

### A2. Ownership — two different mechanisms

- `ONBOARDING_AUTH_RBAC.md:35,43` and §8.1 — an `accounts.owner_user_id` column
- `MULTITENANCY.md:70-83` — accounts has **no** such column; `:120-121` enforces one owner
  via a partial unique index on `memberships` (`WHERE role='owner' AND status='active'`)

Both work. Having both is a dual source of truth that will drift, and transfer logic plus the
baseline migration differ depending on which wins.

**Fix:** pick the partial unique index. Membership is already the authorization source of
truth (`ONBOARDING_AUTH_RBAC.md` §9.4 loads it fresh every request); a denormalized
`owner_user_id` would have to stay in sync with it.

### A3. `MULTITENANCY.md` §3.1 is not the union of what other docs write to it

| Column | Named in | Status in §3.1 |
|---|---|---|
| `accounts.type` (`household`\|`estate`) | `ONBOARDING:35,133` — mandatory onboarding step 2 | missing |
| `accounts.trial_ends_at` | `PRICING:143`, `BILLING:485` | missing |
| `accounts.trial_used_at` | `PRICING:143` — "one trial per account, ever" | missing |
| `memberships.invited_by` | `MULTITENANCY:112` | present, but `ONBOARDING:36` omits it |

**Name collisions:**
- `PRICING:143` calls it `billing_status`; `MULTITENANCY:80` and `BILLING:365` call it
  `subscription_status`.
- `MULTITENANCY:101` has `users.last_login_at`; `ONBOARDING:34,66,109` has `last_login`.

The trial columns matter most — the no-card trial is a **locked** pricing decision
(`PRICING` §4.2) that cannot function without app-side state, and `BILLING:485` independently
confirms it must be app-managed because no Stripe subscription exists during it.

**Fix:** reconcile §3.1 column-by-column against `ONBOARDING` §2, `PRICING` §4.2, and
`BILLING` §5. One name per field.

### A4. The `homes` table does not exist

- `ONBOARDING_AUTH_RBAC.md:44` — `membership_home_scopes (membership_id, home_id)`
- `ONBOARDING_AUTH_RBAC.md:51,134` — mermaid `accounts ||--o{ homes`; step 3 creates the
  "first `homes` row"
- Code: `src/mihomes/models/property.py:30` — `__tablename__ = "properties"`

**No `homes` table exists**, and no doc proposes a rename. So
`membership_home_scopes.home_id` has no defined FK target.

"Home" is correct *product* language and `max_homes` as an entitlement key is fine. The
problem is only that ONBOARDING uses it as a literal table and column name.

**Fix:** state once, in `MULTITENANCY`, that home = `properties` row. FK becomes
`membership_property_scopes.property_id REFERENCES properties(id)`. Keep "home" in UI copy.

### A5. The baseline migration omits every table added after §3.1 was written

`MULTITENANCY.md:340-346` (§5.2 step 1) creates "`accounts`, `users`, `memberships` +
constraints **and** all 36 domain tables". Grepping the whole doc set for named tables, seven
are required somewhere and appear in neither §3.1 nor the §5.2 baseline:

| Table | Required by | Tenancy |
|---|---|---|
| `invites` | `ONBOARDING` §6.1:166 | tenant-owned — confirm |
| `membership_home_scopes` | `ONBOARDING` §2:44 | tenant-owned (joins two tenant rows) |
| `sessions` | `ONBOARDING` §3.3:76 | **global** ⚠ |
| `processed_webhook_events` | `BILLING` §6:410 (unique constraint required) | **global** ⚠ |
| `waitlist` | `GTM` §4:251 | ships **Phase 0**, before `accounts` exists |
| `telegram_links` | `TELEGRAM` §8:185 | defer (see B4) |
| `telegram_chat_links` | `TELEGRAM` §8:185 | defer (see B4) |

Two are bootstrap-class — the same problem `memberships` already has a documented carve-out
for (`MULTITENANCY:310-318`). **`sessions`** is read by auth middleware *before* any account
context exists. **`processed_webhook_events`** is written by the webhook route that
`BILLING:414` explicitly excludes from tenant-scoping middleware. Give either a naive
`app.current_account` RLS policy and it returns zero rows — locking out every login and
silently reprocessing every Stripe webhook. **`waitlist`** is the reverse hazard: it ships in
Phase 0 and the Phase-1 baseline must not drop it.

The real finding is not "7 tables are missing" — it is that **nothing enforces §3.1/§5.2
being the union of what the set requires**, so this gap regrows every time a doc adds a table.

**Fix:** add the 5 Phase-1 tables to §3.1 with explicit tenancy and to the §5.2 step-1 list;
the 2 Telegram tables ship with the Telegram work. Decide `sessions` and
`processed_webhook_events` global-vs-scoped now — both look global.

### A6. DMARC record — GTM contradicts BILLING's explicit warning

- `BILLING_AND_EMAIL.md:219` — `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai`
- `BILLING_AND_EMAIL.md:224` — "keep the default **relaxed** alignment (**do not set**
  `adkim=s; aspf=s`)", with the reason: Resend's bounce/return-path sits on its own
  sub-label, and strict SPF alignment would fail that legitimately-signed mail
- `GTM_LAUNCH_PLAN.md:273` — `v=DMARC1; p=none; rua=…; adkim=s; aspf=s`

`GTM:262` says BILLING "is authoritative for the email records" — then contradicts it inside
a copy-pasteable DNS table. Someone will paste the GTM row and break Phase 0 waitlist mail.

**Fix:** delete `adkim=s; aspf=s` from `GTM_LAUNCH_PLAN.md:273`. Highest
consequence-per-character edit in the set.

---

## B. Gaps that block a Phase-1/2/3 spec

### B1. What plan is the local single-tenant install on?

`MULTITENANCY.md` §6 (Phase 1) preserves local CLI mode as "one local install = one account".
Entitlements gate on `account.plan`. **Nothing states what plan the seeded local account
gets.** Free = 1 home / 3 seats — which would freeze the founder's own multi-property
production DB the moment §6 lands, and that DB is the stated migration rehearsal and first
dogfood tenant (`SAAS_PRD.md:135`).

**Fix:** local mode bypasses entitlements entirely (recommended), or seeds an unlimited
`plan='local'`. State it in §6.

### B2. Trial expiry falls between the two downgrade paths

`PRICING` §4.3:149 defines exactly two sequences: past-due (Grace → Restricted) and voluntary
downgrade (skips Grace). Trial expiry (`:141` "they simply revert to Free") is neither.

Consequence: `:159` gives the owner a home-picker "shown from day 0 of Grace". With no Grace,
a trial user who added a 2nd home never sees the picker and silently gets oldest-home-wins.

**Fix:** name trial-expiry as a third row in the §4.3 table, and say whether it gets the
picker — recommend yes, ~3 days pre-expiry alongside the `trial_ending` email.

### B3. Shared responder-core refactor has no owning phase

- `TWILIO_PRD.md:83` — "a prerequisite for the Twilio responder, **not optional cleanup**"
- `TELEGRAM_PRD.md:92` — new Telegram work "should land in that shared core"
- `SAAS_PRD.md` — never mentions it, in any phase

It modifies two **live** gateways the founder depends on daily; `TWILIO_PRD.md:251` flags the
regression risk itself.

**Fix:** give it a phase in `SAAS_PRD` §10 (as growth-bet prep, not core), or state that
Twilio absorbs the cost when it starts.

### B4. Gateway phase assignments conflict with the master PRD

`SAAS_PRD.md` §6.2 and §10:182,186 make expanded Telegram and Twilio **Phase 4+ growth
bets**, explicitly not in the hosted MVP. But:

- `TELEGRAM_PRD.md:185` — "**Phase 1** — introduce `telegram_links` / `telegram_chat_links`
  tables with `account_id`"
- `TWILIO_PRD.md:230` — "**Phase 2** | Phone-linking/verification flow built alongside
  onboarding/invites"
- `TWILIO_PRD.md:229` — "**0–1** | Start A2P 10DLC … paperwork **now**"

`TELEGRAM` §8:186 self-caveats ("a dependency floor, not committed Phase 2 scope"), which
covers *when work lands* — but `:185` still puts two new tables in the Phase 1 baseline, which
`MULTITENANCY` §5.2 does not create. The A2P item is genuinely Phase 0–1 (weeks of external
lead time) and is the one piece that *should* start early.

**Fix:** add a "growth-bet prep permitted in this phase" column to `SAAS_PRD` §10, listing
only the A2P/WhatsApp-template paperwork. Drop the Phase-1 table creation from `TELEGRAM` §8.

---

## C. Stale cross-references — delete, do not act on

Two docs flag problems **already fixed** in the target doc. Acting on them would
re-introduce the bug.

- `ONBOARDING_AUTH_RBAC.md:38` — "`MULTITENANCY.md` §3.1 currently lists an `invited`
  membership status; it should drop that." → `MULTITENANCY:112` already lists only
  `active`|`revoked`, and `:118` explains why.
- `VENDOR_DISCOVERY_PRD.md:92` — "`MULTITENANCY.md` … states '`users` is the only global
  business table' — that sentence needs amending." → `MULTITENANCY:507-513` already amends it
  and names `GlobalVendor` explicitly.

**Fix:** delete both notes. Note that C's first item and A1 are two symptoms of **one
incomplete edit**: MULTITENANCY dropped `invited` and added the `invites` table, but
ONBOARDING's note was never deleted and `PRICING:98` still counts seats against the old
model. Do it as one coordinated pass and grep for other survivors.

### C1. Factual error

`MULTITENANCY.md:32` says "24 route modules". `SAAS_PRD.md:12` says 23. Actual is **23**.

---

## D. Self-flagged and correctly deferred

Listed so they are not mistaken for gaps — each doc already names the fix and the owner. All
Phase 4+; correct to leave.

- Telegram entitlement keys absent from `PRICING` §3.1 (`TELEGRAM_PRD.md:187`)
- Twilio/SMS entitlement keys absent from `PRICING` §3.1 (`TWILIO_PRD.md:208`)
- A vendor research run ≠ one AI call under `PRICING` §5's unit (`VENDOR_DISCOVERY_PRD.md:164`)
- `agent_stream()` bypasses the provider abstraction; `MAX_TOOL_ROUNDS` is a module constant
  (`VENDOR_DISCOVERY_PRD.md:129`) — both verified in code
- Moderation acts on global rows but `audit_log` is per-account, so moderation needs a
  separate global/ops audit table (`VENDOR_DISCOVERY_PRD.md:207`)

---

## E. Absent from all ten documents

Not contradictions — topics no doc covers. The first three block Phase 1.

1. **File / object storage.** `Document.file_path` is a `String(500)` local path
   (`web/static/uploads/`), plus Telegram media in `~/.mihomes/media/telegram/` and Google
   tokens in `~/.mihomes/google/`. Migrating rows to Postgres does not move the files those
   rows point to, and local filesystem paths do not work on a multi-tenant host. **No doc
   mentions object storage, S3, or a blob strategy** — this is an entire unspecified storage
   tier, and it compounds gap 2 below (two stores to back up, only one named).
2. **Backup / disaster recovery / PITR.** ⚠ **Still open, and now sharper.** A hosted
   multi-tenant database with no stated RPO/RTO is a Phase-1 decision, not Phase-4 polish.
   The Fly decision (below) raises the stakes: Fly's own Postgres has historically been
   **unmanaged** — a Postgres app you are the DBA for, with **no implied automatic backups** —
   while Fly has since added a managed option. Those are different products. `MULTITENANCY`
   §11.1 now forces the choice in writing, but **it is not yet made.**
3. ~~**Hosting target.**~~ **Resolved 2026-07-31 — Fly.io, single region**
   (`MULTITENANCY` §11, founder call). Three consequences now written into the docs rather
   than latent: transaction-local `set_config` is a **hosting requirement** because Fly
   fronts Postgres with PgBouncer in transaction-pooling mode (§11.2); Fly volumes are
   single-machine local NVMe, so S3-compatible object storage behind `StorageProvider` is
   **mandatory**, not merely clean (§11.3); and scale-to-zero is incompatible with
   always-on scheduled work, so the trial-expiry scheduler and the daily Stripe
   reconciliation sweep need a home that doesn't sleep (§11.4).
4. Monitoring / alerting stack (`SAAS_PRD` §9 names *what* to observe, never *with what*).
5. Provider-outage behavior for Resend/Stripe beyond the `FailoverEmailProvider` sketch.
6. ToS + Privacy Policy — required by `SAAS_PRD.md:193` GA-DoD and by GTM before collecting a
   single waitlist email (`GTM_LAUNCH_PLAN.md:351` flags counsel review). No doc owns drafting.

---

## F. SQLite → Postgres migration — assessed separately

`MULTITENANCY.md` §5 is one of the stronger parts of the set.

**Well covered:** engine swap and PRAGMA removal (§5.1); squashing 36 revisions to a Postgres
baseline with sound reasoning — SQLite-isms are replay hazards and there is no hosted history
to preserve (§5.2); the one-account importer with FK-ordered streaming and post-import
validation (§5.3); a 7-row SQLite-ism conversion table (§5.4). §5.2 even flags its own catch
— local mode still needs migrations — and weighs two Alembic branches against one
dialect-aware chain, recommending the latter with `if bind.dialect.name == "postgresql"`
guards. Most migration plans miss that entirely.

**Three gaps:**

1. **The PK decision is unresolved and blocks the baseline.** §10.1 lists UUID-vs-integer as
   an open question; §5.3 step 3 then says "remap integer autoincrement PKs *per the PK
   decision in §10.1*" — the importer's most invasive step is a forward reference to an unmade
   decision. §5.4 hedges identically ("`uuid` PK or `bigint identity`"). This determines the
   baseline, every FK column, and whether the importer needs an old-id→uuid map per table.
2. **File storage** — see E1. The importer must also rewrite every `file_path`, and object
   writes are not transactional with the Postgres import §5.3 wraps in one transaction.
3. **No cutover runbook.** §5.3 gives importer mechanics but not the operational sequence:
   when the live instance stops, whether it is read-only during import, how to verify before
   flipping, and the rollback path if validation fails. The founder's DB is both rehearsal and
   first tenant, and the Telegram gateway writes to it continuously.

---

---

## G. The two later gateway PRDs — do not spec from these yet

`OMNICHANNEL_GATEWAY_PRD.md` (603 lines) and `WHATSAPP_GATEWAY_PRD.md` (661 lines) were added
in commit `67252f3`, after the other ten. **Neither `README.md` nor `SAAS_PRD.md` §13 indexes
them**, though both claim to list the complete doc set — so the doc set is 12 documents while
its own two indexes say 10.

These two are different in kind from the other ten. The original ten cite code accurately
(every count and path I checked held). These two **do not**, and the errors are load-bearing.

### G1. `WHATSAPP:44`'s divergence list is false on all five points ⚠

> "**Divergence from Telegram**: No PTO approval flow, no inventory chat routing, no
> photo-to-Document linking on issue creation, no maintenance-expert assessment appended to
> issue confirmations, no structured commands (APPROVE/DENY)."

Verified against `whatsapp/responder.py` — every one exists:

| Claim | Reality |
|---|---|
| No PTO approval flow | `_handle_approval_message()` at `:198`, called at `:281` |
| No structured APPROVE/DENY | regex at `:220-221` |
| No inventory chat routing | `whatsapp.inventory_group_jid` at `:297` → `handle_inventory_scan()` at `:300` |
| No photo→Document linking | `create_document()` at `:486` |
| No maintenance-expert assessment | `_issue_expert_reply()` at `:172`, invoked `:514` |

This claim is the premise for §2 gap #6 (`:88`), three §3 P1 rows (`:111,116,117`), and §8.2
(`:406`). Fix `:44` before anything downstream is trusted.

### G2. Category counts wrong in three directions

Actual enums: WhatsApp **8** (`whatsapp/review.py:29`), Telegram **15**
(`telegram/review.py:29-35`).

- `WHATSAPP:88` and `:406` say WhatsApp handles **4**. It handles 8. `OMNICHANNEL:12`
  correctly says 8 — **the two new docs contradict each other.**
- `WHATSAPP:34` says `task_completion` doesn't exist in WhatsApp's schema. It does.
- `WHATSAPP:406` says Telegram handles **11**; `:34` and `:369` say 15. Internal contradiction.

### G3. The prerequisite module has two different paths

`TWILIO_PRD` §2.3 canon names `gateways/core/responder.py`.
`OMNICHANNEL:50,68,584` says `core/`. `WHATSAPP:361,369,400` says `shared/`.
**Neither directory exists.** Two new docs give different paths for the module both call a
hard prerequisite. Pick `core/`.

### G4. OMNICHANNEL invents a colliding phase numbering and calls the work launch-blocking

`OMNICHANNEL:580-589` defines its own **Phase 0–4**, where its "Phase 0" is the responder-core
refactor. Canon Phase 0 is landing + waitlist with zero gateway code (`TELEGRAM_PRD:184` states
this explicitly). Same numbers, different meaning, in a set where `README` declares phases canon.

Worse, `OMNICHANNEL:64` declares "**P0 = launch-blocking**" and marks six items P0
(`:68-73`). `SAAS_PRD:186` says chat gateways are "**not part of the hosted MVP**" and a
"4+ growth bet". OMNICHANNEL never uses the words "growth bet", "post-GA", or "Phase 4+"
anywhere. `TELEGRAM_PRD:186` carries exactly the hedge both new docs lack — copy its wording.

### G5. `WHATSAPP` §16 Phase 0 is impossible on the doc's own evidence

`:159` (tier table) — Developer API is "verified numbers only. **No group support.**"
`:643` (Phase 0) — migrate to Developer API with "**no behavior change** for existing users."

The live product is group-based: `cli/whatsapp.py` has `groups`, `link-group`, `unlink-group`,
`send-group`, and `whatsapp.inventory_group_jid` routes an inventory *group*. Migrating to a
tier without group support is total loss of function. `:194` then asserts group messaging works
without tier qualification, and §17 Q8 (`:660`) *re-asks* whether Developer API supports
groups. The doc answers, contradicts, and re-asks the same question in three places.

### G6. Other verified-false code claims

| Doc:line | Claim | Verified reality |
|---|---|---|
| `WHATSAPP:71` | watchdog "**does** supervise the WhatsApp monitor" | **False** — `grep -ci whatsapp scripts/watchdog.py` = **0** |
| `OMNI:9,167` | wraps "existing **`TelegramBot` Protocol**" | No such class; it's `TelegramClient`, and there is **no Protocol** on the Telegram side — so Telegram currently *violates* the behind-a-Protocol canon |
| `OMNI:9` | WhatsApp Protocol's 4 methods as current state | Protocol exists but **nothing implements it**; 3 of 4 methods exist nowhere |
| `WHATSAPP:361` | extract "the `normalize_message()` function" | Doesn't exist. Telegram has `normalize_update()`; WhatsApp normalizes in **Node** — there is no Python WhatsApp normalizer to extract from |
| `OMNI:11` | both responders **529** lines ("parity") | **528 and 781** — the size parity that frames the whole divergence argument is false |
| `OMNI:35,358` | `AIOrchestrator.ask()` class | No such class; `orchestrator.py` is module-level functions |
| `OMNI:112`, `WA:330` | "**Reuse** `require_permission(...)`" | Does not exist in code — it's a design spec in `ONBOARDING` §9.4. "Reuse" is misleading |
| `WHATSAPP:75` | 7 `whatsapp.*` config keys | Only **4** exist (`autostart`, `inventory_group_jid`, `last_extract_ts`, `monitor_property`); 5 listed don't, and 2 real ones are unlisted |
| `OMNI:173-286` | async functions "**extracted from** the common patterns in" both responders | Both responders contain **zero `async def`**, and there is **no FastAPI webhook route anywhere**. A sync→async conversion plus building the webhook surface is unscoped work |

`OMNICHANNEL:51-60` also presents STOP/HELP handling, rate limiting, and `@estate2` account
switching as *current state* to be centralized. All three return zero grep hits.

### G7. Schema and canon violations

- **`omnichannel_dedup` has no `account_id`** (`OMNI:415-423`) — violates the canon rule. Its
  `UNIQUE(channel, sender_id, created_at)` commented "60s window dedup" doesn't implement a
  window; two rows 1 ms apart both pass.
- **All DDL is SQLite** (`INTEGER PRIMARY KEY`, `BOOLEAN DEFAULT FALSE`) against a Postgres
  Phase 1. No RLS policies mentioned for any new table.
- **`OMNI:437-444`** is headed "no changes needed" then lists column additions, and calls
  `*_chat_links` both "keep as-is" *and* "deprecated view over `omnichannel_chat_links`".
- **Phone storage contradicts itself**: `WHATSAPP:331` says numbers are stored **hashed**,
  "never logged or displayed"; `:427-429` stores `phone_hash` *and* `phone_number` ("full
  number, encrypted"). Encrypted-reversible ≠ hashed.
- **7 new tables** defined inline (4 in OMNICHANNEL, 3 in WHATSAPP) plus 2 referenced but never
  defined (`omnichannel_links` `:105`, `whatsapp_dm_context` `:139`). Neither doc defers schema
  ownership to `MULTITENANCY.md`.
- **Neither doc names a single entitlement key**, and neither carries the "add them to PRICING
  §3.1, not here" pointer that `TWILIO_PRD:208` and `TELEGRAM_PRD:187` both do. So paid
  per-conversation WhatsApp arrives with no plan gate specified anywhere.

### G8. Neither doc admits WhatsApp is currently down

`WHATSAPP:18` opens "The WhatsApp gateway is a **working** … interface" and `:96` frames Baileys
risk in the future tense. Baileys pairing is **currently broken** ("cannot link device") — which
is the single strongest argument *for* the Cloud API migration, and neither doc uses it.

**The founder question neither §17 asks:** given `SAAS_PRD` §6.2/§10 puts chat gateways at
Phase 4+ and WhatsApp is currently dark — is the Cloud API migration a **pre-GA necessity or a
post-GA growth bet**? That answer determines whether either §16 roadmap is real.

---

## H. Consolidated open-question inventory

**47 open questions across 11 formal sections; 32 `PLACEHOLDER` values** (20 in `PRICING`
alone). Most do not block spec-writing. Filtered by what actually gates work:

### Blocks Phase 0 (days)

| Question | Source | Why |
|---|---|---|
| ToS/Privacy counsel-reviewed before collecting emails? | `GTM` §9 | Legally cannot take the first waitlist email without them. "Likely yes" is not a decision |
| Founding-member offer — extended trial or annual discount? | `PRICING` Q6, `GTM` §9 | Landing copy promises an offer that isn't defined |
| Waitlist gate number | `SAAS_PRD` §14, `GTM` §8 | GTM proposes ≥250 @ ≥3%; SAAS_PRD defers to founder. Gates Phase 1 spend |
| Apex = marketing, app on subdomain? | `GTM` §9 | Marked "assumed". Determines DNS + deploy shape |

### Blocks Phase 1 (before the baseline migration)

| Question | Source | Note |
|---|---|---|
| **PK strategy: UUID vs integer** | `MULTITENANCY` Q1 | Doc says "decide before the baseline migration". Recommend UUIDv7 app-side — but `pyproject.toml:9` declares `>=3.11` while `uuid.uuid7()` is stdlib only from **3.14** |
| **Local/self-hosted edition long-term?** | `SAAS_PRD` §14 | **Highest-leverage question in the set** — see below |
| ~~Data residency / region~~ | `SAAS_PRD` §14 | **Resolved** — Fly.io, single region, US-first (`MULTITENANCY` §11) |
| **Postgres: managed or unmanaged?** | `MULTITENANCY` §11.1 | **Replaces the above as the blocker.** Fly's own Postgres has historically been unmanaged with no implied backups; a managed option now exists. Different products, different guarantees. Pick one and state RPO/RTO — see E2 |
| Account-switching carrier: subdomain / path / session? | `MULTITENANCY` Q6 | Affects §4.1 tenant resolution |
| Founder's live gateways during re-platform | `SAAS_PRD` §14 | The Telegram bot writes continuously to the DB being migrated |

**Why the local/self-hosted question dominates:** if the answer is "hosted only", a large part
of Phase 1 evaporates — the dual-mode `db.py` fork, the dialect-aware Alembic chain (§5.2
option b), the local-mode entitlements bypass (B1), and the local↔SaaS drift risk
(`MULTITENANCY` Q5). If "keep local", all of it is required scope. One answer, materially
different Phase 1.

### Deferrable with a stated leaning (no action now)

`PRICING` Q1–Q5, Q7–Q8 (pricing mix, add-ons, AI top-ups, nonprofit, fair-use ceiling,
currency) · `BILLING` §10 (tax: Stripe Tax vs merchant-of-record — the doc notes MoR "would
change the implementation but not the interface", so it is safely a Phase 3 call; also dunning
cadence, proration, refunds) · `ONBOARDING` Q1–Q6 · `MULTITENANCY` Q2–Q5, Q7–Q8 (all
engineering, with mitigations already named) · `TELEGRAM` Q1–Q8 · `TWILIO` all · `VENDOR` all.

### Lead-time items — start now even though the work is later

- **A2P 10DLC + WhatsApp template registration** (`TWILIO:229`, `OMNI:594`) — weeks, and can be
  rejected. The one growth-bet item that genuinely belongs in Phase 0–1.
- **Vendor Discovery counsel sign-off** — `VENDOR:295` requires it *before D0*, the earliest
  stage.
- **ToS/Privacy drafting** — needed for Phase 0 and owned by no doc (E6).

---

## Recommended sequence

1. **Doc-fix pass (~2–3 h)** — A0–A6, B1–B4, C, C1. Mechanical. The only real decisions are
   A2 (ownership mechanism), A3 (one name per colliding field), and A5 (`sessions` /
   `processed_webhook_events` tenancy); all are recommended above.
2. **Answer the four Phase-0 questions in H** — ToS/Privacy, founding offer, waitlist gate,
   apex. All four gate Phase 0 and none is an engineering call.
3. **Answer E1–E3 + the Phase-1 questions in H** — file storage, backup posture, hosting
   target, PK strategy, and above all **local-vs-hosted-only**.
4. **Fix or quarantine the two gateway PRDs (§G).** They are not spec-ready: their central
   factual claims about the existing code are false, they use a colliding phase numbering, and
   they contradict each other on the module path and the category counts. Also index them in
   `README` and `SAAS_PRD` §13.
5. **Then write specs — Phase 0 and Phase 1 only.** Phase 2–4 specs should wait: the
   entitlements/billing surface is already well specified, and Phase 1 will teach things about
   the scoping layer that later specs would have to absorb as rework. Gateway specs wait on
   step 4.

## Verification for the doc-fix pass

- Re-grep for `invited`, `owner_user_id`, `homes`, `adkim`, `billing_status`, `last_login`;
  confirm each survives in exactly one intended place.
- Grep every doc for "entitlements service" plus a phase number; confirm all say Phase 2
  (config-only) → Phase 3 (billing wired in).
- Confirm `MULTITENANCY` §3.1 and the §5.2 step-1 list name the **same** set of tables, and
  that the set is the union of every table named anywhere in the doc set. Re-run that grep as
  the standing acceptance check — this gap regrows.
