# SPEC-003 — Phase 2: Onboarding + Team + RBAC

**Phase:** 2 (canon — `../product/SAAS_PRD.md` §10)
**Status:** Ready to build
**Written:** 2026-08-03
**Source PRDs:** `../product/ONBOARDING_AUTH_RBAC.md` (primary), `../product/PRICING_AND_PACKAGING.md` §3, `../product/SAAS_PRD.md` §8.4–8.5, `../product/TELEGRAM_PRD.md` §4/§6
**Depends on:** SPEC-002 (Phase 1) — `accounts`, `memberships`, `invites`, `membership_property_scopes`, `sessions`, the `TenantOwned` mixin, and the scoped session. SPEC-001 for `EmailService`.

**Goal.** Turn a single-user multi-tenant app into a multi-*user* one: onboard an owner, invite
admins and staff, and enforce who may do what — composed with Phase 1's tenant scoping.

**Exit criteria** (`SAAS_PRD` §10, as read through D18): an owner can onboard, invite an
admin/staff, and **roles are enforced**; the entitlements service exists with the correct
`can()`/`usage()` shape.

**The stake.** Phase 1 defends the boundary *between* customers. Phase 2 defends the boundary
*inside* one. That is the harder problem: cross-tenant leakage fails loudly and RLS backstops
it, but a staff member seeing another property's data — or the household's finances — looks
exactly like the feature working. Every scoping decision below is enforced at the query layer
for that reason.

---

## 0. Two things a reader must know before trusting this spec

**0.1 — Phase 1 is not built.** `docs/specs/README.md:101-118` says Phases 2–4 are deliberately
unwritten because Phase 1's tenant layer is load-bearing, and "speccing the phases that sit on
top of it before it exists means writing rework." The stated precondition for overriding is to
read "Phase 1's *outcome*". There is no outcome: `account_id` appears **zero times** in any
`.py` file on any branch, `config.py` hardcodes `DB_URL = f"sqlite:///{DB_PATH}"`, and
`pyproject.toml` has no Postgres driver.

This spec was written anyway, by founder decision (2026-08-03). Consequences a reader must
carry:

- Every reference here to `account_id`, `TenantOwned`, `memberships`, `sessions`, or the scoped
  session is **forward-looking** — it describes SPEC-002's design, not code that exists.
- If SPEC-002's implementation diverges from its spec, **this document inherits the
  divergence.** Re-verify §4 and §5 against the tree before building.
- The three PRD conflicts catalogued in §2 (`homes`, `owner_user_id`, entitlements phasing) are
  precisely the rework the README predicted. They are fixed here, in the doc layer, rather than
  discovered during implementation.

**0.2 — Scope exceeds `SAAS_PRD.md:179`'s deliverable list.** Three items are founder-authorized
additions: the field-level redaction layer (D14), `documents.staff_visible` (D13), and Telegram
bot sender scoping (D15–D17, D19). The first two are not optional extras — without them,
"finances ✗ for staff" cannot be enforced at all, because the money lives inside rows staff are
permitted to see (F4). The third closes a live production leak.

`GTM_LAUNCH_PLAN.md:295` estimates this phase at 3–4 weeks. **That estimate no longer describes
this phase** and must be re-derived, not inherited. Steps 8, 9, 13, and 14 are net-new against
the canonical list. One item the prior draft of this spec *cut* — the config UI — is back in, per
SPEC-002 §7:614 (F7).

---

## 1. Decisions

### 1.1 Locked — inherited or doc-derivable

| # | Decision | Source |
|---|---|---|
| D1 | Roles are `owner` / `admin` / `staff`; exactly one **active** owner | `ONBOARDING` §9.1; enforced by SPEC-002 D4's partial unique index |
| D2 | Ownership moves only by **transfer**, never by invite or role-change | `ONBOARDING` §10 — the `owner` role can never be *assigned* |
| D3 | Staff scope is a **whitelist**; zero scope rows = zero properties visible | `ONBOARDING:44` — "fail closed, never 'all'". Properties added later are invisible to staff until explicitly scoped |
| D4 | Invites live in `invites`, not as a membership status | SPEC-002 D6/N7; `MULTITENANCY:118`. A membership needs a `user_id` an un-signed-up invitee lacks |
| D5 | The invite **token** is the authority, not the email | `ONBOARDING` §6.3, with its mismatch-notify mitigations |
| D6 | Seat = active `memberships` + pending `invites` (two tables) | `PRICING` §3.1 as corrected by `PRD_REVIEW` A1. The owner counts; a pending invite consumes a seat immediately |
| D7 | Entitlements ship **config-only** — every account `free`, limits from a config module | `SAAS_PRD:144` — "Without this, Phase 2 would secretly depend on Phase 3" |
| D8 | Role is loaded **fresh from the DB every request**, never cached in the session | `ONBOARDING` §3.3 (`:78` — "the session stores *who* and *which account is current* — never the role"), §9.4 step 2. Revocation takes effect on the next request |
| D9 | A cross-account target is **404**, not 403 | `ONBOARDING` §9.4 step 1 — do not reveal existence |
| D10 | RBAC and entitlements are **separate gates**; both must pass | `ONBOARDING` §9.4 step 5 — "a permission grant never bypasses a plan limit or vice versa" |

### 1.2 Locked — founder decisions, 2026-08-03

