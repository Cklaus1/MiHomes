# SPEC-005 — Phase 4: Polish + Email Lifecycle + GA

**Phase:** 4 (canon — `../product/SAAS_PRD.md` §10)
**Status:** Ready to build — **2 open decisions** (O1: drip content + cadence; O2: deletion grace length). **3 inbound gates** from earlier specs block *launch*, not the build (§1.6)
**Written:** 2026-08-04
**Source PRDs:** `../product/SAAS_PRD.md` §9, §10 (the GA definition of done, `:189-196` — this spec's §8), `../architecture/BILLING_AND_EMAIL.md` §2 (email interfaces, §2.4's outbox, §2.6's catalogue), §3 (DNS/deliverability), `../product/PRICING_AND_PACKAGING.md` §3.1 (the three Estate keys), §4.4 (deletion + retention), `../product/GTM_LAUNCH_PLAN.md` §5–§6 (launch gates)
**Depends on:** SPEC-001 (Phase 0) — the whole `services/email/` package, `/healthz`, the `waitlist` table. SPEC-002 (Phase 1) — `account_id` on all 36 tables, RLS, `TenantOwned`, the scoped session, `StorageProvider`. SPEC-003 (Phase 2) — `can()`, `require_permission`, `audit_write()`. SPEC-004 (Phase 3) — `EmailService`'s four billing methods, the `mihomes jobs` entrypoint, `AIUsageRollup`, the entitlements limits module.

**Goal.** Take the product from *sellable* to *generally available*: make the email lifecycle real,
give a customer a way to take their data out and their account down, put eyes on the running
system, and enforce the tier that is already purchasable but undifferentiated.

**Exit criteria** (`SAAS_PRD:189-196`): the six-bullet **GA definition of done**. This is the only
spec in the set whose exit criteria were written before the spec was.

**The stake.** Every previous phase defended a boundary — between customers (Phase 1), inside one
(Phase 2), between what was paid for and what was not (Phase 3). Phase 4 is different: it is where
the product stops being something the founder operates and becomes something strangers depend on.
Three of its failure modes are silent in a way the earlier phases' were not. **A scheduler that
never fires** leaves the dunning ladder, the trial sweep and the drips all dead while every test
passes. **An export that is not tenant-scoped** hands one customer the whole database and looks
like a working feature. **A deletion that misses a table** leaves personal data behind and is a
regulatory finding, not a bug report. GA is also the last moment any of this is cheap: afterwards,
every one of them has a customer attached.

---

## 0. Four things a reader must know before trusting this spec

**0.1 — All four preceding phases are unbuilt.** The count has grown with each spec: SPEC-003 sat
on one unbuilt phase, SPEC-004 on three, this on four. Verified on `telegram-bot`, clean tree,
2026-08-04:

- `src/mihomes/services/email/` **does not exist.** A grep across `src/ --include=*.py` for
  `smtp|sendgrid|resend|postmark|mailgun|EmailProvider|send_mail|MIMEText|smtplib|boto3` returns
  **zero matches** (F1). SPEC-001 §3 creates the package; every `send_*` method this spec adds
  attaches to a class that does not yet exist.
- `config.py:14` still hardcodes `DB_URL = f"sqlite:///{DB_PATH}"`; no Postgres driver is installed
  on any branch.
- No `Account`, `User`, `Membership`, or auth layer. No `can()`. No `/healthz`.

Consequences a reader must carry:

- Every reference here to `EmailService`, `can()`, `account_id`, `TenantOwned`, the scoped session,
  or `mihomes jobs` describes **a spec, not code**.
- **Divergence compounds across four specs.** Re-verify §4 and §5 against the tree before building.
- The one thing that *is* real is the domain code this phase gates and exports — `csv_io.py`,
  `backup.py`, `audit.py`, `predictive_maintenance.py` and `ai/reports.py` all exist today, and
  §1.5's findings are about **their** current shape, not about a spec.

**0.2 — `O1` here is the fifth unrelated `O1` in this set.** Label namespaces are per-spec-local
(`README.md` §"Working on a spec"). SPEC-002's O1/O2 **closed** (→ D13/D14). **SPEC-001's O1**
(ToS + Privacy Policy), **SPEC-003's O1** (at-rest secret encryption) and **SPEC-004's O1** (launch
prices) are all **still open, and all three gate GA** — they are carried in §1.6 under their
original labels and are *not* renumbered. This spec's own O1 and O2 are different questions
entirely. A reader who resolves "O1" against the wrong spec will conclude a live launch gate is
settled.

**0.3 — What this phase inherits from SPEC-004 §10.** Nine items were declared not-made-safe
there — SPEC-003's four carried forward, plus five of SPEC-004's own. **None are addressed by the
phases in between.** §10 restates all nine alongside this phase's own, because this is the last
spec in the set and there is no §10 after it to inherit them. Do not treat the list as background:
three of the nine (secrets at rest, revenue correctness, the Stripe account's own configuration)
are things GA ships *with*, and whoever decides to launch needs them in one place.

**0.4 — "Full email lifecycle" is not specified in any PRD. This spec defines it.** Every other
spec in this set transcribed decided PRD content into buildable form. This one cannot, and the
distinction matters when reading §4 and §5:

`BILLING` §2.6 is the **only** email catalogue in the doc set. It has nine rows: eight assigned to
Phases 0, 2 and 3, and one (`weekly_ai_report`) marked "later". **Zero rows say Phase 4.** Worse,
the GA definition of done's own email list — `SAAS_PRD:191`, "welcome → invite → receipt → dunning
→ cancellation" — names five templates that all ship in **Phases 2 and 3** (SPEC-004 Step 15 ships
the last four). Read literally, the GA email gate is satisfied before Phase 4 begins (F3).

It is not, because five things the lifecycle actually needs are named nowhere, or named in three
words: the **escalating dunning ladder** (Phase 3 sends one `payment_failed`; SPEC-004 B2 assigns
the ladder here), **onboarding drips** (named only in SPEC-004:709 — a *spec*, not a PRD),
**unsubscribe** (`SAAS_PRD:167`, three words, no design), **bounce/complaint suppression** (nothing
anywhere), and **delivery tracking** (`SAAS_PRD:168` requires it; no schema exists). The retry
**outbox** is named once in `BILLING` §2.4 — "retry with backoff via a small outbox/queue" — and
specified nowhere: no table, no worker, no ladder, no phase.

So §4 and §5 below are **original design**, not transcription. They are flagged as such, and the
two genuinely product-shaped questions inside them are raised as O1 and O2 rather than defaulted.

---

## 1. Decisions

### 1.1 Locked — inherited or doc-derivable

| # | Decision | Source |
|---|---|---|
| D1 | **The `EmailProvider` Protocol stays transport-only.** New mail = one `send_*` method on `EmailService` + an `.html`/`.txt` template pair | SPEC-001 §5.1, restated twice in SPEC-004 (`:205`, Step 15). A provider that renders its own templates breaks failover (`BILLING` §2.1) |
| D2 | **Server-side Jinja2 templates, checked into the repo** — never provider-hosted | `BILLING` §2.5. A Resend-hosted template does not survive failover to Postmark/SES |
| D3 | **Email never blocks or fails its caller.** Catch `EmailSendError`, log with template key + recipient, return | `BILLING` §2.4; SPEC-001 §5.3. "A lost receipt email must never roll back a billing state change" |
| D4 | **A dedicated sending subdomain**, `send.mihomes.ai`, kept separate from any future `mail.mihomes.ai` bulk domain | `BILLING` §3 — bulk complaints must never poison transactional reputation |
| D5 | **DMARC keeps relaxed alignment.** Do **not** set `adkim=s; aspf=s` | `BILLING:224` — Resend's return-path sits on its own sub-label; strict SPF alignment fails legitimately-signed mail. See B1 |
| D6 | **Deletion offers export first, then retains, then hard-deletes** | `PRICING` §4.4. The retention window itself is `PLACEHOLDER` — see O2 |
| D7 | **Three separate observability surfaces**: the per-tenant audit log, the billing/webhook event log, and the email delivery log | `SAAS_PRD:168` names all three. They have different retention, different readers, and different tenancy |
| D8 | **Account deletion and data export are owner-only** | `ONBOARDING` §9.2 via SPEC-003's matrix — the same reasoning SPEC-004 D8 used for billing: admins manage the estate, not its existence |
| D9 | **Scheduled work stays a plain idempotent CLI command**, triggered externally | SPEC-004 **D15**. This phase adds workloads to that entrypoint; it does not invent a second mechanism |
| D10 | **Estate's three keys are enforced exactly as `PRICING` §3.1 writes them** — `false` on Free and Pro, `true` on Estate | `PRICING:88-90`. The same reasoning that closed P3-a in SPEC-004 D12: there are no hosted users to grandfather, so it is a pricing question the PRD already answered |

### 1.2 Locked — founder decisions, 2026-08-04

| # | Decision | Rationale |
|---|---|---|
| **D11** | **`EmailProvider.send()` gains exactly one additive keyword: `headers: dict[str, str] \| None = None`.** This is the only widening of the Protocol this phase makes | `List-Unsubscribe` and `List-Unsubscribe-Post` are **per-message** headers (RFC 8058) with nowhere else to live: `send()` accepts `to`, `subject`, `html`, `text`, `reply_to` and nothing more. Three reasons this does not violate D1: the constraint in SPEC-001 §5.1 and `BILLING` §2.1 is about **rendering** ("transports an already-rendered message"), not signature width, and a header dict carries no templating; the argument is **additive with a default**, so every existing call site and both implementations keep working; and "it breaks all four providers" is false — only `ConsoleProvider` and `ResendProvider` exist, `PostmarkProvider` and `SESProvider` being named in `BILLING` §2.3's factory and **specified nowhere** (F2). Recorded at length because a later reader will otherwise read the widening as a violation of SPEC-004 Step 15 |
| **D12** | **The outbox is a real table with a worker, not an in-process retry loop** | `BILLING` §2.4 names an "outbox/queue" and specifies nothing. An in-process retry dies with the request and cannot survive a deploy — useless for exactly the mail §2.4 calls billing-critical. A row plus an idempotent drain command reuses D9's entrypoint and is testable with no scheduler present (§4.1, Step 6) |
| **D13** | **Suppression is checked at `EmailService._send`, and it is absolute for lifecycle mail and inapplicable to transactional mail** | One choke point, so no `send_*` method can forget it — the same single-function discipline as SPEC-003 N3's redaction. The transactional/lifecycle split is the legally meaningful one: a receipt for money taken is not marketing and must send regardless of unsubscribe state; a drip is, and must not. §5.2 makes the class an explicit argument rather than a per-template guess |
| **D14** | **Export is assembled from the ORM under the scoped session — never from `csv_io.export_csv`, never from `backup.create_backup`** | Both are cross-tenant by construction today (F4, F5) and neither is fixable in place: `export_csv` covers **5 of 28** model modules, so a "download my data" built on it silently omits ~82% of the estate; `create_backup` tars the entire SQLite file plus the whole media directory. Under RLS the second is a **total** data breach wearing the name of a feature. N4 forbids both |
| **D15** | **Deletion is a two-phase state machine — `requested` → (grace) → `purged` — and the purge enumerates tables from `Base.metadata`, not from a hand-written list** | A hand-maintained list is exactly the artifact that rots: correct when written, silently wrong the first time someone adds a model. Same adversarial reasoning as SPEC-004 A11. A28 asserts every `TenantOwned` table is reached |
| **D16** | **`weekly_ai_report` is enforced as a *send*, not as a gate; the other two Estate keys are gates** | The three keys are not equal work and §6 does not pretend they are. `predictive_maintenance` and `audit_export` are `can()` call sites on functions that exist. `weekly_ai_report` names **no scheduled anything** — `generate_estate_digest` exists (`services/ai/reports.py:206`) but is reachable only from `web/routes/ai.py:311`, on request, when a human clicks (F6). Enforcing it requires building the weekly job first, which is why it is Step 13 and not part of Step 12 |
| **D17** | **Deliverability is verified by a test over the repo's own DNS documentation, not by a live DNS query** | SPEC-001 A18 set this precedent for the DMARC value. A test that resolves real DNS is a network-dependent flake and cannot run in CI before the domain exists; a test asserting the documented record is internally consistent catches B1's copy-paste defect, which is the failure that actually occurred |

### 1.3 `OPEN — needs decision: founder`

| # | Question | Why it cannot be defaulted | What it blocks |
|---|---|---|---|
| **O1** | **The drip sequence itself** — how many onboarding emails, at what intervals, saying what; and the same for re-engagement | No PRD names a single drip. `SAAS_PRD:182` says "full email lifecycle" and `SPEC-004:709` says "onboarding drips, re-engagement" — that is the entire specification. Cadence and content are product judgement about a stranger's first week, and a wrong guess is not a bug, it is a bad first impression sent to every new customer | **Template content and the schedule rows only.** The mechanism — enrolment, scheduling, suppression, unsubscribe — is fully specified below and testable with fixture templates. Step 11 ships the machinery; the copy lands in config |
| **O2** | **The deletion grace period**, and whether a deletion can be cancelled during it | `PRICING` §4.4 says 30 days, but the figure carries the doc's blanket `PLACEHOLDER` tag (`PRICING:7`). It is a legal/UX tradeoff: too short and an accidental deletion is unrecoverable; too long and "delete my data" is not honoured in a defensible window | **One config value and the cancel route.** The state machine (D15) is identical either way — `deletion_requested_at` plus a config key covers both — so the build proceeds |

Everything else this phase depends on is settled.

### 1.4 How SPEC-004's forward-flagged items resolve

SPEC-004 flagged nothing forward with a `P4-x` label — unlike SPEC-003, which used `P3-a/b/c`. Its
hand-forward lives in its §7 deferred table and its §10. Resolved here:

| SPEC-004 item | Its statement | Resolution here |
|---|---|---|
| Full dunning sequence | "`SAAS_PRD:185`. Phase 3 sends one `payment_failed` email; the retry ladder is Phase 4 (B2)" | **Built — Step 10.** The ladder is scheduler-driven, which is why Step 5 precedes it |
| Email lifecycle polish | "`SAAS_PRD:182/187`. The four transactional templates ship here" | **Built — Step 11**, mechanism only; content is O1 |
| `FailoverEmailProvider` | SPEC-001 §7: "Wrapping the same `EmailProvider` Protocol is enough — no caller changes" | **Deferred again, deliberately (§7).** Failover to an unverified standby is not failover (D4's caveat), and standby DKIM verification is a launch task with DNS lead time, not a code task. §10 records that GA ships single-provider |
| Metered billing (`report_usage`) | "§4.2's event log is already the data source it would read" | **Still deferred (5+).** Nothing here touches it; `AIUsageEvent.tokens_in/out` keep accumulating |
| Card-first trials, Stripe Tax, refunds UI, annual↔monthly, per-seat pricing | SPEC-004 §7, all `4+` | **Still deferred.** None is a GA gate; each is a revenue feature that wants a customer conversation first |
| Audit-log retention/export (SPEC-003 §7, `4+`) | "`ONBOARDING` §11 Q6; `export.data` exists as an action key" | **Partially built — Step 14** ships `audit_export` as an Estate capability. *Retention* stays deferred: `archive.py` already has a retention mechanism (`_retention_cutoff:28`) and pointing it at the audit log is a policy decision nobody has made |

### 1.5 Survey findings that shaped this spec

Eight findings, all verified against the tree or the doc set on 2026-08-04. Negative results are
stated as negatives, per `README.md:154`.

| # | Finding | Consequence |
|---|---|---|
| **F1** | **No email code exists at all.** `grep -rniE "smtp\|sendgrid\|resend\|postmark\|mailgun\|emailprovider\|send_mail\|mimetext\|smtplib\|boto3" src/ --include=*.py` → **zero matches**. The only repo-wide hit is minified text inside `web/static/htmx.min.js` | Everything in §5.2 attaches to a class SPEC-001 creates. Nothing in this phase can be verified against a running mail path until Phase 0 lands |
| **F2** | **`PostmarkProvider` and `SESProvider` are named in `BILLING` §2.3's factory and specified nowhere.** Neither has a conformance sketch, a config key, or a phase | D11's "breaks all four implementations" concern is unfounded — two of the four are vapor. Also why `FailoverEmailProvider` stays deferred: there is no specified standby to fail over *to* |
| **F3** | **The GA email gate is already satisfied on paper.** `SAAS_PRD:191`'s five named templates are assigned by `BILLING` §2.6 to Phases 2 and 3 — the last four by SPEC-004 Step 15 | §0.4. The gate must be read as *lifecycle infrastructure*, not as those five templates, or Phase 4's email work reads as already done |
| **F4** | **`csv_io.export_csv` covers 5 of 28 model modules** — `property`, `staff`, `vendor`, `task`, `issue` — and calls `session.query(model_class).all()` with **no account filter** | D14. A GDPR export built on it omits ~82% of the estate — assets, documents, contracts, budgets, notes, appointments, books, inventory and more — while appearing to succeed |
| **F5** | **`backup.create_backup:13` tars the entire SQLite file *and* the whole media directory** (`tar.add(DB_PATH, …)`, `tar.add(MEDIA_DIR, …)`), with no account parameter anywhere in its signature | D14, N4. Under multitenancy this is a total cross-tenant breach. It is an **operator** tool and must stay operator-only, never routed to a customer-facing "export" button |
| **F6** | **The three Estate features exist but sit very differently.** `run_predictive_maintenance` (`services/predictive_maintenance.py:137`) has **zero callers** — the same dead-code shape as SPEC-004's F6 ratings finding. `generate_estate_digest` (`services/ai/reports.py:206`) has exactly one caller, `web/routes/ai.py:311`, on human request. `record_change` (`services/audit.py:32`) has **many** callers across the service layer and is written by nearly every mutation | D16 and the Step 12–14 split. Gating `record_change` would break every write in the app — the `audit_export` gate belongs on the **read/export** path, the same read-gate shape SPEC-004 D14 established for ratings |
| **F7** | **No observability of any kind.** `grep -rniE "sentry\|structlog\|opentelemetry\|prometheus\|datadog\|newrelic\|statsd\|loguru" src/ tests/ pyproject.toml` → **zero matches**. Logging is 8 ad-hoc `getLogger` call sites; the only `logging.basicConfig` in the tree is `ha/bridge.py:121`, inside the Home Assistant bridge, not the web app. **No FastAPI `exception_handler` is registered anywhere**, and there are **136 `except Exception` blocks** in `src/`, many bare-swallowing | Step 15. "Hardening" is undefined in the PRD (`SAAS_PRD:182`); this is what it has to mean, because a GA service whose errors are swallowed 136 times over has no way to learn it is broken |
| **F8** | **`audit_log` is in SPEC-002's `account_id` sweep, but as a polymorphic special case.** SPEC-002 F5 and Step 4 name it among 5 models carrying `entity_type` + `entity_id` with **no `ForeignKey`**, for which "a composite FK is **impossible** — use a trigger, or accept application-only enforcement and say so". Its PK also becomes UUIDv7 under SPEC-002 D2's int→UUID remap (`:548`) | This phase does **not** own an `audit_log` migration — that is SPEC-002's. But `audit_export` inherits the weaker guarantee: rows are scoped by application logic, not by a foreign key. §10 records it |

### 1.6 Inbound launch gates — three open decisions from earlier specs

These are **not** renumbered here (`README.md:21-24`). Each is resolved in the spec that raised it,
and each blocks the GA definition of done rather than this build:

| Origin | Question | Which GA bullet it blocks |
|---|---|---|
| **SPEC-001 O1** | ToS + Privacy Policy published — *"No doc owns drafting it. The footer links exist in the template and will 404 until this lands"* | `:194` directly. It also legally blocks Phase 0's first email capture, making it the oldest unresolved item in the set |
| **SPEC-004 O1** | The actual launch prices and limits (~20 `PLACEHOLDER` values) | `:195` — public signup at real prices |
| **SPEC-003 O1** | At-rest encryption of provider API keys | None directly, but §10 records that GA ships plaintext secrets in `configurations.value`, and GA is when strangers' keys start arriving |

**All three are founder decisions and none is closed by this spec.** §8's A31 asserts they are
*explicitly tracked*, not that they are resolved — a spec cannot test a legal document into
existence.

---

## 2. Doc-fix prerequisites

Per the scope decision recorded in §0, this section carries **only what Phase 4 inherits**. The
much larger backlog decided by earlier specs and never applied is catalogued separately in §2.2,
because a reader needs to know the PRDs are stale without this spec pretending to own SPEC-002's
edits.

### 2.1 Fixes this phase must land

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `GTM_LAUNCH_PLAN.md:273` — the DMARC row reads `v=DMARC1; p=none; rua=mailto:dmarc@mihomes.ai; adkim=s; aspf=s` | **Delete `adkim=s; aspf=s`.** `BILLING:224` explicitly forbids strict alignment, and `GTM:262` itself says BILLING "is authoritative for the email records" — then contradicts it inside a copy-pasteable table. `PRD_REVIEW` A6 rates this highest-priority and it is **still unapplied**, verified today. GA requires "DKIM/SPF/DMARC passing" (`:191`), so this line will actively break the gate it sits beside (D5, A20) |
| **B2** | `SAAS_PRD:192` lists "Downgrade/past-due grace policy implemented per `PRICING` §4.3" as a **Phase 4 exit** criterion | **Mark it satisfied upstream.** SPEC-004 Step 14 builds it and A20 proves it. Leaving it in the Phase 4 list invites rebuilding a shipped feature. Same for `:190`, which is pure re-verification of Phase 1–3 criteria — reword both as *regression checks*, not deliverables |
| **B3** | `BILLING` §2.6's catalogue assigns **no** template to Phase 4, while `SAAS_PRD:182` makes "full email lifecycle" Phase 4's headline | **Add the Phase 4 rows** this spec defines: `dunning_2`, `dunning_3`, `dunning_final`, the drip sequence, `deletion_requested`, `deletion_complete`, `export_ready` — and move `weekly_ai_report` from "later" to 4. Without this the catalogue reads as complete and Phase 4 reads as empty (F3) |
| **B4** | `SAAS_PRD:167` — "email opt-out" is three words in an NFR table, with no unsubscribe design anywhere | **Write the requirement properly:** one-click unsubscribe (RFC 8058 `List-Unsubscribe-Post`), a suppression list, and the transactional/lifecycle distinction that makes opt-out legally coherent (D13) |
| **B5** | `SAAS_PRD:168` Observability requires "email delivery tracking" with no schema, and `BILLING` §2.4's "outbox/queue" is named once and never specified | **Point both at §4.1–§4.2 here.** Two tables, one worker, one drain command (D12) |
| **B6** | `PRICING` §4.4's 30-day deletion retention carries the doc's blanket `PLACEHOLDER` tag but is written as though settled | **Tag it explicitly** and record it as this spec's **O2**, so the number is not copied into code as fact |

### 2.2 Still unlanded across the set — not this spec's to fix

Verified 2026-08-04: **every** doc-fix decided by SPEC-001 through SPEC-004 remains unapplied to
`docs/product/` and `docs/architecture/`. SPEC-004:137 says so about its own: *"(`PRD_REVIEW` A3,
still unfixed as of today)"*. A sample, with the stale text still present today:

| Label | Still-wrong text | Decided in |
|---|---|---|
| `PRD_REVIEW` A1 | `PRICING:98` still says membership status `active` **or** `invited` | SPEC-002 `:103`, SPEC-003 D6 |
| `PRD_REVIEW` A2 | `ONBOARDING:35,43,220` still carry `accounts.owner_user_id` | SPEC-002 D4 |
| `PRD_REVIEW` A3 | `PRICING:143` still says `billing_status`; `ONBOARDING:34,66,109` still `last_login` | SPEC-002 `:105`, SPEC-004 B1 |
| `PRD_REVIEW` A4 | `ONBOARDING:44,51,52,285` still reference `membership_home_scopes(…, home_id)` — scoping to a `homes` table that does not exist | SPEC-002 D5, N8 |
| `PRD_REVIEW` C1 | `MULTITENANCY.md:32` still says "24 route modules"; the tree has **23** | SPEC-002 `:109` |

**Why this is named rather than deferred silently:** the specs are the record of what was decided,
so the system is buildable — but anyone reading the PRDs directly is reading known-wrong values,
and `PRD_REVIEW` §G is the catalogue of what that costs. The recommendation is a single mechanical
pass over `docs/product/` and `docs/architecture/` applying all of them, tracked outside this spec.

### 2.3 Contradictions with no owning phase

Named so they stop being invisible. None is built here.

- **`PRD_REVIEW` B3 — the shared responder-core refactor has no owning phase.** Verified by grep:
  `responder.core|responder-core|shared responder|gateways/core|core/responder` across
  `docs/specs/` returns **zero hits** in all four specs. It modifies two live gateways and belongs
  with the 4+ gateway work, but no document claims it.
- **`PRD_REVIEW` §G (G1–G8)** — `TELEGRAM_PRD` and `WHATSAPP_GATEWAY_PRD` contain nine
  verified-false claims about the existing code. §G says plainly: *"do not spec from these yet."*
  They remain unquarantined — no header, no banner — so the next reader will not know.
- **Contradiction C2 — three PRDs declare their own "Phase 4 (GA)".** `TELEGRAM_PRD:188`,
  `WHATSAPP_GATEWAY_PRD:647` and `OMNICHANNEL_GATEWAY_PRD:589` each assign gateway work to a
  "Phase 4" that `SAAS_PRD` §10 puts at **4+**, and `OMNICHANNEL:64` calls six gateway items
  "P0 = launch-blocking". `SAAS_PRD` §10 is canon (`:174`) and wins. **If those docs are read at
  face value, this spec doubles in size** — which is precisely why the collision is named here.
- **`PRD_REVIEW` B4's A2P 10DLC registration** — the one item with a regulatory lead time, absent
  from every spec. Not needed for GA (Twilio is 4+), but lead time is the whole point of naming it.

---

## 3. File manifest

### New — email lifecycle

```
src/mihomes/services/email/outbox.py             enqueue / drain / backoff ladder (D12)
src/mihomes/services/email/suppression.py        is_suppressed / suppress / unsubscribe tokens (D13)
src/mihomes/services/email/campaigns.py          drip enrolment + due-send selection (O1 supplies content)
src/mihomes/models/email_delivery.py             EmailOutbox + EmailSuppression + EmailDelivery
src/mihomes/models/email_campaign.py             CampaignEnrolment (TenantOwned)
```

### New — GDPR

```
src/mihomes/services/privacy/__init__.py
src/mihomes/services/privacy/export.py           build_export — ORM-assembled, scoped (D14)
src/mihomes/services/privacy/deletion.py         request / cancel / purge state machine (D15)
src/mihomes/models/account_deletion.py           AccountDeletionRequest
src/mihomes/web/routes/privacy.py                owner-only: export, delete, cancel (D8)
src/mihomes/web/templates/privacy.html
```

### New — observability

```
src/mihomes/logging_config.py                    ONE dictConfig, JSON in prod (F7)
src/mihomes/web/errors.py                        FastAPI exception handlers + error templates
src/mihomes/web/templates/error.html
```

### New — email templates (the package is SPEC-001 §3; the four billing pairs are SPEC-004)

```
src/mihomes/services/email/templates/dunning_2.html + .txt
src/mihomes/services/email/templates/dunning_3.html + .txt
src/mihomes/services/email/templates/dunning_final.html + .txt
src/mihomes/services/email/templates/weekly_digest.html + .txt
src/mihomes/services/email/templates/deletion_requested.html + .txt
src/mihomes/services/email/templates/deletion_complete.html + .txt
src/mihomes/services/email/templates/export_ready.html + .txt
src/mihomes/services/email/templates/drip_*.html + .txt          count and copy = O1
src/mihomes/services/email/templates/partials/unsubscribe_footer.html
```

### New — migration

```
alembic/versions/xxxx_phase4_lifecycle.py        5 tables + RLS. NO accounts changes, NO audit_log changes
```

### Modified

| File | Change |
|---|---|
| `services/email/provider.py` | **One additive kwarg** `headers` on `send()` (D11) — and nothing else |
| `services/email/service.py` | `_send` grows a `klass` argument and the suppression check (D13); the new `send_*` methods |
| `services/email/resend_provider.py` | Pass `headers` through to the Resend payload |
| `services/email/console_provider.py` | Print `headers`, so A18 can assert on them |
| `cli/jobs.py` | Four new subcommands on SPEC-004's entrypoint: `drain-outbox`, `dunning`, `drips`, `weekly-digest` (D9) |
| `services/billing/service.py` | `payment_failed` starts the ladder instead of sending one email (Step 10) |
| `services/predictive_maintenance.py` | `run_predictive_maintenance` takes `account` and asserts `can()` (D10) |
| `services/audit.py` | New `export_audit_log` — gated. **`record_change` is NOT gated** (F6) |
| `services/ai/reports.py` | `generate_estate_digest` unchanged; the new weekly job calls it (D16) |
| `web/routes/ai.py` | `:311` unaffected — Estate gates the *scheduled* send, not the on-request one |
| `services/csv_io.py` | Docstring only: names itself operator/per-entity, points at `privacy/export.py` (N4) |
| `services/backup.py` | Docstring only: **operator tool, never customer-facing** (F5, N4) |
| `web/app.py` | Register `logging_config` and the exception handlers |
| `pyproject.toml` | `structlog`; `sentry-sdk` config-gated (§10) |

**No migration touches `accounts` or `audit_log`.** `audit_log`'s `account_id` and UUIDv7 PK are
SPEC-002's Step 4 / §5.4 work (F8). If this phase finds itself writing an `ALTER` on either,
SPEC-002 was implemented differently than specified — stop and reconcile (§0.1) rather than
patching around it, exactly as SPEC-004 N13 requires.

---

## 4. Schemas as code

### 4.1 The outbox, the suppression list, and the delivery log

```python
# src/mihomes/models/email_delivery.py
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.db import Base
from mihomes.ids import new_id
from mihomes.models.mixins import TenantOwned


class EmailOutbox(Base, TenantOwned):
    """Queued mail awaiting delivery. THE thing BILLING §2.4 names and never specifies (D12).

    Why a table and not an in-process retry: a retry loop dies with the request and
    cannot survive a deploy, which makes it useless for precisely the billing-critical
    mail §2.4 is talking about. A row survives both.

    TenantOwned because a queued message belongs to the account it is about — an
    operator listing one account's pending mail must not see another's.
    """

    __tablename__ = "email_outbox"
    __table_args__ = (
        # The drain worker's only query: due, unsent, oldest first.
        Index("ix_email_outbox_due", "next_attempt_at", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(50), nullable=False)

    # Rendered at SEND time, not enqueue time, so a template fix repairs queued mail.
    # This column holds the render CONTEXT, not the rendered html.
    context: Mapped[str] = mapped_column(Text, nullable=False)          # JSON

    # "transactional" | "lifecycle" — decides whether suppression applies (D13).
    klass: Mapped[str] = mapped_column(String(20), nullable=False)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when attempts are exhausted. A dead row is KEPT, never deleted: "why did the
    # customer not get their receipt" is a question someone will ask in support.
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class EmailSuppression(Base):
    """Addresses that must never receive lifecycle mail (D13).

    Deliberately NOT TenantOwned — the same carve-out shape as `sessions` (SPEC-002 §7)
    and `processed_webhook_events` (SPEC-004 §4.1), for a different reason: suppression
    is a property of an ADDRESS, not of an account. Someone who unsubscribes, hard-
    bounces, or files a spam complaint must stay suppressed even if they later appear
    under a second account. Scoping this per-tenant would re-mail a complainer the first
    time they are invited elsewhere, which is how a sending domain gets blocklisted.
    """

    __tablename__ = "email_suppressions"
    __table_args__ = (
        UniqueConstraint("address", name="uq_email_suppression_address"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    address: Mapped[str] = mapped_column(String(320), nullable=False)

    # "unsubscribe" | "hard_bounce" | "complaint" | "manual"
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    suppressed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # The provider event that caused it, when there was one. NULL for a user-clicked
    # unsubscribe.
    provider_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)


class EmailDelivery(Base, TenantOwned):
    """Per-message delivery outcome — SAAS_PRD:168's "email delivery tracking" (B5).

    Separate from EmailOutbox on purpose: the outbox is a work queue that drains, this
    is a permanent record. Merging them means either the queue never empties or the
    history is deleted.
    """

    __tablename__ = "email_deliveries"
    __table_args__ = (
        Index("ix_email_delivery_account_sent", "account_id", "sent_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    template: Mapped[str] = mapped_column(String(50), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Updated by the provider webhook when one arrives: delivered | bounced |
    # complained | opened. NULL means "accepted by the provider, no further signal" —
    # the normal terminal state, not an error.
    status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### 4.2 Drip enrolment — `models/email_campaign.py`

```python
class CampaignEnrolment(Base, TenantOwned):
    """One row per (account, campaign). Tracks how far through a drip an account is.

    The step INDEX is stored, not the next template name, so O1 can change a sequence's
    content without a migration. A sequence that shortens leaves rows whose step exceeds
    the new length — that is completed(), not an error (§5.3).
    """

    __tablename__ = "campaign_enrolments"
    __table_args__ = (
        UniqueConstraint("account_id", "campaign", name="uq_enrolment_account_campaign"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign: Mapped[str] = mapped_column(String(50), nullable=False)   # "onboarding" | "reengagement"

    enrolled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    step: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Set when the sequence finishes OR the account unenrols. Non-NULL means the
    # scheduler skips this row forever — the drip's own idempotency guarantee.
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### 4.3 Deletion — `models/account_deletion.py`

```python
class AccountDeletionRequest(Base, TenantOwned):
    """The two-phase deletion state machine (D15).

    requested -> (grace, O2) -> purged. This row OUTLIVES the account's data: after the
    purge every TenantOwned row is gone, but this record remains as proof the request
    was honoured, and when. That is the artifact a regulator asks for, so it must not be
    caught by its own purge (§5.4 excludes it explicitly).
    """

    __tablename__ = "account_deletion_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requested_by_user_id: Mapped[str] = mapped_column(String(36), nullable=False)

    purge_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Per-table row counts at purge time, as JSON. Not decoration: it is how A28 proves
    # the purge reached every table, and how support answers "what was deleted".
    purge_manifest: Mapped[str | None] = mapped_column(Text, nullable=True)
```

### 4.4 Migration — five tables, one deliberate carve-out

```python
# alembic/versions/xxxx_phase4_lifecycle.py
def upgrade() -> None:
    # ... create email_outbox, email_deliveries, campaign_enrolments,
    #     account_deletion_requests (all with the account_id FK), and
    #     email_suppressions (NO account_id) ...

    # RLS on the four tenant tables; the suppression list gets none.
    for table in ("email_outbox", "email_deliveries", "campaign_enrolments",
                  "account_deletion_requests"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (account_id = current_setting('app.current_account', true))
        """)

    # email_suppressions is deliberately NOT RLS-enabled (§4.1). A tenant-scoped
    # suppression list re-mails a complainer under a second account. A21 is the test
    # that catches a later migration adding a policy here.
```

---

## 5. Function signatures

### 5.1 The one Protocol change — `services/email/provider.py`

**Reuse SPEC-001 §5.1's declarations verbatim** — `EmailProviderError`, `EmailAuthError`,
`EmailSendError`, `EmailResult`, `get_email_provider`. Exactly one line changes:

```python
class EmailProvider(Protocol):
    def send(
        self,
        to: str | list[str],
        subject: str,
        html: str,
        *,
        text: str | None = None,
        reply_to: str | None = None,
        headers: dict[str, str] | None = None,   # <-- D11: the ONLY addition
    ) -> EmailResult:
        """Send a pre-rendered message. Returns the provider message id.

        `headers` carries per-message SMTP headers the caller has already decided on —
        today only List-Unsubscribe / List-Unsubscribe-Post (RFC 8058). It does NOT make
        the provider a renderer: the value is a finished dict, exactly as `html` is
        finished markup. The Protocol stays transport-only (D1).
        """
```

### 5.2 `EmailService` — suppression at the choke point

```python
class EmailService:
    def _send(self, to: str, template: str, data: dict, *, klass: str,
              account: Account | None = None) -> None:
        """Render and dispatch. Catches EmailSendError, logs, never raises (D3).

        `klass` is REQUIRED and keyword-only, never defaulted. D13: suppression applies
        to "lifecycle" and never to "transactional". A default would silently pick one
        for every future call site, and picking wrong in either direction is bad — a
        suppressed receipt is a billing dispute, an unsuppressed drip is a CAN-SPAM
        violation.

        Order: suppression check -> render -> unsubscribe headers (lifecycle only) ->
        enqueue to the outbox. Business code never calls the provider directly.
        """

    # Lifecycle mail — every one of these is klass="lifecycle"
    def send_dunning(self, to: str, *, step: int, account: Account,
                     invoice_url: str, retry_at: datetime | None) -> None: ...
    def send_weekly_digest(self, to: str, *, account: Account, digest_html: str) -> None: ...
    def send_drip(self, to: str, *, account: Account, campaign: str, step: int) -> None: ...

    # Transactional — klass="transactional", NOT suppressible
    def send_export_ready(self, to: str, *, download_url: str, expires_at: datetime) -> None: ...
    def send_deletion_requested(self, to: str, *, purge_after: datetime,
                                cancel_url: str) -> None: ...
    def send_deletion_complete(self, to: str) -> None: ...
```

### 5.3 Outbox, suppression, campaigns

```python
# outbox.py
def enqueue(session: Session, *, to: str, template: str, context: dict,
            klass: str, account_id: str) -> None:
    """Insert a due-now row. Called by EmailService._send, never by business code."""

def drain(session: Session, *, limit: int = 100, now: datetime) -> DrainResult:
    """Send every due row, oldest first. Idempotent and safe to run twice (D9).

    Backoff on failure: 1m, 5m, 30m, 2h, 12h — five attempts, then failed_at is set and
    the row stops being selected. `now` is injected rather than read from the clock, so
    the ladder is testable without sleeping (A16).
    """

# suppression.py
def is_suppressed(session: Session, address: str) -> bool: ...

def suppress(session: Session, address: str, *, reason: str,
             provider_event_id: str | None = None) -> None:
    """Idempotent: a second suppression of the same address is a no-op, not an error.
    Bounces and complaints arrive more than once."""

def unsubscribe_token(address: str) -> str:
    """HMAC over the address using the app secret. Stateless, so no token table.

    NOT a raw address in a URL: a bare /unsubscribe?email=x lets anyone unsubscribe
    anyone. Same token discipline as SPEC-001 N7's confirmation tokens.
    """

# campaigns.py
def enrol(session: Session, account: Account, campaign: str) -> None:
    """Idempotent per (account, campaign) — the unique constraint is the guard."""

def due_sends(session: Session, *, now: datetime) -> list[CampaignEnrolment]:
    """Enrolments whose next step is due, per the O1 schedule in config.

    An enrolment whose step exceeds the configured sequence length is COMPLETED, not an
    error: O1 may shorten a sequence after rows already exist (§4.2).
    """
```

### 5.4 Privacy — export and deletion

```python
# privacy/export.py
def build_export(session: Session, account: Account) -> ExportBundle:
    """Every row this account owns, assembled from the ORM under the scoped session.

    D14/N4: NOT csv_io.export_csv (5 of 28 models, unscoped — F4) and NOT
    backup.create_backup (whole DB file + whole media dir — F5).

    Tables are enumerated from Base.metadata by TenantOwned membership, not from a
    hand-written list, for the same reason as the purge (D15): a list rots silently the
    first time a model is added. A27 asserts the enumeration covers every TenantOwned
    table.

    Documents are included by reference (presigned StorageProvider URLs, SPEC-002 §5),
    never inlined — an estate's media does not belong in a JSON blob.
    """

# privacy/deletion.py
def request_deletion(session: Session, account: Account, user: User) -> AccountDeletionRequest:
    """Owner-only (D8). Sets purge_after = now + grace (O2), sends deletion_requested,
    and offers the export first (PRICING §4.4). Deletes nothing yet."""

def cancel_deletion(session: Session, account: Account) -> None:
    """Only while purged_at is NULL. Idempotent."""

def purge(session: Session, request: AccountDeletionRequest) -> dict[str, int]:
    """Hard-delete every TenantOwned row for the account, returning per-table counts.

    Enumerated from Base.metadata (D15). Two deliberate exclusions, both stated so a
    reader does not read them as bugs:
      - account_deletion_requests itself — the proof the request was honoured (§4.3)
      - email_suppressions — not TenantOwned, and a suppressed address must STAY
        suppressed after the account that surfaced it is gone (§4.1)

    Storage objects are deleted through StorageProvider.delete BEFORE the DB rows, so a
    mid-failure leaves orphaned rows pointing at deleted files rather than orphaned files
    nothing points at. Orphaned rows are findable; orphaned S3 objects are not.
    """
```

### 5.5 The three Estate gates

```python
# services/predictive_maintenance.py — a WRITE gate on a function with zero callers (F6)
def run_predictive_maintenance(session: Session, account: Account) -> MaintenanceScanResult:
    """Now takes `account`: can(account, "predictive_maintenance.run") must be Allowed.

    Zero callers today, so this gate has no live surface — placed exactly as SPEC-004
    D14 placed the dead vendor_rating gates, so whoever wires it up inherits the gate
    instead of reopening the hole.
    """

# services/audit.py — a READ gate. record_change is NOT gated (F6)
def export_audit_log(session: Session, account: Account, **filters) -> str:
    """can(account, "audit.export") must be Allowed. Estate-only (D10).

    The gate is on EXPORT, never on record_change: that function has many callers across
    the service layer and is written by nearly every mutation, so gating it would make a
    Free account's writes fail. Same read-gate shape as SPEC-004 D14's ratings.
    """

# The weekly digest — a SEND gate, not a call gate (D16)
#   cli/jobs.py weekly-digest: for each account, can(account, "weekly_ai_report.send")
#   before enqueuing. generate_estate_digest() itself is UNCHANGED and the on-request
#   route at web/routes/ai.py:311 is UNAFFECTED — a Pro user clicking "generate digest"
#   still works. Estate buys the *scheduled* send, which is what PRICING:89 names.
```

---

## 6. Sequenced steps

Each step ends in a green test or an observable behaviour. Four ordering constraints are
load-bearing and are called out where they bind: **Step 1 before everything email** (the Protocol
change is one line and everything depends on it), **Step 5 before Steps 10, 11 and 13** (the
scheduler exists before three workloads need it), **Step 7 before Step 8** (export exists before
deletion offers it), and **Step 12 before Step 13** (the weekly job exists before it can be gated).

**Step 1 — the Protocol widening.** `headers` on `send()`; both providers pass it through;
`ConsoleProvider` prints it. **First, because it is one line and six later steps depend on it.**
*Verify:* every existing call site still works untouched (the default is `None`), and
`ConsoleProvider` emits a header dict when given one (A18).

**Step 2 — suppression.** `models/email_delivery.py`'s `EmailSuppression`, `suppression.py`, and
the HMAC token. *Verify:* `suppress` twice on one address is a no-op (A22); a forged token is
rejected; the table has **no** RLS policy (A21).

**Step 3 — the delivery log.** `EmailDelivery` plus the write in the send path.
*Verify:* a sent message writes exactly one row carrying the provider message id (A19).

**Step 4 — the outbox.** `EmailOutbox`, `enqueue`, `drain`, and the five-rung backoff ladder.
`EmailService._send` enqueues instead of calling the provider. *Verify:* a provider failure
reschedules rather than losing the message; the fifth failure sets `failed_at` and the row stops
being selected (A16); `drain` on an empty queue is a no-op.

**Step 5 — the scheduler, verified for real.** Add `drain-outbox`, `dunning`, `drips` and
`weekly-digest` to SPEC-004's `mihomes jobs` entrypoint, and **close SPEC-004 D15's open infra
assumption**: confirm against Fly's current documentation that the scheduled-machine mechanism
exists and behaves as D15 assumed, or adopt the named alternative. **Strictly before Steps 10, 11
and 13.** *Verify:* every subcommand is idempotent on a second consecutive run (A17), and the
enumeration test A15 passes with all six workloads wired.

**Step 6 — the migration.** §4.4's five tables, four RLS policies, one carve-out.
*Verify:* it applies and reverts (A30); `email_suppressions` has no policy (A21).

**Step 7 — data export.** `build_export`, enumerated from `Base.metadata`, under the scoped
session. Owner-only route. **Before Step 8**, because deletion must offer it (`PRICING` §4.4).
Steps 7 and 8 together are the GA gate at **`SAAS_PRD:193`** — "data export and account-deletion
paths exist (GDPR/CCPA baseline from §9)".
*Verify:* the bundle contains rows from **every** `TenantOwned` table (A27); a second account's
rows appear nowhere in it (A26); documents are presigned references, not inlined bytes.

**Step 8 — deletion.** The `requested → grace → purged` state machine, the cancel route, and the
purge enumerated from `Base.metadata`. *Verify:* after a purge, **zero** rows remain in every
`TenantOwned` table for that account (A28); `account_deletion_requests` and `email_suppressions`
survive (A29); a purge that fails midway leaves no orphaned storage objects.

**Step 9 — unsubscribe.** The one-click route (RFC 8058 `List-Unsubscribe-Post`), the footer
partial, and header injection for lifecycle mail only. *Verify:* a lifecycle send carries both
headers and a transactional send carries neither (A18); clicking unsubscribe suppresses in one
request with no confirmation page — one-click means one click.

**Step 10 — the dunning ladder.** `payment_failed` starts a sequence instead of sending one email;
subsequent rungs fire from Step 5's `dunning` job on the `BILLING` §5 schedule. *Verify:* a single
`invoice.payment_failed` produces one email immediately and the rest on schedule (A23); recovery
mid-ladder stops it (A24); the ladder never outlives the subscription that started it.

**Step 11 — the drip machinery.** `campaigns.py`, enrolment on account creation, `due_sends`, and
the `drips` job. **Mechanism only — O1 supplies the content**; build and test against fixture
templates. *Verify:* an enrolled account receives step 0 then step 1 on schedule and never twice
(A25); a suppressed address receives nothing (A22); shortening a sequence completes in-flight
enrolments rather than erroring.

**Step 12 — two Estate gates.** `predictive_maintenance.run` and `audit.export`. *Verify:* Free
and Pro are denied and Estate allowed on both (A12); **`record_change` still fires for every
account on every plan** (A13) — the check that would have caught gating the wrong function.

**Step 13 — the weekly digest job.** The scheduled send, gated per account (D16). **After Steps 5
and 12.** *Verify:* an Estate account receives it weekly and a Pro account does not (A14); the
on-request route at `web/routes/ai.py:311` still works for **every** plan (A14b) — Estate buys the
schedule, not the feature.

**Step 14 — `audit_export` end to end.** Wire the gated export to a route and the CLI.
*Verify:* an Estate owner exports; a Pro owner gets a `Denied` naming the upgrade target.

**Step 15 — observability and error handling.** One `logging_config.py` with a real `dictConfig`
(JSON in prod), FastAPI exception handlers, an `error.html`, and `/healthz` confirmed live from
SPEC-001. Audit the 136 `except Exception` blocks: every one either re-raises or logs with
context — **none stays silent** (F7). *Verify:* an unhandled exception renders the error page and
emits one structured log record with a request id (A31); no bare swallow remains in the request
path (A32).

**Step 16 — the deliverability check.** B1's `GTM:273` edit, plus D17's documentation test.
*Verify:* the repo's DMARC record is internally consistent and carries no `adkim=s`/`aspf=s`
(A20).

**Step 17 — the GA readiness surface.** A single command or page enumerating the six
`SAAS_PRD:189-196` gates with their status, including the three §1.6 inbound gates as
**explicitly unresolved** where they are. *Verify:* A33 — every one of the six is present and
none reports a false green.

**Exit criterion check.** With Steps 1–17 green and the §1.6 gates closed by their owners, the six
GA bullets are met. That is `SAAS_PRD:189-196`.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not widen the `EmailProvider` Protocol beyond `headers`.** D11 authorises exactly one
additive keyword. Attachments, tags, scheduling, templates, or a `send_batch` all belong in
`EmailService` or nowhere — the moment a provider does more than transport a rendered message,
failover breaks (`BILLING` §2.1, D1).

**N2 — Do not send directly from a request handler.** Everything goes through the outbox (D12).
A direct send inside a web request reintroduces exactly the coupling `BILLING` §2.4 forbids: a
slow provider becomes a slow page, and a failed provider becomes a failed checkout.

**N3 — Do not apply suppression to transactional mail.** A receipt for money taken, a deletion
confirmation, and an export link are not marketing and must send regardless of unsubscribe state
(D13). Suppressing them is not caution — it is withholding a record the customer is owed.

**N4 — Do not build data export on `csv_io.export_csv` or `backup.create_backup`.** The first
covers 5 of 28 models and is unscoped (F4); the second tars the whole database and the whole media
directory (F5). Under RLS the second is a **complete cross-tenant breach** that presents as a
working feature. `create_backup` stays an operator tool and never gets a customer-facing route.

**N5 — Do not enumerate tables by hand in the export or the purge.** Both walk `Base.metadata`
(D14, D15). A hand-written list is correct the day it is written and silently wrong the first time
a model is added — and for the purge, "silently wrong" means personal data left behind after a
deletion the customer was told was complete.

**N6 — Do not delete `email_suppressions` rows during an account purge.** Suppression is a
property of an address, not an account (§4.1). Purging it re-mails a complainer the moment they
reappear, which is how a sending domain gets blocklisted.

**N7 — Do not gate `record_change`.** It has many callers across the service layer and is written
by nearly every mutation (F6). Gating it makes writes fail for non-Estate accounts. The
`audit_export` gate belongs on the read/export path — the read-gate shape SPEC-004 D14 established.

**N8 — Do not gate `generate_estate_digest` or the `/ai` digest route.** D16: Estate buys the
**scheduled** send. Gating the function paywalls a button that works today for everyone and
delivers a worse version of SPEC-004 N9's mistake — a Free user seeing a feature disappear rather
than an upgrade prompt.

**N9 — Do not put the raw email address in an unsubscribe URL.** HMAC it (§5.3). A bare
`?email=` parameter lets anyone unsubscribe anyone, and the addresses are enumerable.

**N10 — Do not require a confirmation page for one-click unsubscribe.** RFC 8058 means the
`POST` completes the unsubscribe. A confirmation step makes mailbox providers treat the header as
broken, which costs deliverability — the opposite of the intent.

**N11 — Do not read the clock inside `drain`, `due_sends`, or the dunning ladder.** `now` is
injected (§5.3), so the schedules are testable without sleeping. A test that waits is a test that
gets deleted.

**N12 — Do not add an `accounts` or `audit_log` migration.** Both are earlier specs' work (F8,
SPEC-002 §4.2). Needing one here means an earlier phase diverged — stop and reconcile (§0.1),
exactly as SPEC-004 N13 requires.

**N13 — Do not treat `SAAS_PRD:190` and `:192` as build work.** Phase 1–3 exit criteria and the
downgrade grace policy are already built and tested upstream (SPEC-004 Step 14, A20). They are
**regression checks** at GA, not deliverables (B2). Rebuilding them is the most likely way to
waste this phase.

**N14 — Do not spec from `TELEGRAM_PRD` or `WHATSAPP_GATEWAY_PRD`.** `PRD_REVIEW` §G:
*"do not spec from these yet"* — nine of their factual claims about the existing code are false.
Their self-declared "Phase 4 (GA)" sections are not this phase (§2.3, contradiction C2).

**N15 — Do not silently swallow an exception in the request path.** Step 15 audits all 136 sites
(F7). A GA service that discards its own errors cannot learn it is broken, and the customer finds
out first.

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| `FailoverEmailProvider` (Resend → Postmark/SES) | 5+ | SPEC-001 §7 assigned it Phase 4; **deferred again deliberately** (§1.4). The Protocol already supports wrapping — but no standby is specified (F2) and DKIM pre-verification is a DNS-lead-time launch task, not code. §10 records that GA ships single-provider |
| `PostmarkProvider` / `SESProvider` | 5+ | Named in `BILLING` §2.3's factory, specified nowhere (F2). The factory's `else: raise` already names the supported set |
| Metered / usage-based billing (`report_usage`) | 5+ | SPEC-004 §7 unchanged. `AIUsageEvent.tokens_in/out` keep accumulating as its data source |
| Audit-log **retention** | 5+ | `archive.py:28`'s `_retention_cutoff` is the mechanism; the policy is undecided. Export ships here (Step 14) |
| Per-tenant watchdog / monitor redesign | 5+ | `SAAS_PRD:164`. Downstream of per-tenant gateways, which GA excludes (`:186`) — redesigning now solves a problem GA does not have. Step 5 hardens the *shared scheduler*, which is the part GA needs |
| Chat-gateway tenant-awareness; shared responder-core (`PRD_REVIEW` B3) | 4+ | SPEC-003 §7 unchanged. B3 still has **no owning phase** (§2.3) |
| Granular per-capability staff permissions; non-Google invitees; cross-account seats | 4+ | SPEC-003 §7 unchanged |
| Card-first trials, Stripe Tax, refunds UI, annual↔monthly, per-seat pricing | 4+ | SPEC-004 §7 unchanged |
| Open/click tracking | 5+ | `EmailDelivery.status` already accepts `opened`; nothing writes it. Deliberate: open tracking is a pixel, and a pixel is a privacy decision nobody has made |
| Localized pricing / currencies | post-GA | `PRICING` Q8 — "USD only at launch" |
| Referral bump ("move up 20 spots") | 5+ | `GTM:212`, SPEC-001 §7. `referred_by` is populated and still read by nothing |
| At-rest secret encryption | ? | **SPEC-003's O1, still open.** This phase does not widen it and does not close it (§1.6, §10) |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | Lifecycle mail to a suppressed address is not sent | `test_suppression.py::test_lifecycle_suppressed` |
| A2 | Transactional mail to a suppressed address **is** sent | `test_suppression.py::test_transactional_ignores_suppression` |
| A3 | `_send` cannot be called without an explicit `klass` | `test_email_service.py::test_klass_required` |
| A4 | A provider failure reschedules; the message is not lost | `test_outbox.py::test_retry_preserves_message` |
| A5 | An email failure never rolls back its caller's transaction | `test_outbox.py::test_send_failure_does_not_rollback` |
| A6 | Export contains no row belonging to another account | `test_export.py::test_no_cross_tenant_rows` |
| A7 | A purge leaves zero rows in every `TenantOwned` table | `test_deletion.py::test_purge_complete` |
| A8 | Deletion is owner-only; admin and staff are denied | `test_privacy_routes.py::test_owner_only` |
| A9 | A cancelled deletion restores normal service | `test_deletion.py::test_cancel` |
| A10 | Storage objects are deleted before their rows | `test_deletion.py::test_storage_before_rows` |
| A11 | An unsubscribe token is unforgeable | `test_suppression.py::test_token_hmac` |
| A12 | Free and Pro denied, Estate allowed, on both Estate gates | `test_estate_gates.py::test_gate_matrix` |
| A13 | **`record_change` fires for every account on every plan** | `test_estate_gates.py::test_audit_write_ungated` |
| A14 | Estate receives the weekly digest; Pro does not | `test_weekly_digest.py::test_scheduled_send_gated` |
| A14b | The on-request digest route works on **every** plan | `test_weekly_digest.py::test_on_request_ungated` |
| A15 | **Every scheduled workload is wired and fires** — enumerated from the tree | `test_jobs_enumeration.py::test_all_workloads_scheduled` |
| A16 | The backoff ladder runs five rungs, then stops selecting the row | `test_outbox.py::test_backoff_ladder` |
| A17 | Every `jobs` subcommand is a no-op on a second consecutive run | `test_jobs.py::test_idempotent` |
| A18 | Lifecycle mail carries both RFC 8058 headers; transactional carries neither | `test_unsubscribe.py::test_headers_by_class` |
| A19 | Every send writes exactly one `EmailDelivery` row | `test_delivery_log.py::test_one_row_per_send` |
| A20 | The documented DMARC record has no `adkim=s`/`aspf=s` | `test_docs_dns.py::test_dmarc_relaxed` |
| A21 | `email_suppressions` has **no** RLS policy | `test_email_tenancy.py::test_suppression_not_rls` |
| A22 | Suppressing an address twice is a no-op | `test_suppression.py::test_idempotent` |
| A23 | One `payment_failed` produces one immediate email, the rest on schedule | `test_dunning.py::test_ladder_schedule` |
| A24 | Recovery mid-ladder stops the sequence | `test_dunning.py::test_recovery_stops_ladder` |
| A25 | A drip sends each step once and never twice | `test_campaigns.py::test_no_duplicate_steps` |
| A26 | A second account's data never appears in the first's export | `test_export.py::test_tenant_isolation` |
| A27 | **The export enumerates every `TenantOwned` table** — discovered from `Base.metadata` | `test_export.py::test_covers_all_tenant_tables` |
| A28 | **The purge reaches every `TenantOwned` table** — discovered from `Base.metadata` | `test_deletion.py::test_purge_covers_all_tables` |
| A29 | The deletion record and the suppression list survive the purge | `test_deletion.py::test_deliberate_survivors` |
| A30 | The Phase 4 migration applies and reverts cleanly | `test_migration_phase4.py::test_up_down` |
| A31 | An unhandled exception renders the error page and emits one structured log record | `test_errors.py::test_handler_and_log` |
| A32 | No bare `except Exception` swallow remains in the request path | `test_errors.py::test_no_silent_swallow` |
| A33 | **All six GA gates are enumerated, with the three inbound ones reported unresolved** | `test_ga_readiness.py::test_all_gates_tracked` |

**A15 is the phase's definition of done.**

> **A scheduler that never fires is indistinguishable from a system with nothing to do.** The
> trial sweep, the reconciliation sweep, the outbox drain, the dunning ladder, the drips and the
> weekly digest are six workloads with one trigger. If that trigger is misconfigured — or if
> SPEC-004 D15's unverified assumption about Fly's scheduled machines is simply wrong — every one
> of them stops, no exception is raised, no test fails, and the first signal is a customer asking
> why they were never told their card failed.

This is the cost-control analogue of SPEC-004's A11 and the same adversarial shape: A15 must
discover scheduled workloads **from the tree** and assert each is registered and reachable, so the
test fails when someone adds a seventh without wiring it. A hand-maintained list rots, and it rots
silently.

**A33 is the exit criterion.** It does not assert the six gates are *green* — three of them
(§1.6) are founder decisions and one is a legal document. It asserts none of them is **silently
absent**, which is the failure this spec's scope was chosen to prevent.

---

## 9. Test manifest

```
tests/unit/test_email_service.py         klass required, suppression choke point, render
tests/unit/test_suppression.py           suppress/idempotent, HMAC tokens, lifecycle vs transactional
tests/unit/test_outbox.py                enqueue, drain, backoff ladder, failure isolation
tests/unit/test_campaigns.py             enrolment, due_sends, sequence shortening
tests/unit/test_estate_gates.py          the gate matrix + record_change ungated (A13)
tests/unit/test_jobs_enumeration.py      THE enforcement test (A15) — static, tree-walking
tests/unit/test_email_tenancy.py         the suppression RLS carve-out (A21) — static schema assertion
tests/unit/test_docs_dns.py              DMARC internal consistency (A20) — reads the repo, not DNS
tests/unit/test_errors.py                handler registration, structured log, no silent swallow
tests/integration/test_unsubscribe.py    RFC 8058 headers, one-click POST, no confirmation page
tests/integration/test_dunning.py        ladder schedule, recovery, no orphan sequences
tests/integration/test_delivery_log.py   one row per send, provider message id
tests/integration/test_export.py         completeness (A27), tenant isolation, presigned refs
tests/integration/test_deletion.py       state machine, purge completeness (A28), survivors
tests/integration/test_privacy_routes.py owner-only, cancel window
tests/integration/test_weekly_digest.py  scheduled send gated, on-request ungated
tests/integration/test_jobs.py           all six workloads, idempotency
tests/integration/test_migration_phase4.py  up/down — own engine, real Alembic
tests/integration/test_ga_readiness.py   THE exit criterion (A33)
```

**Fixtures.** Extend SPEC-004's `account_free` / `account_pro` with **`account_estate`**, plus:

- **`FakeEmailProvider`** — satisfies the Protocol structurally (no subclassing, per `AIProvider`'s
  precedent), recording `(to, subject, html, text, headers)` per call, with a settable failure mode
  so the backoff ladder is exercisable. Every test above except those parsing real provider payloads
  uses it. **Not** a Resend mock.
- **`frozen_now`** — an injected `datetime` for `drain`, `due_sends` and the ladder (N11). No test
  sleeps.
- **A populated-account fixture** — one account with at least one row in **every** `TenantOwned`
  table, so A27 and A28 are meaningful. A purge test against an account with three rows proves
  nothing.

**Coverage.** Add `resend_provider.py` to `pyproject.toml`'s `omit` list, following the existing
precedent for the AI provider HTTP implementations (`pyproject.toml:66-76`) and SPEC-004's
treatment of `stripe_provider.py`. Testing that Resend's SDK works is Resend's job; the seam worth
testing is the Protocol boundary, and `FakeEmailProvider` tests it.

**Migrations.** As SPEC-004 §9 records, `tests/conftest.py` builds schema from `Base.metadata` and
**no existing test exercises Alembic**. `test_migration_phase4.py` needs its own engine and must
run the real migration up and down (A30), or this phase's DDL — including the RLS carve-out —
ships unverified.

**The adversarial pattern for A15.** Not "does `drain-outbox` work when called" — that passes
trivially. Instead: enumerate every scheduled workload **by walking the tree** (the `jobs` command
group, plus any module declaring periodic work), then assert each is registered in the deployment
manifest and reachable through the entrypoint. The test must fail when someone adds a seventh
workload without scheduling it. Pair it with a check that the trigger mechanism itself is the one
Step 5 verified, not an assumption inherited from SPEC-004 D15.

**The adversarial pattern for A27/A28.** Same discipline, different target: both must derive the
table list from `Base.metadata` filtered by `TenantOwned`, never from a literal. Then assert the
export contains, and the purge empties, **every** one. A test that lists tables by hand passes
forever while the feature silently rots (N5).

---

## 10. What this phase does not make safe

This is the last spec in the set. There is no §10 after it to inherit these, so the list is
complete rather than forward-looking: **this is what GA ships with.**

### Carried forward, unaddressed (SPEC-004 §10's nine)

- **Secrets at rest — SPEC-003's O1, still open.** Provider API keys remain plaintext in
  `configurations.value` and `mihomes config list` still prints them unredacted. GA is precisely
  when strangers' keys start arriving, which makes this materially worse than it was in Phase 3
  without anything about it having changed.
- **Revenue correctness.** Every criterion here and in SPEC-004 proves the *mechanism*. None
  proves the **prices are right** — they are `PLACEHOLDER` until SPEC-004's O1 (§1.6).
- **The Stripe account's own configuration.** Products, prices, tax settings, the webhook endpoint
  secret, and whether the restricted key is scoped correctly all live in the Stripe dashboard.
  Nothing in this repo verifies them.
- **Cost attribution below the account**, and **inference cost vs. price** — SPEC-004 §10
  unchanged.
- **Mis-declared action keys** — the harness proves a route declares *something*, not the right
  thing (SPEC-003 §10).
- **The Telegram bot's transport** — still a supervised CLI process polling with a token in
  per-account config, not the authenticated webhook `TELEGRAM_PRD` §5 describes.
- **Aggregate inference by scoped staff** — SPEC-003 A15 tests the direct paths; it does not close
  inference.

### This phase's own

- **Deliverability is asserted, not observed.** A20 proves the repo's documented DMARC record is
  internally consistent (D17). It does not prove SPF, DKIM and DMARC actually pass at a real
  mailbox provider, that `send.mihomes.ai` is verified, or that mail lands in an inbox rather than
  a spam folder. `GTM` §5's checklist item — send a real test message and confirm placement — is a
  human task no test replaces.
- **GA ships single-provider.** `FailoverEmailProvider` is deferred (§1.4) and no standby is
  specified (F2). If Resend has an outage, transactional mail queues in the outbox and drains when
  they recover — which is *better* than losing it, but it is not availability. The outbox is the
  mitigation; it is not failover.
- **The audit log is scoped by application logic, not by a foreign key.** `audit_log` is
  polymorphic with no FK (F8), so SPEC-002's own step allows "application-only enforcement" for it.
  `audit_export` inherits that: a bug in scoping is not caught by the database.
- **Deletion is proven complete against the schema, not against reality.** A28 asserts every
  `TenantOwned` table is emptied. It cannot assert that no personal data was copied somewhere the
  ORM does not know about — provider-side logs, Stripe's records, Resend's delivery history, a
  support inbox. GDPR erasure covers those too, and nothing here addresses them.
- **Backups outlive deletion.** A purged account's rows persist in whatever managed-Postgres
  point-in-time backup covers the purge date (SPEC-002 D13). That is standard and defensible, but
  it means "hard delete" has a retention tail nobody has written down.
- **Observability is instrumentation, not alerting.** Step 15 makes the system *legible* — one
  logging config, structured records, real error handlers. Nobody is paged. `PRD_REVIEW` E4 asked
  which monitoring stack, and no doc has answered; `sentry-sdk` is config-gated but unconfigured.
  At GA, someone still has to be watching.
- **The drip content is unwritten at spec time.** Step 11 ships the machinery against fixture
  templates; O1 supplies the copy. A green Step 11 means drips *can* send, not that anything worth
  sending exists.
- **The PRDs remain stale.** Every doc-fix from SPEC-001 through SPEC-004 is still unapplied
  (§2.2). Anyone reading `docs/product/` directly — rather than the specs — is reading known-wrong
  values, and `PRD_REVIEW` §G is the catalogue of what that has already cost once.