| # | Decision | Rationale |
|---|---|---|
| **D11** | Account switching is carried in a **session field** (`sessions.current_account_id`) | The inherited default: matches `ONBOARDING:205-212` as written and SPEC-002's assumption, and adds no routing work. **Known limitation:** one browser = one current account, so two families cannot be open side by side (workaround: two browser profiles). **Reversible** — the revisit trigger is the first real customer who is staff on two accounts; switching to a path prefix (`/a/{slug}/…`) then is a contained change. Neither choice affects isolation: `account_id` scoping plus RLS do that regardless. Resolves `MULTITENANCY` §10 Q6 |
| **D12** | Staff see vendor **contact information only**, read-only | Resolves a contradiction in the source (see F2b). Staff get `company_name`, `contact_name`, `phone`, `email`, `contacts`. Never `insurance_info`, `license_number`, `notes`, or ratings. No create, edit, or delete |
| **D13** | Documents carry a `staff_visible` flag, owner/admin controlled, **default `false`** | Closes a gap §9.3's carve-out leaves open — it names "account-level vendors, budgets, account settings", **not documents** (F2c). Fail-closed, consistent with D3: a housekeeper sees an appliance manual once it is ticked; a newly uploaded invoice is never exposed by default |
| **D14** | Money fields are **redacted** for staff, not row-denied | Matrix rows 6/7 grant staff scoped work orders, assets, and inventory; row 9 denies finances. They collide on the same records (F4). Redaction honours both without removing records staff need to do their jobs |
| **D15** | The Telegram bot is **scoped by sender** in Phase 2 | Founder: staff must not get financial answers from the bot. This is *intra-account role* scoping, which is separable from the tenant-awareness deferral at `SAAS_PRD:186` / SPEC-002 §7:615 — see §7-N7 |
| **D16** | An **unlinked** bot sender is treated as **staff-level** | Fail-closed without a dead bot. `TELEGRAM_PRD:158` specifies deny-by-default, which on day one would silence the bot for the entire Belle group — including the founder, since no links exist yet. Staff-level is restrictive enough to close the leak and lets linking be an upgrade rather than a prerequisite. **Departure from the PRD, deliberate** |
| **D17** | A financial answer is **never posted to a group containing staff**; the bot offers a DM | Scoping by *asker* alone still leaks: the bot replies into a shared group, so an owner's answer about monthly spend is read by every staff member in the chat |
| **D18** | **Plan gates are Phase 3**, not Phase 2 | `SAAS_PRD:180` places "plan gates (homes/seats/staff/AI)" in Phase 3's deliverables; `:184` says Phase 2 ships the service and Phase 3 "wires billing state into it." Phase 2 builds the interface. Nothing flips. See §1.4 |
| **D19** | The Telegram identity link is keyed on **`memberships`**, never `Staff` | Two role vocabularies exist and must not be crossed. `memberships.role` is `owner`/`admin`/`staff` — the capability matrix's vocabulary, and what D16 means by "staff-level". `StaffRole` (`models/staff.py:18-31`) is a **job** enum spanning `RESIDENT`/`OWNER`/`FAMILY_MEMBER`/`ASSOCIATE`. Resolving a sender via `Staff` and then applying a matrix decision would silently mix them — a `StaffRole.OWNER` housekeeping record is not an account owner. `TELEGRAM_PRD:129` specifies `telegram_user_id → membership → (account_id, role, home scopes)`, and `:158`'s "revoking a membership implicitly revokes the link" only holds if membership is the key |

### 1.3 `OPEN — needs decision: founder`

| # | Question | Why it cannot be defaulted |
|---|---|---|
| **O1** | Are AI provider API keys **encrypted at rest** before the Step 13 config UI starts writing them? | Today keys sit in plaintext in a shared `configurations.value: Text` column, `mihomes config list` prints them unredacted (`cli/config.py:39-50`), and there is no encryption anywhere in `src/` (F7). A web form multiplies the write paths. Masking on read (Step 13) is table stakes and is specified; at-rest encryption is a real decision with key-management consequences, and shipping plaintext silently is not acceptable. **Blocks Step 13's write path only** — the read/masking half can proceed |

Everything else this phase depends on is settled.

### 1.4 D18's consequence — how to read Phase 2's exit criterion

`SAAS_PRD:179` reads: "An owner can onboard, invite an admin/**staff**, and roles + **Free
limits are enforced**." Taken literally that is self-contradictory. Free sets
`staff_invites_allowed: false` (`PRICING` §3.1) and Phase 2 makes every account `free` (D7), so
the two halves cannot both be demonstrated in the same phase.

Given `:180` and `:184`, this spec reads the criterion as:

- **Roles are enforced** — real, testable, and the substance of the phase.
- **The entitlements service exists** with the correct `can()`/`usage()` shape, config-only.
- **Staff invites work in Phase 2 precisely because nothing gates them yet.** The gate arrives
  with Stripe in Phase 3.

Three items are **flagged forward to SPEC-004**, not resolved here:

| Ref | Conflict | Why it is Phase 3's |
|---|---|---|
| **P3-a** | `vendor_ratings: false` and `work_order_scheduling: false` on Free, but both features **ship today** (`services/vendor_rating.py`, `/work-orders` mounted, `routes/vendors.py:56` renders ratings) | Enforcing Free literally would *remove working features from every existing user*, including the founder. It is a pricing decision, and it only bites when a paid tier exists |
| **P3-b** | `ai_calls_per_month: 200` is **unenforceable** — no meter exists anywhere in `src/`. The only token record is `ai_conversations.tokens_used`, a nullable per-row int with no account and no monthly rollup | `usage()` therefore ships as a declared interface returning unlimited, tagged `DEFERRED (Phase 3)`. Building the meter is Phase 3 work per `PRICING` §5 |
| **P3-c** | `PRICING:250` says "The Free tier, gates, and billing UI ship in **Phase 3**", contradicting `SAAS_PRD:179`'s Phase 2 entitlements | Resolved in favour of the split: *service* in 2 (`SAAS_PRD:144`), *gates* in 3 (`:180`). Recorded as doc-fix B3 |

### 1.5 Survey findings that shaped this spec

Eight things found in the code and the source PRDs. Each drives a step below. All verified
against the tree on 2026-08-03.

**F1 — 146 endpoints need an action declaration, and nothing enforces that they have one.**
146 route decorators across 23 files in `web/routes/` — heaviest: `assets.py` 18,
`work_orders.py` 13, `vendors.py` 11, `properties.py` 10, `ai.py` 10. §9.2's footnote requires
that "an undeclared action is a deploy-time error, not a silent allow." That sentence is the
whole design, but it names no mechanism. Without one, 146 hand-edits produce silent gaps, and a
missed declaration on a **write** route is an authorization bypass, not a cosmetic omission.
→ Steps 4, 5.

**F2 — the capability matrix is not a lookup table.** `ONBOARDING:244-265` has **20 data rows**
(not 21 — an earlier draft of this spec miscounted). Every action is an English verb phrase
("Manage vendors", "Invite users (admin/staff)"); there are zero dotted identifiers and no
snake_case keys. Values are **three-valued** (`✓` / `✗` / `scoped`), and two cells carry prose
caveats *inside the cell*:

- Row 13 `Change a member's role` → admin `✓ (not owner's, not own)`
- Row 20 `Link chat gateway for self` → staff `✓ (scoped access applies)`

But §9.4 step 3 says to "look up `(role, action)` in the capability matrix." **There is nothing
to look up.** Encoding this is spec work, not transcription: §4.1 defines the action vocabulary
and hoists both caveats into explicit rules. → Step 1.

**F2b — the vendor rule contradicts itself.** Matrix row 8 says `Manage vendors | ✓ | ✓ |
scoped`. §9.3 (`:272`) says objects that do not belong to a home — "account-level vendors,
budgets, account settings" — are "account-level and therefore **✗ for staff by the matrix
above**." The matrix does not say ✗ for vendors; it says `scoped`. An implementer following the
matrix grants staff vendor access; one following §9.3 denies it, and both cite the same
document. §9.3's first paragraph leans permissive again: staff may "view/contact vendors for
that home." The doc's implicit reconciliation is the phrase "vendor **link** → home" — the join
is scoped, the record is account-level — but the matrix row never carries that distinction.
Resolved by **D12**. → Steps 1, 8.

**F2c — documents are `scoped` with nothing to scope by.** Matrix row 7 is `Manage inventory &
documents | ✓ | ✓ | scoped`, but §9.3's account-level carve-out lists only vendors, budgets, and
account settings. **Documents are not in it.** An account-level document — a contract, an
insurance policy — has no property to scope by, and no sentence anywhere resolves the case.
Silent, with no rescuing text, unlike F2b. Resolved by **D13**. → Step 9.

**F2d — genuinely silent entities.** Searched and confirmed: `salary`, `payroll`, and `cost`
appear **zero times** in `ONBOARDING_AUTH_RBAC.md`. `contract` appears once, only inside row 9's
parenthetical. `note` appears once (`:225`), and only to say notes are owned by the account
rather than the member — no matrix row, no scoping rule. Personnel records are never addressed;
row 10 "Manage staff" governs **memberships**, not HR data. §4.1's entity classification must
cover these rather than leave `require_permission` to guess.

**F3 — the AI advisor cannot be staff-scoped.** `assemble_context()` at
`services/ai/context.py:11` takes `property_slug: str | None = None` (keyword-only, `:16`) — one
*optional* property where staff need a *set*, and `None` fetches everything in the account
across all 14 `_fetch_*` helpers. `ai/tools.py` has **15** tool executors (verified three ways:
15 `_query_*` defs, 15 entries in the `_EXECUTORS` map at `:919-935`, 15 `TOOL_SCHEMAS` names),
dispatched by `execute_tool` at `:274`. §9.3 is explicit that this must be blocked "at the query
layer, so a staff member cannot exfiltrate another home's data by asking the AI about it." Tenant
RLS does not help — this is *within* one account. **The most security-sensitive item in the
phase, and the easiest to under-scope, because the feature appears to work while leaking.**
→ Step 10.

**F4 — money lives inside rows staff are permitted to see.** Verified money-bearing models:
`work_order.py`, `asset.py`, `consumable.py`, `contract.py`, `recurring_expense.py`,
`budget.py`, `event.py`, `document.py`, `alert.py`, `task.py`, `vendor_rating.py`. Matrix rows
6/7 grant staff scoped work orders, assets, and inventory; row 9 denies finances. Those collide
on the same records: a housekeeper who may see a work order can see its cost, and an asset she
is asked to service carries its value and price history.

`require_permission` **cannot express this** — it decides *whether* you get the row, not *which
columns*. Enforcing "finances ✗ for staff" therefore requires **field-level redaction** in
addition to row filtering. This is the finding the earlier draft of this spec missed entirely,
and it is the mechanism both D12 and D14 depend on. → Steps 6, 8.

**F5 — the Telegram bot has no permission layer, and scoping it touches three call sites.**
The bot is a CLI-driven polling process (`cli/telegram.py` `monitor`), not part of the FastAPI
app; `scripts/watchdog.py` respawns it. Findings:

- **Sender identity is captured and then discarded.** `gateways/telegram/client.py:156-160`
  builds `sender` (the Telegram user_id), `senderName`, and `senderUsername`. `sender` is read
  at exactly **one** place in the whole codebase — the PTO approver check,
  `responder.py:226-231`. `senderUsername` is never read. **Nothing is persisted.**
- **No link table and no column.** `Staff` (`models/staff.py:57-69`) has `phone`, `email`,
  `whatsapp_phone` — no Telegram field. `TELEGRAM_PRD:102,128-133` fully specifies
  `telegram_user_id → membership → (account_id, role, scopes)`; nothing implements it, and
  SPEC-002 §7:615 confirms `telegram_links` tables are **not** in the Phase 1 baseline.
- **Two independent DB paths, neither using the 15 executors.** Q&A: `_answer_question`
  (`responder.py:201`) → `orchestrator.ask` (`:174`) → `assemble_context`. Classification:
  `analyze_messages` (`review.py:199`) → `_build_estate_context` (`:120-196`), which hand-rolls
  direct queries on `Issue`, `Asset`, and `Staff`. The tool-executor path is reachable only from
  the web UI (`web/routes/ai.py:364,403`).
- **Scope is chat-level and sender-independent.** `telegram.chat_links` maps
  `{chat_id: property_slug}` (`client.py:164`); everyone in a group gets identical scope. Net
  effect today: any member of a linked group can ask anything and receives the full
  property-scoped estate context — open issues, assets, staff roster, budgets.
- **Side benefit of linking.** `_resolve_reporter` (`responder.py:340-347`) currently identifies
  who reported an issue by fuzzy-matching the *LLM's name guess* against
  `Staff.name ILIKE '%name%'` — not the authenticated sender. A real sender link fixes that.

→ Step 14.

**F6 — `require_permission` and entitlements are greenfield; the audit log is not.** Zero hits
for `require_permission` and zero for `entitlement` anywhere in `src/`, despite `ONBOARDING`
§9.4, `TELEGRAM_PRD` §6, `TWILIO_PRD` §4, and `PRICING` §3.2 all referencing them as though
built — and SPEC-002 §7:610 flags this explicitly ("**It does not.**"). But `models/audit_log.py:11`
**does** exist, and `AuditLog.actor` (`:25`) defaults to `"admin"`, which the bot never
overrides. So the audit work is *threading a real actor through an existing table*, not building
one — smaller than the earlier draft assumed, but it means every current call site writes a
fictional actor. → Step 2.

**F7 — the config UI is Phase 2 scope; an earlier draft wrongly cut it.** The literal phrase
"config UI" returns zero hits across `docs/`, which is what led the earlier draft to call it
invented. But SPEC-002 §7:614 assigns it outright: *"Per-tenant config UI | 2 | `configurations`
PK becomes `(account_id, key)` in Step 6. The web UI replacing `mihomes ai setup` is Phase 2 —
see `web/routes/ai.py:47`."*

It also matters more than that draft argued, because **SPEC-002 D1 drops local SQLite mode and
makes the CLI an operator tool** — citing `web/routes/ai.py:47` as its own justification. With no
user-facing CLI and no config UI, a tenant cannot configure anything at all. Today
`web/routes/ai.py:47-48` returns "Run `mihomes ai setup` in the CLI" *to the browser* as the
assistant's reply. Storage is ready (`MULTITENANCY:150-157` makes the PK `(account_id, key)` in
Phase 1); only the UI is missing. Carries **O1**. → Step 13.

**F8 — `home` vs `property` drift is a document-wide sweep, not three identifiers.**
`ONBOARDING_AUTH_RBAC.md` says "home" everywhere and never "property". Full catalogue in §2.
SPEC-002 D5/N8 locks `membership_property_scopes.property_id → properties.id` and forbids a
`homes` table; D4 says `accounts.owner_user_id` does not exist. Spot-fixing three lines would
leave eleven.

---

## 2. Doc-fix prerequisites

Apply before or alongside the build; each is a stale identifier or a phase conflict that would
otherwise be coded in.

| Ref | Fix | File / lines |
|---|---|---|
| **B1** | `membership_home_scopes(membership_id, home_id)` → `membership_property_scopes(membership_id, property_id)` | `ONBOARDING:44, 51, 52, 285` |
| **B2** | Drop `accounts.owner_user_id`; ownership is the partial unique index on `memberships` (SPEC-002 D4) | `ONBOARDING:35, 43, 220` |
| **B3** | Entitlements *service* → Phase 2; *gates* → Phase 3. Reconcile the three conflicting assignments | `product/README.md:61-62`, `MULTITENANCY:465`, `PRICING:250` |
| **B4** | `homes` table → `properties`; "home" stays a **UI word only** | `ONBOARDING:30, 44, 50, 51, 52, 134` (incl. the mermaid ER diagram) |
| **B5** | Fix the row-8 / §9.3 vendor contradiction per **D12**: row 8 becomes `View contact info` (staff `scoped`, read-only) | `ONBOARDING:253, 272` |
| **B6** | Add documents to §9.3's account-level discussion and reference the `staff_visible` flag (**D13**) | `ONBOARDING:252, 272` |
| **B7** | Record that the capability matrix needs machine-readable action keys; point at this spec §4.1 as canon | `ONBOARDING:244-265, 284` |
| **B8** | Note the two-route-class rule (item vs collection) beside §9.4 step 4 | `ONBOARDING:285` |
| **B9** | Replace the `PLACEHOLDER` on invite expiry with the locked 7-day value | `ONBOARDING:167` |
| **B10** | Correct `require_permission`'s signature: `target_home` → `target_property` | `ONBOARDING:280`, `TELEGRAM_PRD:159` |
| **B11** | Record **D16**'s deliberate departure from deny-by-default for unlinked senders | `TELEGRAM_PRD:158` |
| **B12** | Note that §11 Q2 (granular staff capabilities) is *partly answered now* — F2b/F2c were unresolved for today, not just for later | `ONBOARDING:307` |

---

## 3. File manifest

### New — authorization core

```
src/mihomes/authz/__init__.py              public API: require_permission, Action, Decision
src/mihomes/authz/actions.py               the 20-row matrix as data (§4.1) + route classes
src/mihomes/authz/permissions.py            require_permission() — §9.4's five ordered steps
src/mihomes/authz/scope.py                  scoped_property_ids() — THE scope primitive (§4.3)
src/mihomes/authz/redact.py                 redact_for_role() — field-level stripping (§4.4)
src/mihomes/authz/audit.py                  audit_write() wrapper over the existing AuditLog
```

### New — entitlements

```
src/mihomes/entitlements/__init__.py        public API: can, usage
src/mihomes/entitlements/limits.py          plan → limits config module (one source of truth)
src/mihomes/entitlements/service.py         can() live; usage() declared, DEFERRED (Phase 3)
```

### New — services

```
src/mihomes/services/onboarding_service.py  6-step resumable flow (§5)
src/mihomes/services/invite_service.py       create/resend/revoke/accept, hashed tokens
src/mihomes/services/membership_service.py   role change, offboarding, owner transfer
src/mihomes/services/telegram_link_service.py  membership↔telegram_user_id (D19)
```

### New — web

```
src/mihomes/web/routes/onboarding.py        the 6-step wizard
src/mihomes/web/routes/team.py              members, invites, scopes, transfer
src/mihomes/web/routes/settings.py          per-tenant config UI (F7, O1)
src/mihomes/web/templates/onboarding/*.html
src/mihomes/web/templates/team/*.html
src/mihomes/web/templates/settings/*.html
```

### New — models / migration

```
src/mihomes/models/telegram_link.py         TelegramLink (D19) — keyed on membership_id
alembic/versions/0003_phase2_rbac.py        documents.staff_visible, telegram_links,
                                            onboarding_state, audit_log.actor widening
```

### Modified

```
src/mihomes/models/document.py              + staff_visible (D13)
src/mihomes/models/audit_log.py             actor becomes a real FK-ish reference, not "admin"
src/mihomes/services/ai/context.py          assemble_context() — REQUIRED scope param (§4.3)
src/mihomes/services/ai/tools.py            all 15 executors take the scope set
src/mihomes/services/ai/agent.py            agent_stream() threads scope through
src/mihomes/web/routes/*.py                 146 endpoints × (action, route class) — 23 files
src/mihomes/web/routes/ai.py                delete the "run mihomes ai setup" hint (:47-48)
src/mihomes/services/gateways/telegram/responder.py   sender resolution + refusal (D15/D17)
src/mihomes/services/gateways/telegram/review.py      _build_estate_context takes scope
src/mihomes/services/config_service.py      mask secrets on list/get
```

---

## 4. Schemas as code

### 4.1 The capability matrix as data — `authz/actions.py`

F2's core problem: the source matrix has no keys. This is the canonical vocabulary. **20 actions,
one per matrix row**, in row order.

```python
class Grant(StrEnum):
    ALLOW  = "allow"    # ✓
    DENY   = "deny"     # ✗
    SCOPED = "scoped"   # scoped — allowed only within membership_property_scopes

class Access(StrEnum):
    """Which route class an action may be declared on."""
    ITEM       = "item"        # operates on one record; target_property REQUIRED
    COLLECTION = "collection"  # list/index; authorized by scoped_property_ids()
    ACCOUNT    = "account"     # account-level; no property target exists

@dataclass(frozen=True)
class ActionSpec:
    key:    str
    row:    int          # ONBOARDING §9.2 row number — traceability to the source
    owner:  Grant
    admin:  Grant
    staff:  Grant
    access: Access
    rule:   str | None = None   # hoisted prose caveat; see EXTRA_RULES

MATRIX: dict[str, ActionSpec] = {
    "property.view":        ActionSpec("property.view",        1,  ALLOW, ALLOW, SCOPED, ITEM),
    "property.edit":        ActionSpec("property.edit",        2,  ALLOW, ALLOW, DENY,   ITEM),
    "property.add":         ActionSpec("property.add",         3,  ALLOW, ALLOW, DENY,   ACCOUNT),  # plan-gated (Phase 3)
    "property.delete":      ActionSpec("property.delete",      4,  ALLOW, ALLOW, DENY,   ITEM),
    "task.manage":          ActionSpec("task.manage",          5,  ALLOW, ALLOW, SCOPED, ITEM),
    "issue.manage":         ActionSpec("issue.manage",         6,  ALLOW, ALLOW, SCOPED, ITEM),
    "inventory.manage":     ActionSpec("inventory.manage",     7,  ALLOW, ALLOW, SCOPED, ITEM),
    "vendor.view_contact":  ActionSpec("vendor.view_contact",  8,  ALLOW, ALLOW, SCOPED, ITEM,
                                       rule="D12: staff read-only, contact fields only"),
    "vendor.manage":        ActionSpec("vendor.manage",        8,  ALLOW, ALLOW, DENY,   ITEM,
                                       rule="D12: split from row 8 — staff never write vendors"),
    "finance.view":         ActionSpec("finance.view",         9,  ALLOW, ALLOW, DENY,   ACCOUNT),
    "member.manage":        ActionSpec("member.manage",        10, ALLOW, ALLOW, DENY,   ACCOUNT),
    "invite.create":        ActionSpec("invite.create",        11, ALLOW, ALLOW, DENY,   ACCOUNT),
    "invite.modify":        ActionSpec("invite.modify",        12, ALLOW, ALLOW, DENY,   ACCOUNT),
    "member.change_role":   ActionSpec("member.change_role",   13, ALLOW, ALLOW, DENY,   ACCOUNT,
                                       rule="R1"),
    "account.transfer":     ActionSpec("account.transfer",     14, ALLOW, DENY,  DENY,   ACCOUNT),
    "billing.manage":       ActionSpec("billing.manage",       15, ALLOW, DENY,  DENY,   ACCOUNT),
    "account.delete":       ActionSpec("account.delete",       16, ALLOW, DENY,  DENY,   ACCOUNT),
    "audit.view":           ActionSpec("audit.view",           17, ALLOW, ALLOW, DENY,   ACCOUNT),
    "ai.use":               ActionSpec("ai.use",               18, ALLOW, ALLOW, SCOPED, COLLECTION),
    "export.data":          ActionSpec("export.data",          19, ALLOW, ALLOW, DENY,   ACCOUNT),
    "gateway.link_self":    ActionSpec("gateway.link_self",    20, ALLOW, ALLOW, ALLOW,  ACCOUNT,
                                       rule="R2"),
}
```

Row 8 is deliberately **split in two** — `vendor.view_contact` and `vendor.manage` — because
D12 gives staff read access to some fields and no write access at all, which a single
three-valued cell cannot express. That is 21 keys for 20 rows; the `row` field preserves
traceability, and the acceptance test asserts all 20 rows are covered.

**The two hoisted caveats** (F2), which no lookup table can hold:

```python
# R1 — row 13's "(not owner's, not own)"
#   An admin may not change the role of the active owner, and may not change their own role.
#   The owner may change anyone's role except their own (ownership moves only by D2 transfer).
# R2 — row 20's "(scoped access applies)"
#   Staff MAY link their own chat gateway. The link grants no additional data access:
#   every resolved request re-enters require_permission with the staff role. Linking is
#   self-only for all roles — no role may link on another user's behalf.
EXTRA_RULES = {"R1": _rule_change_role, "R2": _rule_link_self}
```

**Entity classification** — closing F2d's silence. Every model is exactly one of:

| Class | Models | Staff rule |
|---|---|---|
| Property-scoped | `task`, `issue`, `work_order`, `asset`, `consumable`, `zone`, `space`, `appointment`, `event` | Visible if `property_id ∈ scoped_property_ids()`, money redacted (D14) |
| Property-linked via join | `vendor` (`property_ids` JSON) | Contact fields only, read-only (D12) |
| Flagged | `document` | Visible only if `staff_visible` **and** property-scoped (D13) |
| Account-level | `budget`, `contract`, `recurring_expense`, `transaction`, `configuration`, `note`, `book` | `✗` for staff |
| Personnel | `staff` | Staff may see their **own** record; never others' (F2d — the source never addresses this) |
| Global | `user`, `session`, `waitlist` | Not tenant data; unaffected |

### 4.2 New schema

```python
class TelegramLink(TenantOwned, Base):          # D19 — keyed on MEMBERSHIP, never Staff
    __tablename__ = "telegram_links"
    id:               Mapped[UUID] = mapped_column(primary_key=True, default=new_id)
    membership_id:    Mapped[UUID] = mapped_column(ForeignKey("memberships.id", ondelete="CASCADE"))
    telegram_user_id: Mapped[int]  = mapped_column(BigInteger, nullable=False)
    linked_at:        Mapped[datetime]
    __table_args__ = (
        UniqueConstraint("account_id", "telegram_user_id"),   # one link per sender per account
        Index("ix_telegram_links_lookup", "telegram_user_id"),
    )
    # ondelete=CASCADE is what makes TELEGRAM_PRD:158's "revoking a membership implicitly
    # revokes the link" true by construction rather than by remembering to do it.

# models/document.py — D13
staff_visible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
# default False: fail closed. A new invoice is never exposed; a manual is ticked deliberately.

# onboarding_state — resumability (§5, ONBOARDING:144)
class OnboardingState(TenantOwned, Base):
    __tablename__ = "onboarding_state"
    account_id:      Mapped[UUID] = mapped_column(primary_key=True)
    completed_steps: Mapped[list] = mapped_column(JSON, default=list)
    finished_at:     Mapped[datetime | None]
```

### 4.3 The scope primitive — `authz/scope.py`

**One implementation, four consumers**: web queries, the AI advisor's 15 executors, the bot's
Q&A path, and the bot's classification path. Written separately they drift, and drift is a leak.

```python
def scoped_property_ids(membership: Membership) -> frozenset[UUID]:
    """The set of properties this membership may see. THE authorization boundary
    for intra-account scoping.

    owner/admin → every property in the account (their scope rows are ignored, ONBOARDING:44)
    staff       → exactly their membership_property_scopes rows
    staff with zero scope rows → frozenset() — zero properties, never "all" (D3)
    """
```

**Binding interface rule: the scope parameter is REQUIRED, never optional.**

```python
# BEFORE (services/ai/context.py:11-18) — the footgun that created F3
def assemble_context(session, *, property_slug: str | None = None) -> str: ...
#                                                            ^^^^^^ None = the whole account

# AFTER — positional, required, no default
def assemble_context(session, scope: PropertyScope, *, property_slug: str | None = None) -> str: ...
```

Adding an *optional* scope argument would preserve the footgun: a new call site that forgets it
silently gets full access. A required positional parameter means a forgetting call site **fails
to import**. Same rule for all 15 executors and `agent_stream()`.

### 4.4 Redaction — `authz/redact.py`

F4: `require_permission` decides *whether* you get the row, not *which columns*.

```python
REDACTED_FIELDS: dict[type, frozenset[str]] = {
    WorkOrder:  frozenset({"cost", "estimated_cost", "actual_cost", "invoice_number"}),
    Asset:      frozenset({"value", "purchase_price", "price_entries"}),
    Consumable: frozenset({"unit_price", "last_order_cost"}),
    Contract:   frozenset({"cost", "billing_frequency"}),
    Vendor:     frozenset({"insurance_info", "license_number", "notes", "ratings"}),  # D12
    Task:       frozenset({"estimated_cost"}),
}

def redact_for_role(obj, role: Role) -> Any:
    """Strip money and sensitive fields for staff. owner/admin pass through unchanged.
    Applied in BOTH the web serializer and the AI context builder — one function, so a
    field added to one surface cannot be forgotten on the other.
    """
```

Redaction is **not** a template concern. Doing it in Jinja would leave the AI path — which
renders no templates — unprotected, which is exactly F3's shape.

---

## 5. Function signatures

```python
# src/mihomes/authz/permissions.py
def require_permission(
    user: User,
    current_account: Account,
    action: str,                       # a MATRIX key
    target_property: Property | UUID | None = None,
) -> None:
    """Raises HTTPException(404) on a cross-account or out-of-scope target (D9),
    403 on a role denial. §9.4's five ordered steps, in order.

    Route classes (see §6 Step 2):
      Access.ITEM       target_property REQUIRED when the grant is SCOPED; None → deny
      Access.COLLECTION no target; the query is constrained by scoped_property_ids()
      Access.ACCOUNT    no property target exists
    """

# src/mihomes/entitlements/service.py
def can(account: Account, action: str, context: dict | None = None) -> Decision:
    """Allowed | Denied(reason, upgrade_target). PRICING §3.2 rules 1-5.
    Separate gate from RBAC (D10) — both must pass.
    """

def usage(account: Account, meter: str) -> UsageReport:
    """DEFERRED (Phase 3) — returns {used: 0, limit: None, resets_at: None}.
    No AI meter exists (P3-b); this is a declared interface, not an enforced limit.
    """

# src/mihomes/services/invite_service.py
def create_invite(session, account, inviter, email, role, property_ids) -> tuple[Invite, str]:
    """Returns (invite, plaintext_token). Only the HASH is stored (D5).
    Rejects a staff invite with zero property_ids (ONBOARDING:164, D3).
    """
def accept_invite(session, token: str, user: User) -> Membership:
    """Transactional: re-checks the seat count inside the transaction so two concurrent
    acceptances at the cap cannot both succeed (PRICING §3.2 rule 5).
    """

# src/mihomes/services/membership_service.py
def transfer_ownership(session, account, from_member, to_member) -> None:
    """Against memberships + its partial unique index (SPEC-002 D4).
    NOT accounts.owner_user_id — that column does not exist (B2).
    """

# src/mihomes/services/telegram_link_service.py
def resolve_sender(session, telegram_user_id: int, account: Account) -> Membership | None:
    """None → treat as staff-level (D16), not deny. A revoked membership fails
    resolution because the link CASCADEs (§4.2).
    """
```

---

## 6. Sequenced steps

**Step 1 — action vocabulary + matrix as data.** §4.1: 21 keys covering all 20 rows, the two
hoisted rules, the entity classification. *Verify:* a test asserts every `ONBOARDING` §9.2 row
number 1–20 appears in `MATRIX`; R1 and R2 have direct unit tests.

**Step 2 — `require_permission` + audit, with the two route classes.** §9.4's five ordered steps.
Audit lands **here, not last** — every deny is an audit event (§9.4's closing paragraph), so
building it later means retrofitting call sites into Steps 3–14. `AuditLog.actor` must carry the
real actor; today it defaults to `"admin"` and the bot never overrides it (F6).

> **The route-class rule, and why it is settled before Step 5.** §9.4 leaves undefined what
> happens when a grant is `SCOPED` but `target_property` is `None`. A flat "None → deny" is
> **wrong**: `GET /tasks` has no single target, so every collection route would 403 for staff and
> Step 7's filtering would be unreachable code. Item routes require a target; collection routes
> are authorized by `scoped_property_ids()` constraining the query, per §9.4 step 4's own words
> ("filtered to scoped homes at the query layer, not post-hoc"). **Consequence:** each endpoint
> declares **two** facts — action *and* route class — which is why this precedes the 146 edits.

*Verify:* a `SCOPED` item route with `target_property=None` denies; a `SCOPED` collection route
with no target returns filtered rows, not 403; a cross-account target yields 404 (D9); a revoked
membership denies on the very next request (D8).

**Step 3 — entitlements service.** `can()` live per `PRICING` §3.2 rules 1–5; `usage()` declared
and `DEFERRED` (P3-b). Limits in one config module (rule 1). Per `SAAS_PRD:144`, shipping it now
is what prevents Phase 2 secretly depending on Phase 3. *Verify:* `can()` is called at invite
creation and property creation server-side; a `Denied` always names an `upgrade_target` (rule 4).

**Step 4 — the fail-closed harness.** F1. A test that walks the FastAPI router table and fails on
any endpoint lacking `(action, route_class)`. **Built before Step 5.** Two allowlists:

- **Permanent**, reviewed: genuinely unauthenticated routes (health, login/OIDC callback,
  webhooks, static). Each entry needs a one-line justification in the file.
- **Shrinking**, temporary: not-yet-declared routes, so the harness is *enforceable during* the
  migration instead of red until it finishes. A test asserts this list only ever gets shorter.

*Verify:* adding a new undeclared route to a scratch module makes the suite fail.

**Step 5 — declare actions on 146 endpoints.** Mechanical, chunked by router file (23 files),
verified continuously by Step 4 as the shrinking allowlist empties. **Residual risk, stated
plainly:** the harness catches *undeclared*, not *mis-declared* — 21 keys across 146 endpoints
means a route can declare the wrong action and pass. Mitigation: a focused human review of every
**write, delete, and export** route, listed explicitly in the PR.

**Step 6 — the scope primitive.** §4.3 `scoped_property_ids()` + §4.4 `redact_for_role()`. One
implementation. *Verify:* a staff membership with zero scope rows yields `frozenset()`;
owner/admin yield every property in the account even with scope rows present.

**Step 7 — staff scoping in web queries.** Filtered at the query layer, never post-hoc. Reuses
the per-property filtering pages already have (`tasks.py` takes a `property_id`); the change is
that staff get an **enforced allowed-set** rather than an optional user-chosen filter.
Owner/admin behaviour is unchanged. *Verify:* a scoped staff `GET /tasks` returns only scoped
rows; requesting an out-of-scope `property_id` explicitly yields 404, not an empty list.

**Step 8 — field-level redaction.** D12, D14, F4. Applied in the web serializer **and** the AI
context builder via the same function. *Verify:* one test per model in `REDACTED_FIELDS` asserts
the field is absent for staff and present for admin — including through the AI path.

**Step 9 — document visibility.** D13. `documents.staff_visible` default `false`; owner/admin
toggle in the UI; staff queries filter on it **and** on property scope. *Verify:* a newly
uploaded document is invisible to staff until ticked.

**Step 10 — AI scoping.** F3. Thread the required scope set through `assemble_context()`, all 15
executors, and `agent_stream()`. Enforced at the query, per §9.3 — "a retrieval constraint, not
a prompt nicety." **The highest-risk step in the phase**: it looks like it works while leaking.
*Verify:* the §9.3 exfiltration test — a staff member scoped to property A asks the AI about
property B's tasks and receives nothing, on every one of the 15 executors.

**Step 11 — onboarding flow.** 6 steps per §5 (`:130-137`). Steps 2 (create account) and 3 (add
first property) are the **only** hard requirements; steps 1 and 6 are non-interactive screens
(the source marks them `—`, not "Yes"); steps 4–5 are skippable and skipping is a first-class
path. Prefill the account name from the Google profile, default type `household`, require only
the property *name*. Idempotent and resumable via `onboarding_state`. **Billing never blocks it**
(`:143`). *Verify:* dropping off after step 2 resumes at step 3; a skipped step 5 lands on the
dashboard.

**Step 12 — invites.** Create/resend/revoke/accept. Tokens hashed, single-use, 7-day expiry
(B9); the token is the authority (D5) with §6.3's mismatch-notify mitigations. Seat re-check
**inside** the acceptance transaction (`PRICING` §3.2 rule 5). A staff invite with zero
properties is rejected (D3). Email types `welcome`, `staff_invite`, `invite_accepted` on the
SPEC-001 `EmailService`. *Verify:* two concurrent acceptances at the seat cap — exactly one
succeeds; an expired token is rejected; a revoked invite cannot be accepted.

**Step 13 — account switcher.** D11: updates `sessions.current_account_id` server-side and
persists `last_used_account`. **Hidden entirely for single-account users**, so a homeowner who
will never see a second account gets no added clutter. *Verify:* switching changes every
subsequent request's data; the control is absent with one membership.

**Step 14 — owner transfer + member offboarding.** Against `memberships` and its partial unique
index (SPEC-002 D4), **not** `accounts.owner_user_id` (B2). Last-owner invariant enforced. On
offboarding, tasks/notes/issues/uploads stay with the **account** (`ONBOARDING:225`), and the
membership's `TelegramLink` CASCADEs away (§4.2). *Verify:* the last owner cannot be removed or
demoted; transfer leaves exactly one active owner; an offboarded member's chat link stops
resolving immediately.

**Step 15 — per-tenant config UI.** F7; SPEC-002 §7:614 assigns it here. A settings form over the
existing `config_service` (`get_config`/`set_config`/`list_config`/`reset_config`), replacing
`web/routes/ai.py:47-48`'s "run `mihomes ai setup` in the CLI" — which is currently returned *to
the browser* as the assistant's reply. Owner/admin only (matrix row 2). **Secret values masked on
read**, in both the web UI and `mihomes config list` (`cli/config.py:39-50` prints them
unredacted today). **Carries O1** — at-rest encryption is unresolved and blocks the *write* path
for secret keys only; the read/masking half proceeds regardless. *Verify:* a staff member gets
403; `ai.*_api_key` renders masked everywhere; the CLI no longer prints raw keys.

**Step 16 — Telegram bot scoping.** D15, D16, D17, D19, F5. Four parts:

1. `telegram_links` (§4.2) + a `/link <code>` flow per `TELEGRAM_PRD:126-127` — short-lived,
   single-use, hashed codes bound to `(user_id, account_id, membership_id)`.
2. Resolve the sender from `message["sender"]`, already present on every message
   (`client.py:158`) and already used for the PTO approver check (`responder.py:230`). Unlinked →
   **staff-level** (D16), not denied.
3. Thread the Step 6 primitive through **both** DB paths — `orchestrator.ask`/`assemble_context`
   *and* `review.py:120` `_build_estate_context`. Missing either leaves a hole.
4. **D17:** a financial question is never answered into a group containing staff; the bot offers
   a DM instead.

Fix `_resolve_reporter` (`responder.py:340-347`) to prefer the resolved sender over the LLM's
fuzzy name guess. Scoped as a **safety fix for the live deployment** — explicitly *not* the
tenant-awareness work deferred at `SAAS_PRD:186` / SPEC-002 §7:615 (see §7-N7). *Verify:* a
staff sender asking "how much did we spend this month?" is refused; an owner asking the same in
a staff-containing group gets a DM offer, not the number in-channel.

**Step 17 — cross-cutting adversarial tests.** Per-step criteria live with their steps; this step
is only the leak matrix. See §9.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not treat "declare actions on 146 endpoints" as one task.** It is 23 router files, and
the declaration carries *two* facts per endpoint (Step 2). Build the harness first (Step 4) or
the edits are hopeful rather than verified. Under-scoping this is the most likely way the phase
slips.

**N2 — Do not add the scope parameter as optional.** `assemble_context(property_slug=None)` is
the footgun that created F3: `None` means "the whole account." An optional scope argument
preserves it. Required and positional, so a forgetting call site fails to import (§4.3).

**N3 — Do not redact in templates.** The AI path renders no templates, so template-level
redaction leaves it unprotected — F3's exact shape. Redact in one function called by both
surfaces (§4.4).

**N4 — Do not scope only the property-bearing entities.** Vendors, contracts, budgets, notes, and
personnel records have no `property_id`; threading a property set past them silently allows them.
Every model must land in one §4.1 class.

**N5 — Do not deny a `SCOPED` capability merely because `target_property` is `None`.** That
403s every list page for staff and makes Step 7 unreachable. Item vs collection (Step 2).

**N6 — Do not key the Telegram link on `Staff`.** Two role vocabularies: `memberships.role` is
the matrix's, `StaffRole` is a job enum containing its own `OWNER`. Crossing them makes a
housekeeping "owner" an account owner (D19).

**N7 — Do not treat Step 16 as chat-gateway tenant-awareness.** Multi-tenant chat linking,
webhook transport, and per-account bot routing remain Phase 4+ (`SAAS_PRD:186`, SPEC-002
§7:615). Step 16 is *intra-account role scoping on the single existing deployment* — it closes a
live leak and adds no tenant routing.

**N8 — Do not enforce `vendor_ratings: false` or `work_order_scheduling: false`.** Both features
ship and work today. Enforcing the Free row literally would delete working functionality from
every user (P3-a). Phase 3's call.

**N9 — Do not build the AI usage meter.** `usage()` is a declared interface returning unlimited
(P3-b). Metering is `PRICING` §5, Phase 3.

**N10 — Do not cache the role in the session.** D8; `ONBOARDING:78`. Revocation must take effect
on the next request, which means a fresh membership load every time.

**N11 — Do not write secret config values to a plaintext column from a new web form until O1
is answered.** The masking half of Step 15 proceeds; the secret-write half waits.

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| Stripe Checkout/Portal, webhooks | 3 | `can()` already takes billing status as an *input* (`PRICING` §3.2 rule 3); Phase 3 supplies it |
| Plan gates (homes/seats/staff/AI) | 3 | `SAAS_PRD:180`. `can()` exists and is called; the limits config simply says "free, unlimited" |
| AI usage meter + `usage()` behaviour | 3 | Signature ships now (§5); the events table and rollup are Phase 3 |
| `vendor_ratings` / `work_order_scheduling` gating | 3 | Flags exist in the limits module, wired to nothing (N8, P3-a) |
| Granular per-capability staff permissions | 4+ | `ONBOARDING` §11 Q2. `MATRIX` is per-role today; per-membership overrides would key on `membership_id` |
| Chat-gateway tenant-awareness | 4+ | `telegram_links` is per-account from birth (§4.2), so Phase 4 adds routing, not a migration |
| Non-Google invitees | 4+ | `ONBOARDING` §11 Q3 — the `IdentityProvider` abstraction anticipates it |
| Audit log retention/export | 4+ | `ONBOARDING` §11 Q6; `export.data` exists as an action key |
| Cross-account seat accounting for professionals | 4+ | `ONBOARDING` §11 Q5 |
| At-rest secret encryption | ? | **O1.** Until answered, Step 15 masks on read and does not add secret write paths |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | All 20 `ONBOARDING` §9.2 rows are represented in `MATRIX` | `test_matrix.py::test_all_twenty_rows_covered` |
| A2 | R1: an admin cannot change the owner's role or their own | `test_matrix.py::test_rule_change_role` |
| A3 | R2: linking a gateway grants no extra data access | `test_matrix.py::test_rule_link_self` |
| A4 | Every route declares `(action, route_class)`; an undeclared route fails the suite | `test_route_declarations.py::test_no_undeclared_routes` |
| A5 | The temporary allowlist only ever shrinks | `test_route_declarations.py::test_allowlist_monotonic` |
| A6 | A `SCOPED` **item** route with `target_property=None` denies | `test_permissions.py::test_item_route_requires_target` |
| A7 | A `SCOPED` **collection** route returns filtered rows, **not 403** | `test_permissions.py::test_collection_route_filters` |
| A8 | A cross-account target yields **404**, not 403 | `test_permissions.py::test_cross_account_is_404` |
| A9 | Revoking a membership denies on the next request (no session cache) | `test_permissions.py::test_revocation_immediate` |
| A10 | Staff with zero scope rows see **zero** properties | `test_scope.py::test_empty_scope_is_empty` |
| A11 | owner/admin see all properties even with scope rows present | `test_scope.py::test_privileged_ignores_scope_rows` |
| A12 | Money is redacted for staff on every `REDACTED_FIELDS` model | `test_redaction.py::test_money_hidden_per_model` |
| A13 | Staff see vendor contact fields only; no writes (D12) | `test_redaction.py::test_vendor_contact_only` |
| A14 | A new document is invisible to staff until `staff_visible` is set (D13) | `test_documents.py::test_default_hidden` |
| A15 | **AI exfiltration: scoped staff cannot reach another property's data via any of the 15 executors** | `test_ai_scoping.py::test_no_cross_property_exfiltration` |
| A16 | Redaction holds through the AI path, not just the web serializer | `test_ai_scoping.py::test_money_redacted_in_context` |
| A17 | Onboarding resumes at step 3 after dropping off at step 2 | `test_onboarding.py::test_resumable` |
| A18 | Skipping steps 4–5 lands on the dashboard | `test_onboarding.py::test_skip_optional` |
| A19 | Two concurrent invite acceptances at the seat cap: exactly one succeeds | `test_invites.py::test_seat_race` |
| A20 | An expired or revoked invite token is rejected | `test_invites.py::test_token_lifecycle` |
| A21 | A staff invite with zero properties is rejected | `test_invites.py::test_staff_needs_scope` |
| A22 | The last owner cannot be removed or demoted | `test_membership.py::test_last_owner_protected` |
| A23 | Transfer leaves exactly one active owner, via `memberships` | `test_membership.py::test_transfer_invariant` |
| A24 | The switcher is absent for single-account users | `test_switcher.py::test_hidden_when_single` |
| A25 | `can()` denies name an `upgrade_target` (`PRICING` rule 4) | `test_entitlements.py::test_denied_names_target` |
| A26 | RBAC and entitlements are independent gates (D10) | `test_entitlements.py::test_both_gates_required` |
| A27 | Staff get 403 on the config UI; secrets render masked everywhere | `test_settings.py::test_staff_denied`, `::test_secrets_masked` |
| A28 | An unlinked bot sender is treated as staff-level, not denied (D16) | `test_telegram_scope.py::test_unlinked_is_staff` |
| A29 | A staff sender's financial question is refused | `test_telegram_scope.py::test_staff_financial_refused` |
| A30 | A financial answer is never posted into a staff-containing group (D17) | `test_telegram_scope.py::test_group_dm_offer` |
| A31 | Both bot DB paths are scoped — Q&A **and** classification | `test_telegram_scope.py::test_both_paths_scoped` |
| A32 | Revoking a membership immediately breaks its chat link | `test_telegram_scope.py::test_revocation_cascades` |
| A33 | Every privileged action and **every deny** writes an audit row with a real actor | `test_audit.py::test_denies_and_actor` |

**A15 is the phase's definition of done.** Roles enforced in the UI while the AI answers freely
is not a partial success — it is the leak wearing the feature's clothes. If A15 is not green,
Phase 2 is not finished regardless of what else works.

---

## 9. Test manifest

```
tests/unit/test_matrix.py                  20-row coverage, R1/R2, entity classification
tests/unit/test_scope.py                   scoped_property_ids: empty, staff, owner/admin
tests/unit/test_redaction.py               per-model field stripping, vendor contact-only
tests/unit/test_route_declarations.py      THE fail-closed harness (A4/A5) — static
tests/unit/test_entitlements.py            can() rules 1-5, usage() declared-only
tests/integration/test_permissions.py      §9.4's five steps, route classes, 404-not-403
tests/integration/test_onboarding.py       6 steps, mandatory/skippable, resumability
tests/integration/test_invites.py          lifecycle, hashed tokens, seat race
tests/integration/test_membership.py       role change, transfer, last-owner, offboarding
tests/integration/test_switcher.py         current_account_id, hidden for single-account
tests/integration/test_documents.py        staff_visible default + filtering
tests/integration/test_settings.py         config UI perms, secret masking
tests/integration/test_ai_scoping.py       THE exfiltration test (A15/A16) — all 15 executors
tests/integration/test_telegram_scope.py   sender resolution, refusals, both paths, cascade
tests/integration/test_audit.py            privileged actions + denies, real actor
```

**Fixtures.** Extend SPEC-002's `account_a`/`account_b` with `owner_a`, `admin_a`, `staff_a`
(scoped to one property), and `staff_a_unscoped` (zero scope rows — the fail-closed case).
Reuse the existing `session` fixture name and semantics, per SPEC-002's §9 note.

**The adversarial pattern for A15.** Not "does the scoped query work" — that passes trivially.
Instead: seed two properties with distinguishable data, then for **each** of the 15 executors
assert that a staff member scoped to A cannot obtain B's rows by any phrasing, including asking
for "all", asking by B's name, and asking for aggregates that would sum across both. An
aggregate is the case a row-level filter can pass while still leaking a total.

---

## 10. What this phase does not make safe

Stated so the next spec inherits it honestly:

- **Mis-declared actions.** The harness proves every route declares *something*, not that it
  declared the *right* thing (Step 5). A `task.manage` on a route that deletes contracts would
  pass. Mitigated by human review of writes/deletes/exports, not eliminated.
- **Secrets at rest.** O1 is open. Until answered, provider API keys remain plaintext in
  `configurations.value`; Step 15 masks them on display and does not make them safe.
- **The bot's transport.** Step 16 scopes *answers*. The bot still polls with a token in
  per-account config and runs as a supervised CLI process, not the authenticated webhook
  `TELEGRAM_PRD` §5 describes. Phase 4+.
- **Aggregate inference.** A staff member scoped to one property can sometimes infer account-level
  facts from what they *can* see. A15 tests the direct paths; it does not close inference.
