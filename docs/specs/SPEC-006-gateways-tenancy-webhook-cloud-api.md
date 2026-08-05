# SPEC-006 — Gateways: tenancy, webhook transport, WhatsApp Cloud API

**Phase:** 4+ growth bet (canon — `../product/SAAS_PRD.md` §10; **not** a Phase 5 — see §0.2)
**Status:** Ready to build — **2 open decisions** (O1: Cloud API tier + group support; O2: webhook host for local installs)
**Written:** 2026-08-05
**Verified against:** `origin/main` @ **`be8d398`** (2026-07-30). Every code claim below was checked with `git show be8d398:<path>`. **This is not the branch the doc set was written on** — see §0.1.
**Source PRDs:** `../product/TELEGRAM_PRD.md` (the trustworthy gateway PRD — phase mapping §"Mapped to the product phases", linking flow), `../product/OMNICHANNEL_GATEWAY_PRD.md` and `../product/WHATSAPP_GATEWAY_PRD.md` (**both defective — repaired in §2**), `../product/TWILIO_PRD.md` §2.3 (the shared-core recommendation, now satisfied), `../PRD_REVIEW.md` §G
**Depends on:** SPEC-002 (Phase 1) — `account_id`, RLS, the scoped session. SPEC-003 (Phase 2) — `memberships`, `can()`, `require_permission`, and **`TelegramLink`**, which SPEC-003 §4.2 already ships as `TenantOwned` with `UniqueConstraint("account_id", "telegram_user_id")`. SPEC-005 (Phase 4) — GA must have happened; this is post-GA work.

**Goal.** Make the chat gateways multi-tenant, move them off polling onto webhooks, and replace
the Baileys WhatsApp bridge with the official Cloud API — so the way estate staff actually talk to
the product survives contact with hosted customers.

**Exit criteria:** a message from a linked sender in account A creates a row in account A and is
invisible to account B; the same message arrives by webhook with no polling process running; and
WhatsApp delivers through the Cloud API with Baileys removed from the tree.

**The stake.** The gateways are the product's real interface for the people who do the work —
housekeepers, groundskeepers, contractors — most of whom will never open the web app. Today every
one of them talks to a single-tenant process. The failure mode this phase exists to prevent is
specific and severe: **a gateway with no tenancy does not fail closed, it fails into the wrong
account.** An unlinked sender whose message lands in whichever account the process happens to be
configured for is a cross-tenant write that looks exactly like the feature working — the same
shape as SPEC-005's export defect, arriving through a channel where nobody is watching a screen.

---

## 0. Four things a reader must know before trusting this spec

**0.1 — This spec is verified against `origin/main`, not against the branch the doc set was
written on.** That distinction is load-bearing and it is why this section leads.

SPEC-001 through SPEC-005 were written against `telegram-bot`. **`telegram-bot` is 13 commits
behind `origin/main` and 30 ahead of it** (`git rev-list --left-right --count origin/main...telegram-bot`
→ `13  30`). The gateway work landed on main in `c4954a0` and its follow-ups, which
`telegram-bot` forked before. So the same file read on the two refs gives different answers, and
**a claim about "the gateway code" is meaningless without naming a ref.**

This caused a real error during this spec's own research: the shared responder core was reported
as non-existent, because `telegram-bot` genuinely does not have it. It exists on main. The lesson
is `README.md:167`'s rule with a clause added — code claims must be verified against the tree
**and the tree must be named**. `PRD_REVIEW` §G is the catalogue of what unverified claims cost;
this is the same failure with a ref instead of a fact.

Consequence: **if this spec is built from `telegram-bot`, everything in §3–§5 is wrong**, because
it assumes a core that branch lacks. Reconciling the two branches is a prerequisite and is **not**
in this spec's scope (§10).

**0.2 — This is Phase 4+ growth-bet work. There is no Phase 5.** `SAAS_PRD` §10's table ends at
Phase 4 (GA); the row beneath it is `4+ | Growth bets (separate PRDs) | … | Per-PRD`. `SAAS_PRD:186`
is explicit that chat gateways "remain **single-tenant/founder-only until made tenant-aware** (a 4+
growth bet); they are not part of the hosted MVP."

Inventing a Phase 5 here would repeat exactly the defect `PRD_REVIEW` **G4** catches
`OMNICHANNEL:580-589` making — a private phase numbering colliding with the canon one, in a doc
set where `README` declares phases canon. `TELEGRAM_PRD` carries the correct hedge already
("Phase 2 here is a dependency floor, not committed Phase 2 scope"), and §2's **B4** copies its
wording into the two docs that lack it.

**0.3 — The shared responder core already exists. Do not re-extract it.** `TWILIO_PRD` §2.3
recommended extracting a channel-agnostic core "before Twilio adds a third responder". **That
recommendation has been satisfied.** On `be8d398`:

- `src/mihomes/services/gateways/review_common.py` — **1,175 lines**, containing the
  `GatewayAdapter` seam (`:48`), a `REVIEW_SCHEMA` documented as "the SUPERSET of every category
  either gateway can act on", `analyze_messages` (`:507`), `dispatch_items` (`:723`),
  `handle_approval_messages` (`:595`) and `is_trusted_sender` (`:683`)
- `gateways/dedup.py` — `ProcessedIdStore` (`:32`), `PoisonGuard` (`:77`), `poll_lease` (`:145`)
- `gateways/pid.py` — process-liveness helpers
- responders collapsed to **271** (Telegram) and **285** (WhatsApp) lines
- **six** gateway test files exist

Both PRDs treat this as future work. It is past work. §2 corrects them, and §3–§5 **consume** the
core rather than rebuilding it.

**0.4 — Both source gateway PRDs are known-defective, and this spec repairs them rather than
working around them.** `PRD_REVIEW` §G ("do not spec from these yet") catalogued nine false code
claims, contradictory category counts, two different paths for one module, and a colliding phase
numbering across `OMNICHANNEL_GATEWAY_PRD.md` and `WHATSAPP_GATEWAY_PRD.md`. Recommendation 4 said
"fix or quarantine". **Neither happened** — the five following commits wrote SPEC-001–005, and
SPEC-005 §2.3 documented the problem instead of fixing it.

§2 lands the repairs. This is affordable because §G did the hard half: it states the *correct
value* for nearly every defect, so the work is transcription. Three of its findings are **now
additionally stale** because main moved (§2.1's B2, B5, B7) — verifying against the current ref
changed the answer, which is §0.1 restated as a worked example.

---

## 1. Decisions

### 1.1 Locked — inherited or doc-derivable

| # | Decision | Source |
|---|---|---|
| D1 | **Gateways are a post-GA growth bet, not GA scope** | `SAAS_PRD:186`, §6.2. `TELEGRAM_PRD`'s phase mapping carries the hedge verbatim |
| D2 | **`GatewayAdapter` is the extension point for every channel** — `label` + `send(chat_id, text)` | `review_common.py:48`. Twilio, Cloud API and any future channel implement this and nothing else |
| D3 | **`TelegramLink` is keyed on `membership_id`, never on `Staff`** | SPEC-003 **D19**, §4.2. Two role vocabularies exist and crossing them makes a housekeeping "owner" an account owner |
| D4 | **Revocation is `ondelete=CASCADE`, true by construction** | SPEC-003 §4.2 — "revoking a membership implicitly revokes the link" without anyone remembering to do it |
| D5 | **One link per sender per account**: `UniqueConstraint("account_id", "telegram_user_id")` | SPEC-003 §4.2. The same human may legitimately hold links in two accounts |
| D6 | **The category superset is settled and shared** — 15 categories, one schema, both channels | `review_common.py:63`. Retires the 15-vs-8 asymmetry as a live issue (§2's B5) |
| D7 | **Webhook signature verification reads raw bytes before any parse** | SPEC-004 **N3**, established for Stripe. Identical discipline, different vendor |
| D8 | **Sensitive actions stay gated on sender trust** | `review_common.py:661` `SENSITIVE_CATEGORIES`, `:683` `is_trusted_sender`. Tenancy *extends* this seam; it does not replace it |

### 1.2 Locked — founder decisions, 2026-08-05

| # | Decision | Rationale |
|---|---|---|
| **D9** | **WhatsApp stays in the product and migrates Baileys → the official Cloud API** | Founder decision. This resolves `PRD_REVIEW` **G5**, which no fact-correction could: `WHATSAPP:159` says the Developer tier has no group support while `:643` promises "no behavior change", and the live product is group-based. It was a product question wearing a documentation defect's clothes. The migration target is the Cloud API, and the group question becomes **O1** rather than a silent contradiction |
| **D10** | **The Cloud API implementation satisfies the *existing* `WhatsAppBridge` Protocol** | `whatsapp/protocol.py` already declares `send_message` (`:13`), `send_template` (`:25`), `get_message_status` (`:43`) and `register_webhook` (`:54`) — and **has no implementers** (`grep -rn "WhatsAppBridge" src/ \| grep -v WhatsAppBridgeError` → definition only). It is a dead Protocol shaped exactly like the Cloud API, which is not a coincidence: it was written for it. Implementing it is the migration, and the `GatewayAdapter` seam means responders never learn which transport won |
| **D11** | **Tenancy resolves at the transport edge, before any handler runs** | Sender → link → membership → account → scoped session, established once at ingress. Resolving deeper means every one of `dispatch_items`' 14 category branches must remember to scope, which is the shape SPEC-003 N4 warns about. One resolution point, one place to get it right |
| **D12** | **An unlinked sender is refused, not defaulted** | The whole stake (§ preamble). Today `property_slug` comes from a config map (`cli/telegram.py`'s chat-link map) — a *deployment* default, which is correct for one tenant and a cross-account write for many. Fail closed: an unlinked sender gets a linking prompt, never an account |
| **D13** | **`property_slug` scoping and `account_id` tenancy are different axes and both survive** | An account may hold several properties, and the existing chat→property routing is how a staff group maps to a house. Collapsing them would either break multi-property estates or leak across accounts. Tenancy is added *above* property scoping, not instead of it |
| **D14** | **The webhook is the transport; the poller stays as a fallback, deleted only when the webhook is proven** | `dedup.py:145`'s `poll_lease` exists precisely because two pollers must not run concurrently; a webhook plus a live poller is that same double-delivery hazard wearing a new hat. Both paths converge on `dispatch_items`, and `ProcessedIdStore` (`dedup.py:32`) already makes redelivery idempotent — so the risk is manageable, but only if the cutover is explicit rather than accidental |
| **D15** | **The watchdog shrinks rather than disappearing** | On `be8d398` it supervises **both** gateways (23 WhatsApp references, `_start_whatsapp_monitor` at `:160`, `_whatsapp_bridge_running` at `:149`). Under webhooks there is no poller to restart — but the Baileys *bridge* also goes away with D9, so what remains is health checking, not process supervision |

### 1.3 `OPEN — needs decision: founder`

| # | Question | Why it cannot be defaulted | What it blocks |
|---|---|---|---|
| **O1** | **Which Cloud API tier, and does group messaging survive it?** | This is `PRD_REVIEW` G5's live half. The live product routes an inventory *group* (`whatsapp.inventory_group_jid`) and the CLI has `groups`/`link-group`/`send-group`. If the chosen tier has no group support, the migration is a **loss of function**, not a transport swap — and `WHATSAPP:660` re-asks this same question in its own §17, so the PRD never knew either. Engineering cannot pick: it is a cost/capability tradeoff against how the estate actually uses group chat | **The WhatsApp half of Step 8 only.** The Protocol (D10), the adapter, and the webhook are tier-independent. What changes is whether group JIDs survive or every recipient becomes an individual number |
| **O2** | **Where does the webhook terminate for a local/single-tenant install?** | A webhook needs a publicly reachable HTTPS endpoint. The hosted app has one; a founder running locally does not, which is why polling was chosen originally. Options — tunnel, keep polling locally, or hosted-only — differ in operational burden, and the choice determines whether D14's fallback is temporary or permanent | **The cutover half of Step 6.** The route, verification and handler are identical either way |

Everything else this phase depends on is settled.

### 1.4 How earlier specs' forward-flagged items resolve

| Ref | Its statement | Resolution here |
|---|---|---|
| SPEC-003 §7, `4+` | "Chat-gateway tenant-awareness — `telegram_links` is per-account from birth (§4.2), so **Phase 4 adds routing, not a migration**" | **Confirmed and built — Steps 3–5.** The prediction holds exactly: `TelegramLink` already carries `account_id` and the composite unique constraint, so this phase writes resolution logic and no DDL |
| SPEC-003 §10 | "**The bot's transport.** Still polls with a token in per-account config and runs as a supervised CLI process, not the authenticated webhook `TELEGRAM_PRD` §5 describes. Phase 4+" | **Built — Steps 6–7.** Both halves: the webhook replaces polling, and D7 supplies the authentication |
| SPEC-003 N7 | "Do not treat Step 16 as chat-gateway tenant-awareness … multi-tenant chat linking, webhook transport, and per-account bot routing remain Phase 4+" | **This spec is that work.** SPEC-003 scoped *answers* within one account; this scopes *accounts* |
| `TWILIO_PRD` §2.3 | "before Twilio adds a third responder, extract the channel-agnostic core into a shared module" | **Already satisfied** (§0.3) — `review_common.py` with `GatewayAdapter`. Twilio is SPEC-007 and inherits a finished seam |
| `PRD_REVIEW` B3 | The shared responder-core refactor has no owning phase | **Closed as obsolete.** It was done on main in `c4954a0` without a spec claiming it. §2's B8 records that so the review's open item stops being open |

### 1.5 Survey findings that shaped this spec

Eleven findings, verified against `origin/main` @ `be8d398` on 2026-08-05. Negatives stated as
negatives, per `README.md:154`. **Several contradict `PRD_REVIEW` §G — because §G was accurate
about `telegram-bot` and main has moved** (§0.1).

| # | Finding | Consequence |
|---|---|---|
| **F1** | **Zero tenancy anywhere in `src/`.** `git grep -l account_id be8d398 -- "src/*.py"` → **zero matches**, not merely in gateways but in the entire package | Confirms §0.3's caveat: `TelegramLink`, `can()` and the scoped session are all *specs*. Every signature in §5 is written against SPEC-002/003's design |
| **F2** | **Zero webhooks.** `git grep -c setWebhook be8d398 -- src/` → **zero**. Telegram uses **short** polling, not long polling: `get_updates(offset=offset, timeout=0, limit=50)` at `cli/telegram.py:323`, paced by `time.sleep(interval)` at `:390` with a 15-second default (`:271`) | Step 6 is genuinely new surface. Note `timeout=0` — any PRD claiming "long polling" is wrong about the mechanism as well as the direction |
| **F3** | **`whatsapp/protocol.py` is a dead Protocol shaped like the Cloud API.** Four methods declared, **no implementers** | D10. The migration has a pre-built seam. This is the single cheapest thing in the phase |
| **F4** | **The shared core exists and is substantial** — `review_common.py` 1,175 lines; `GatewayAdapter:48`; `analyze_messages:507`; `dispatch_items:723`; `is_trusted_sender:683`; plus `dedup.py` and `pid.py` | §0.3. Both PRDs are wrong about the single most load-bearing fact in their own subject area |
| **F5** | **The category asymmetry is already resolved.** Per-gateway `review.py` files are now **16 lines each** — thin re-exports. Their own docstring says: *"this WhatsApp schema had lost 8 categories the dispatcher still handled; both now use the single superset implementation"* | D6. `PRD_REVIEW` **G2** (Telegram 15 / WhatsApp 8 / three wrong numbers) is **stale**: the drift it found was real and has been fixed. §2's B5 corrects the PRDs to describe the superset, not the old split |
| **F6** | **The watchdog supervises both gateways on main** — 23 WhatsApp references, `_whatsapp_autostart_enabled:138`, `_whatsapp_bridge_running:149`, `_start_whatsapp_monitor:160`; its docstring says "both gateways can run side by side" | `PRD_REVIEW` **G6**'s claim that `WHATSAPP:71` is false (`grep -ci whatsapp scripts/watchdog.py` = 0) was true on `telegram-bot` and is **now wrong on main**. The PRD's original claim turns out to be *correct* on the current ref. D15 |
| **F7** | **Redelivery is already idempotent.** `dedup.py` ships `ProcessedIdStore:32`, `PoisonGuard:77` and `poll_lease:145` — the last existing specifically to stop two pollers running at once | D14. Webhook redelivery — which providers do aggressively — lands on machinery built for it. The concurrency hazard is *between* transports, not within one |
| **F8** | **Sender identity resolution already exists, but resolves to staff, not to an account.** `is_trusted_sender:683` matches an allowlist or a known staff member by phone (WhatsApp) or sender id (Telegram); "an empty allowlist does NOT trust everyone" | D11/D12. Tenancy extends this function's shape rather than introducing a new concept — and its fail-closed default is the precedent D12 follows |
| **F9** | **`notify_staff` is WhatsApp-only with no fallback** (`staff_pto.py:208-229`), while its sibling `notify_approver` was fixed under "H35" to try WhatsApp then fall back to Telegram (`:165-205`) | A real, narrow bug: on a Telegram-only install a staff member is never told their PTO was approved. Step 9. **Not** the broader breakage first suspected — `notify_approver` is already correct |
| **F10** | **Gateway responders are excluded from coverage.** `pyproject.toml`'s `omit` still lists `*/services/gateways/whatsapp/*` and `*/services/gateways/telegram/*`. `review_common.py` sits **above** those globs | The new core *is* measured; the per-channel adapters are not. Since D2 makes adapters the extension point for every future channel, the untested surface is exactly the surface that grows. §10 |
| **F11** | **Six gateway test files exist on main** — `test_gateway_review_common.py`, `test_gateway_safety.py`, `test_gateway_property_resolution.py`, `test_gateway_stop.py`, `test_telegram_client.py`, `test_whatsapp_drain.py` | The refactor was not done blind, and §9 extends these rather than starting over. Contradicts the "zero gateway tests" reading that `telegram-bot` supports |

---

## 2. Doc-fix prerequisites

`PRD_REVIEW` §G's repairs, **landed here** rather than catalogued again. §G supplies the correct
value for most; three needed re-derivation because main moved.

**These edits are applied in the same commit as this spec.** SPEC-005 §2.2 records that *no*
doc-fix decided by SPEC-001–005 was ever applied — that pattern stops here, since a repair deferred
is a repair that does not happen.

### 2.1 Repairs to the two gateway PRDs

| # | Doc + location | Fix |
|---|---|---|
| **B1** | `WHATSAPP:44`'s "Divergence from Telegram" list — five claims, **all false** (§G's G1) | Delete the list. Each feature exists: PTO approval `_handle_approval_message`, APPROVE/DENY regex, inventory routing via `whatsapp.inventory_group_jid`, photo→Document via `create_document`, expert assessment via `_issue_expert_reply`. **It is the premise for §2 gap #6, three §3 P1 rows and §8.2** — all four downstream sections go with it |
| **B2** | `OMNI:11` — "both responders **529** lines", the parity claim framing the whole divergence argument | **Re-derived, not §G's number.** §G said 528/781 (`telegram-bot`); on `be8d398` it is **271/285**. Replace with the current figures and the note that both now delegate to `review_common.py` |
| **B3** | `OMNI:50,68,584` say `core/`; `WHATSAPP:361,369,400` say `shared/`; `TWILIO:77` says `gateways/core/responder.py` | **All three are wrong.** The module exists as **`gateways/review_common.py`** with helpers in `dedup.py`/`pid.py`. §G said "pick `core/`" — superseded by the real path. Correct all three docs to the shipped name |
| **B4** | `OMNI:580-589` invents a Phase 0–4; `OMNI:64` declares "**P0 = launch-blocking**" for six items | Delete both. Chat gateways are **4+ growth bets, not launch-blocking** (`SAAS_PRD:186`). Copy `TELEGRAM_PRD`'s hedge wording, which already says a phase floor "is a dependency floor, not committed scope". `README` declares phases canon (G4) |
| **B5** | `WHATSAPP:88`/`:406` say WhatsApp handles 4 categories; `:34` denies `task_completion` exists; `:406` says Telegram handles 11 while `:34`/`:369` say 15; `OMNICHANNEL:12` says 8 | **Rewrite as one superset.** Per F5 the split no longer exists: 15 categories, one `REVIEW_SCHEMA`, both channels. The old numbers described a drift that has been repaired — describing it as current is now doubly wrong |
| **B6** | `WHATSAPP` §16 Phase 0 — migrate to Developer API with "**no behavior change**" (`:643`) while `:159` says that tier has "no group support", and §17 Q8 (`:660`) re-asks the question | Replace with **D9**: the target is the Cloud API, and the group question is **O1**, openly tagged. A doc that answers, contradicts and re-asks the same question in three places is worse than one that says "open" |
| **B7** | `WHATSAPP:71` — the watchdog "**does** supervise the WhatsApp monitor" | **Leave it. It is correct.** §G marked this false; that was true of `telegram-bot`. On `be8d398` the watchdog supervises both (F6). Recorded so nobody "fixes" a correct line using a stale review |
| **B8** | `OMNI:9,167` cite a "`TelegramBot` Protocol"; `OMNI:35,358` cite `AIOrchestrator.ask()`; `OMNI:112`/`WA:330` say "**reuse** `require_permission`"; `WHATSAPP:361` says extract "`normalize_message()`"; `WHATSAPP:75` lists 7 `whatsapp.*` config keys | Correct each: the class is `TelegramClient` with no Protocol; `orchestrator.py` is module-level functions; `require_permission` is a **spec** (SPEC-003), so "reuse" misleads; `normalize_message` does not exist; four config keys exist, not seven |
| **B9** | `OMNI:173-286` presents async functions as "**extracted from** the common patterns in" both responders | Both responders contain **zero `async def`** and no FastAPI webhook route exists (F2). Say plainly that a sync→async conversion **and** building the webhook surface is new work — this spec's Steps 6–7 — rather than an extraction |
| **B10** | Neither PRD is indexed in `docs/product/README.md` or `SAAS_PRD` §13, though both claim to list the complete set | Index both. `PRD_REVIEW` recommendation 4's second half, never done — the doc set is 12 documents while its own indexes say 10 |

### 2.2 `PRD_REVIEW` items this spec closes or supersedes

| Item | Disposition |
|---|---|
| §G's "do not spec from these yet" | **Lifted** for the repaired sections. §G was right that the docs were unusable as written; §2.1 makes them usable rather than leaving them quarantined forever |
| **B3** (responder-core has no owning phase) | **Obsolete** — shipped in `c4954a0` (§0.3). The review's open item can be closed |
| **G2** (category counts) | **Stale** — the drift was real and is fixed (F5). Superseded by B5 |
| **G6**'s watchdog row | **Reversed** on the current ref (F6). Superseded by B7 |
| **B4**'s A2P 10DLC lead time | **Still unowned.** Not this spec's — Twilio is SPEC-007 — but a regulatory lead time nobody tracks is the kind of item that surfaces late. §10 |

---

## 3. File manifest

### New — tenancy

```
src/mihomes/services/gateways/identity.py        resolve_sender -> ResolvedSender | Unlinked (D11)
src/mihomes/services/gateways/linking.py         /link <code> flow, token issue + redeem
src/mihomes/models/gateway_link_token.py         GatewayLinkToken (TenantOwned, hashed)
```

`TelegramLink` itself is **not** here — SPEC-003 §4.2 ships it. Adding it would mean SPEC-003 was
implemented differently than specified (N9).

### New — webhook transport

```
src/mihomes/web/routes/gateways.py               POST /webhooks/telegram, /webhooks/whatsapp
src/mihomes/services/gateways/webhook.py         signature verification, envelope -> normalized
```

### New — WhatsApp Cloud API

```
src/mihomes/services/gateways/whatsapp/cloud_client.py   implements the EXISTING protocol.py (D10)
```

### Modified

| File | Change |
|---|---|
| `gateways/review_common.py` | `dispatch_items` takes `account`; `is_trusted_sender` resolves within an account (D8, D11) |
| `gateways/telegram/responder.py` | `process_and_respond` takes `account`; passes it through |
| `gateways/whatsapp/responder.py` | Same, plus the adapter points at `cloud_client` |
| `gateways/whatsapp/client.py` | Baileys HTTP client — **removed** once D9's cutover completes |
| `cli/telegram.py` | `monitor` gains a deprecation notice; `link-chat` becomes account-aware |
| `cli/whatsapp.py` | Re-registered in `cli/__init__.py`, minus the Baileys bridge commands |
| `cli/__init__.py` | Re-add the WhatsApp Typer app (currently unregistered — F9's context) |
| `scripts/watchdog.py` | Shrinks to health checks; Baileys supervision removed (D15) |
| `services/staff_pto.py` | `notify_staff` gains `notify_approver`'s fallback ladder (F9) |
| `bridge/` | The whole Baileys Node bridge — **deleted** at cutover |
| `pyproject.toml` | Coverage `omit`: narrow so the new adapters are measured (F10) |

**No migration touches `telegram_links`.** SPEC-003 §4.2 pre-ships `account_id` and the composite
unique constraint. This phase writes resolution logic and no DDL for it — exactly as SPEC-003 §7
predicted. The one new table is the link *token* (§4.1).

---

## 4. Schemas as code

### 4.1 `gateway_link_tokens` — the only new table

```python
# src/mihomes/models/gateway_link_token.py
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from mihomes.db import Base
from mihomes.ids import new_id
from mihomes.models.mixins import TenantOwned


class GatewayLinkToken(Base, TenantOwned):
    """A short-lived code an owner/admin issues so a sender can bind their chat
    identity to a membership (TELEGRAM_PRD's `/link <code>` flow).

    Hashed, never stored raw — same discipline as SPEC-001 N7's confirmation
    tokens and SPEC-003's invite tokens. A link code is a bearer credential that
    grants write access to an estate; a leaked log line must not be usable.

    TenantOwned because the token belongs to the account issuing it, and an
    operator listing one account's outstanding codes must not see another's.
    """

    __tablename__ = "gateway_link_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_gateway_link_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)

    # The membership this code will bind to — chosen at issue time, so redeeming
    # cannot escalate to a different role than the issuer intended (D3).
    membership_id: Mapped[str] = mapped_column(String(36), nullable=False)

    # "telegram" | "whatsapp". A code issued for one channel must not redeem on
    # another: the sender id namespaces are unrelated and a collision would bind
    # the wrong human.
    gateway: Mapped[str] = mapped_column(String(20), nullable=False)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Single-use. Set on redemption; a second attempt is refused rather than
    # rebinding, so a forwarded code cannot hijack an existing link.
    redeemed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    redeemed_by_sender: Mapped[str | None] = mapped_column(String(100), nullable=True)
```

### 4.2 Migration — one table, one policy

```python
# alembic/versions/xxxx_gateway_link_tokens.py
def upgrade() -> None:
    op.create_table(
        "gateway_link_tokens",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("account_id", sa.String(36), nullable=False),
        sa.Column("membership_id", sa.String(36), nullable=False),
        sa.Column("gateway", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("redeemed_by_sender", sa.String(100), nullable=True),
    )
    op.create_unique_constraint(
        "uq_gateway_link_token_hash", "gateway_link_tokens", ["token_hash"])

    op.execute("ALTER TABLE gateway_link_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY gateway_link_tokens_tenant_isolation ON gateway_link_tokens
        USING (account_id = current_setting('app.current_account', true))
    """)

    # NOTE: no telegram_links DDL here. SPEC-003 §4.2 ships that table with
    # account_id and UNIQUE(account_id, telegram_user_id) already. If it is
    # missing at this point, SPEC-003 diverged — stop and reconcile (N9).
```

**Redemption reads this table before an account is known**, so the lookup runs on an unscoped
session in exactly one place (§5.1) — the same carve-out shape SPEC-004 §4.1 used for the webhook
ledger, and for the same reason: identity resolution necessarily precedes tenancy.

---

## 5. Function signatures

### 5.1 Identity — `gateways/identity.py`

```python
@dataclass(frozen=True)
class ResolvedSender:
    account_id: str
    membership_id: str
    role: str            # memberships.role — the RBAC vocabulary, never StaffRole (D3)


class UnlinkedSender(Exception):
    """Raised when a sender has no TelegramLink. NOT an error condition — the
    expected first contact. The caller replies with a linking prompt (D12)."""


def resolve_sender(session: Session, *, gateway: str, sender_id: str) -> ResolvedSender:
    """Sender identity -> account. THE tenancy boundary (D11).

    Looks up TelegramLink by (gateway, sender_id) on an UNSCOPED session — this
    is the one place that is legitimate, because which account to scope to is
    precisely what is being determined. Every caller must then open a scoped
    session with the returned account_id and do nothing else first.

    Raises UnlinkedSender when no link exists. It does NOT fall back to a
    configured default account (D12): a default is correct for one tenant and a
    cross-account write for many, and the failure is silent because the sender
    sees a normal-looking confirmation.

    A sender linked in TWO accounts is legitimate (D5) and resolves by the
    chat/group the message arrived in; a DM from such a sender is ambiguous and
    is refused with a disambiguation prompt rather than guessed.
    """
```

### 5.2 Linking — `gateways/linking.py`

```python
def issue_link_token(session: Session, account: Account, membership_id: str,
                     *, gateway: str, ttl_minutes: int = 15) -> str:
    """Owner/admin only — can(account, "gateway.link.issue"). Returns the RAW
    code once, for display. Only the hash is stored (§4.1)."""


def redeem_link_token(session: Session, *, gateway: str, sender_id: str,
                      raw_token: str) -> ResolvedSender:
    """Bind sender_id to the token's membership. Single-use, expiry-checked.

    Refuses if: expired, already redeemed, wrong gateway, or this sender already
    holds a link in that account (D5's constraint would raise anyway — refuse
    with a clear message rather than surfacing an IntegrityError).
    """
```

### 5.3 The core, made account-aware — `gateways/review_common.py`

**`dispatch_items` keeps its existing signature and gains one keyword.** It already takes
`property_slug` as a keyword-only argument, so this follows the established shape exactly:

```python
def dispatch_items(
    session: Session,
    items: list[dict],
    *,
    account: Account,          # <-- NEW. Required, never defaulted (D11).
    adapter: GatewayAdapter,
    reply_target: str,
    messages: list[dict],
    property_slug: str | None,
    resolve_reporter: Callable[[dict], int | None],
    sender_trusted: bool = True,
) -> dict:
    """Unchanged behaviour, now scoped.

    `account` is required and has no default, deliberately: a default would let
    a future call site silently write to the wrong tenant, and every one of the
    14 category branches below would inherit the mistake. Required-and-explicit
    means a forgetting caller fails at import, not in production (SPEC-003 N2's
    reasoning, applied to accounts instead of property scope).

    `property_slug` is UNCHANGED and orthogonal (D13): an account may hold
    several properties, and the chat->property map still decides which house a
    staff group is talking about.
    """


def is_trusted_sender(session: Session, message: dict, *, gateway: str,
                      account: Account) -> bool:
    """Now resolves the allowlist and staff match WITHIN an account (D8).

    Without `account` the staff-match branch searches every account's staff, so a
    sender known in account B would be trusted in account A. The existing
    fail-closed default ("an empty allowlist does NOT trust everyone") is kept
    exactly as written — it is the precedent D12 follows.
    """
```

### 5.4 Webhook — `gateways/webhook.py` and `web/routes/gateways.py`

```python
def verify_telegram(raw_body: bytes, secret_token_header: str | None) -> None:
    """Telegram sets a caller-chosen secret_token on setWebhook and echoes it in
    X-Telegram-Bot-Api-Secret-Token. Constant-time compare; raise on mismatch."""


def verify_whatsapp(raw_body: bytes, signature_header: str | None) -> None:
    """Cloud API signs with HMAC-SHA256 over the RAW body (X-Hub-Signature-256).

    Raw bytes, verified BEFORE any parse (D7) — SPEC-004 N3 verbatim: a framework
    that hands you a parsed body has already re-serialized it, so the signature
    either fails or, worse, passes after you acted on unverified input.
    """


# web/routes/gateways.py
#   POST /webhooks/telegram, POST /webhooks/whatsapp
#     1. read RAW body; verify (D7)
#     2. normalize the envelope to the message dict shape the responders expect
#     3. resolve_sender -> account, or reply with a linking prompt (D12)
#     4. open a scoped session for that account
#     5. hand to process_and_respond
#   Excluded from session auth and tenant scoping, like SPEC-004's Stripe route:
#   the request arrives before any account is known. Steps 3-5 establish it.
#   Redelivery is safe — ProcessedIdStore (dedup.py:32) already dedups (F7).
```

### 5.5 WhatsApp Cloud API — `whatsapp/cloud_client.py`

```python
class CloudAPIClient:
    """Implements the EXISTING WhatsAppBridge Protocol (whatsapp/protocol.py) —
    send_message, send_template, get_message_status, register_webhook (D10).

    That Protocol has had zero implementers since it was written (F3); its shape
    is the Cloud API's, which is not a coincidence. Structural conformance only,
    no subclassing, per the AIProvider precedent.

    Responders never learn which transport won: they hold a GatewayAdapter (D2),
    whose `send` closes over whichever client is configured.

    Group support is O1. If the chosen tier has none, `send` degrades to
    per-recipient sends and `whatsapp.inventory_group_jid` needs a replacement
    routing key — a behaviour change the migration must state, not absorb.
    """
```

---

## 6. Sequenced steps

Each step ends in a green test or an observable behaviour. Four ordering constraints are
load-bearing: **Step 1 before everything** (the branch reconciliation — without it the tree lacks
the core this spec builds on), **Step 3 before Step 4** (identity resolves before anything is
scoped by it), **Step 5 before Step 6** (tenancy works on the existing transport before the
transport changes underneath it), and **Step 7 before Step 8** (the webhook is proven on Telegram
before WhatsApp's migration depends on it).

**Step 0 — land the §2 doc repairs.** Ten fixes to `OMNICHANNEL_GATEWAY_PRD.md` and
`WHATSAPP_GATEWAY_PRD.md`, plus indexing both. *Verify:* re-grep each stale string and confirm it
is gone (A1). Numbered zero because it is documentation, not code — but it is first, because every
later step reads those docs.

**Step 1 — reconcile `telegram-bot` with `origin/main`.** **A prerequisite, not part of this
spec's design** (§0.1, §10). Nothing below compiles on a tree without `review_common.py`.
*Verify:* `review_common.py`, `dedup.py` and `pid.py` are present on the working branch, and the
six gateway test files pass (A2).

**Step 2 — the link-token table.** §4.1 and §4.2, RLS included. *Verify:* the migration applies
and reverts (A3); a raw token never appears in the table or in logs (A4).

**Step 3 — sender identity.** `resolve_sender`, `UnlinkedSender`, and the unscoped-lookup
carve-out. *Verify:* a linked sender resolves to exactly one account (A5); **an unlinked sender
raises rather than defaulting** (A6); a sender linked in two accounts resolves by chat, and a DM
from them is refused as ambiguous rather than guessed (A7).

**Step 4 — the linking flow.** `/link <code>`, issue and redeem, owner/admin-gated. *Verify:*
expired, replayed, wrong-gateway and cross-account codes are each refused with a distinct message
(A8); redemption is single-use (A9); revoking the membership removes the link with no extra code,
via `ondelete=CASCADE` (A10).

**Step 5 — thread `account` through the core.** `dispatch_items` and `is_trusted_sender` take
`account`; both responders pass it. **Before Step 6** — prove tenancy on the transport that
already works, so a failure here is not confused with a webhook bug. *Verify:* a message from
account A creates rows in A only, and B sees nothing (A11); `is_trusted_sender` does not match
staff from another account (A12); `property_slug` behaviour is unchanged (A13).

**Step 6 — the webhook route.** `POST /webhooks/telegram`, raw-body verification, envelope
normalization, excluded from session auth and tenant scoping. *Verify:* a forged signature is
rejected with no DB write (A14); a valid update reaches `process_and_respond` under the right
account (A15); redelivery of the same update creates nothing twice (A16).

**Step 7 — the polling cutover.** Register the webhook, mark `cli/telegram.py monitor` deprecated,
keep it runnable per D14 and O2. *Verify:* **the webhook and a running poller cannot both process
one update** (A17) — `poll_lease` (`dedup.py:145`) exists because concurrent pollers were already a
hazard; a webhook plus a live poller is that hazard wearing a new hat.

**Step 8 — WhatsApp Cloud API.** `CloudAPIClient` implementing the existing Protocol, its
`GatewayAdapter`, and `POST /webhooks/whatsapp`. **The group question is O1** — build the
tier-independent parts regardless. *Verify:* it satisfies `WhatsAppBridge` structurally with no
subclassing (A18); an inbound Cloud API message produces the same normalized dict Baileys produced
(A19); the responder is unchanged by the swap (A20).

**Step 9 — `notify_staff`'s fallback.** Give it `notify_approver`'s ladder (F9). *Verify:* on a
Telegram-only install a staff member is told their PTO was decided (A21). Small, and it fixes a
live silent failure.

**Step 10 — retire Baileys.** Delete `bridge/`, remove the Baileys client, re-register the
WhatsApp CLI in `cli/__init__.py`, shrink the watchdog to health checks (D15). **Only after Step 8
is green in production**, since this is the irreversible half. *Verify:* no import of the Baileys
client survives (A22); the watchdog supervises nothing that no longer exists (A23).

**Step 11 — close the coverage gap.** Narrow `pyproject.toml`'s `omit` so the adapters are
measured, keeping only genuinely network-bound modules excluded (F10). *Verify:* `identity.py`,
`linking.py`, `webhook.py` and both adapters report coverage (A24).

**Exit criterion check.** With Steps 0–11 green: a message from a linked sender in account A
creates a row in A, is invisible to B, arrives by webhook with no poller running, and is delivered
through the Cloud API. That is A25.

---

## 7. Non-goals and deferred scope

### Do NOT do these

**N1 — Do not re-extract the shared responder core.** It exists (`review_common.py`, 1,175 lines,
F4). Both source PRDs describe it as future work and both are wrong. Re-extracting would fork the
dispatcher, and the fork would drift exactly as the category schemas drifted before `c4954a0`
unified them (F5).

**N2 — Do not default an unlinked sender to any account.** D12. A configured default is correct
for one tenant and a cross-account write for many, and it fails *silently* — the sender gets a
normal confirmation while the row lands somewhere else. Refuse and prompt to link.

**N3 — Do not resolve tenancy inside handlers.** D11: resolve once at ingress. `dispatch_items`
has 14 category branches; scoping in each means fourteen chances to forget, and the one that
forgets is a leak nobody sees.

**N4 — Do not parse a webhook body before verifying its signature.** SPEC-004 N3 verbatim,
different vendor. Raw bytes first.

**N5 — Do not collapse `property_slug` into `account_id`.** D13 — different axes. Collapsing
breaks multi-property estates or leaks across accounts, depending which way it is done.

**N6 — Do not run the webhook and the poller against one bot simultaneously.** Step 7. Telegram
refuses `getUpdates` while a webhook is registered, but the WhatsApp path has no such interlock,
and `poll_lease` exists because this class of hazard already bit once (F7).

**N7 — Do not key gateway links on `Staff`.** SPEC-003 **D19/N6**: `memberships.role` and
`StaffRole` are different vocabularies and `StaffRole` contains its own `OWNER`. Crossing them
makes a housekeeping "owner" an account owner.

**N8 — Do not store a raw link token.** §4.1 — hash only. A link code grants write access to an
estate; SPEC-001 N7 and SPEC-003's invite tokens set the precedent.

**N9 — Do not add a `telegram_links` migration.** SPEC-003 §4.2 pre-ships it with `account_id` and
the composite unique constraint. Needing one here means SPEC-003 diverged — stop and reconcile
(§0.1), as SPEC-004 N13 and SPEC-005 N12 both require.

**N10 — Do not delete the Baileys bridge before the Cloud API is proven in production.** Step 10
after Step 8. `bridge/` is the only working WhatsApp transport today; deleting it early makes
rollback impossible while O1 is still open.

**N11 — Do not treat this as GA scope.** D1, §0.2. Chat gateways are a 4+ growth bet
(`SAAS_PRD:186`); nothing in SPEC-005's GA gates waits on this spec.

**N12 — Do not invent a Phase 5.** §0.2. `SAAS_PRD` §10 ends at Phase 4 and `README` declares
phases canon. This is the defect G4 catches `OMNICHANNEL:580-589` making.

**N13 — Do not describe the category counts as 15-vs-8.** F5 — the split was drift and is fixed.
One superset schema, both channels (§2's B5).

### `DEFERRED (Phase N)` — leave room, do not build

| Item | Phase | Interface room to leave |
|---|---|---|
| Twilio gateway (SMS/MMS/Voice, official WhatsApp) | SPEC-007 | `GatewayAdapter` (D2) is the whole seam — a Twilio adapter supplies `label` + `send` and reuses `dispatch_items` unchanged. `TWILIO:77`'s prerequisite is already satisfied |
| A2P 10DLC registration | SPEC-007 | `PRD_REVIEW` B4's lead-time item, still unowned (§2.2). Regulatory latency, so it wants starting before the code |
| Vendor Discovery | SPEC-008 | The one growth-bet PRD whose code claims `PRD_REVIEW` verified as accurate. Needs counsel sign-off before D0 (`VENDOR:295`) |
| Voice transcription, location sharing, multi-language | 4+ | Named in the gateway PRDs' own roadmaps. Each is a `dispatch_items` category or a normalizer change, not new architecture |
| Per-account bot tokens | 4+ | One bot serves all accounts today; the link table already distinguishes senders. Per-account tokens are a deployment question, not a schema one |
| Inline keyboards / rich replies | 4+ | `GatewayAdapter.send` takes plain text. Richer replies mean widening that seam — additive, same shape as SPEC-005 D11's `headers` |
| Audit-log retention | 5+ | SPEC-005 §7 unchanged |

---

## 8. Acceptance criteria

| # | Criterion | Test |
|---|---|---|
| A1 | Every §2 stale string is gone from both gateway PRDs, and both are indexed | `test_docs_gateway_prds.py::test_repairs_landed` |
| A2 | The shared core and its six test files are present and green on the build branch | `test_gateway_review_common.py::test_superset_schema` (existing file) |
| A3 | The link-token migration applies and reverts cleanly | `test_migration_gateway_links.py::test_up_down` |
| A4 | A raw link token never reaches the database or a log record | `test_linking.py::test_token_hashed_only` |
| A5 | A linked sender resolves to exactly one account | `test_identity.py::test_resolves_single_account` |
| A6 | **An unlinked sender raises `UnlinkedSender` and is never defaulted to an account** | `test_identity.py::test_unlinked_fails_closed` |
| A7 | A sender linked in two accounts resolves by chat; a DM is refused as ambiguous | `test_identity.py::test_multi_account_sender` |
| A8 | Expired, replayed, wrong-gateway and cross-account codes each fail distinctly | `test_linking.py::test_refusal_matrix` |
| A9 | A link code is single-use | `test_linking.py::test_single_use` |
| A10 | Revoking a membership removes its gateway link | `test_linking.py::test_cascade_revocation` |
| A11 | **A message from account A creates rows in A only; B sees nothing** | `test_gateway_tenancy.py::test_cross_account_isolation` |
| A12 | `is_trusted_sender` never matches staff from another account | `test_gateway_tenancy.py::test_trust_is_account_scoped` |
| A13 | `property_slug` routing is unchanged by tenancy | `test_gateway_property_resolution.py::test_unchanged_under_tenancy` (existing file, extended) |
| A14 | A forged webhook signature is rejected with no DB write | `test_gateway_webhook.py::test_bad_signature_no_write` |
| A15 | A valid update reaches the responder under the correct account | `test_gateway_webhook.py::test_routes_to_account` |
| A16 | Webhook redelivery of one update creates nothing twice | `test_gateway_webhook.py::test_redelivery_idempotent` |
| A17 | **A webhook and a running poller cannot both process one update** | `test_gateway_webhook.py::test_no_double_transport` |
| A18 | `CloudAPIClient` satisfies `WhatsAppBridge` structurally, without subclassing | `test_whatsapp_cloud.py::test_protocol_conformance` |
| A19 | A Cloud API inbound message normalizes to the same dict shape as Baileys | `test_whatsapp_cloud.py::test_envelope_parity` |
| A20 | The responder is unchanged by the transport swap | `test_whatsapp_cloud.py::test_responder_untouched` |
| A21 | On a Telegram-only install, staff are told their PTO was decided | `test_staff_pto.py::test_notify_staff_fallback` |
| A22 | No import of the Baileys client survives the cutover | `test_gateway_cleanup.py::test_no_baileys_imports` |
| A23 | The watchdog supervises nothing that no longer exists | `test_gateway_cleanup.py::test_watchdog_scope` |
| A24 | Every new gateway module reports coverage | `test_gateway_cleanup.py::test_coverage_not_omitted` |
| A25 | **End to end: linked sender in A → webhook → Cloud API → row in A, nothing in B** | `test_gateway_e2e.py::test_exit_criterion` |

**A11 is the phase's definition of done.**

> **A gateway without tenancy does not fail closed — it fails into the wrong account.** Every
> other criterion here can pass while A11 fails, and the symptom is a row appearing in a stranger's
> estate with a cheerful confirmation sent back to the person who caused it. Nobody is watching a
> screen when it happens.

This is the gateway analogue of SPEC-002's isolation test and SPEC-005's export criterion, and it
is written as an **enumeration**: A11 must walk `dispatch_items`' category branches from the tree
and assert each one writes only within the resolved account. A hand-listed subset passes forever
while the fourteenth branch leaks — the same reasoning as SPEC-004 A11 and SPEC-005 A15.

**A25 is the exit criterion.** If A25 is red the phase has not shipped, regardless of what else is
green.

---

## 9. Test manifest

```
tests/unit/test_identity.py                 resolve_sender, fail-closed, multi-account senders
tests/unit/test_linking.py                  token hashing, refusal matrix, single-use, cascade
tests/unit/test_docs_gateway_prds.py        §2 repairs landed (A1) — reads the repo, not the code
tests/unit/test_gateway_cleanup.py          no Baileys imports, watchdog scope, coverage config
tests/unit/test_whatsapp_cloud.py           Protocol conformance, envelope parity
tests/integration/test_gateway_tenancy.py   THE enforcement test (A11) — enumerates branches
tests/integration/test_gateway_webhook.py   signature, routing, redelivery, transport exclusivity
tests/integration/test_migration_gateway_links.py  up/down — own engine, real Alembic
tests/integration/test_gateway_e2e.py       THE exit criterion (A25)
```

Plus two **existing** files extended rather than replaced:

```
tests/unit/test_staff_pto.py                + notify_staff fallback ladder (A21)
tests/unit/test_gateway_property_resolution.py  + unchanged under tenancy (A13)
tests/integration/test_gateway_review_common.py + superset schema assertion (A2)
tests/integration/test_gateway_safety.py    + is_trusted_sender is account-scoped (A12)
```

**Extend, do not replace.** Six gateway test files already exist on `be8d398` (F11); the four
above are extended, and `test_gateway_stop.py` / `test_telegram_client.py` / `test_whatsapp_drain.py`
are touched only if Step 10's cleanup breaks them. `test_gateway_safety.py` already covers
`is_trusted_sender`'s pre-tenancy behaviour, so A12 extends that file rather than opening a new
one — the two trust dimensions belong side by side.

**Fixtures.** Extend SPEC-002's `account_a` / `account_b` with:

- **`linked_sender_a`** — a `TelegramLink` binding a sender id to a membership in `account_a`.
  A11 is meaningless without a *second* populated account, so `account_b` gets one too.
- **`FakeGatewayAdapter`** — records `(chat_id, text)` per call, satisfying `GatewayAdapter`
  structurally. Every dispatch test asserts on it rather than on a live client.
- **Captured webhook envelopes** — real Telegram and Cloud API payloads stored as **raw bytes**, so
  signature verification is exercised on the real thing (A14), following SPEC-004 §9's Stripe
  fixture precedent.

**Coverage.** Step 11 narrows `pyproject.toml`'s `omit`. Keep excluded only what genuinely needs a
network — the Baileys client is gone, so what remains is `cloud_client.py`'s HTTP surface, on the
same reasoning that omits `stripe_provider.py` and the AI providers. `identity.py`, `linking.py`,
`webhook.py` and both adapters are pure logic and must be measured (A24).

**The adversarial pattern for A11.** Not "does a scoped write stay scoped" — that passes
trivially. Instead: enumerate `dispatch_items`' category branches **by walking the tree**, drive
one message through each under `account_a`, and assert `account_b`'s row counts are unchanged
across every table. The test must fail when someone adds a fifteenth category without scoping it.

---

## 10. What this phase does not make safe

- **The branch divergence, which no spec can fix.** `telegram-bot` is 13 behind / 30 ahead of
  `origin/main` (§0.1). Main holds the gateway core and its tests; `telegram-bot` holds 30 commits
  of other work. **Nobody owns reconciling them**, and until someone does, "what does the gateway
  code do" has two different answers. Step 1 makes it a prerequisite; it does not make it someone's
  job.
- **The spec set itself is unmerged.** SPEC-001–006 live on `worktree-prd-review-v2`, which
  descends from `telegram-bot`. The merge target has never been decided.
- **Phases 0–4 are still unbuilt.** This spec sits on five unbuilt phases — one more than SPEC-005.
  Every reference to `can()`, `account_id`, memberships or `TelegramLink` is a *spec*, and
  divergence compounds further here than anywhere else in the set.
- **The Cloud API's group behaviour is unknown until O1.** If the chosen tier drops group support,
  the migration is a loss of function for an estate that routes an inventory group today. The
  spec's structure survives; the product experience may not.
- **Per-channel adapter code stays thin but untested until Step 11.** And `cloud_client.py` stays
  omitted after it, on the same network-bound reasoning as every other HTTP client in the tree —
  so the Cloud API's own error handling is exercised by nothing in CI.
- **One bot token serves every account.** Per-account tokens are deferred (§7). A compromised token
  is therefore a cross-account incident, not a single-tenant one — the blast radius grew when
  tenancy arrived, and nothing here shrinks it.
- **Sender identity is only as strong as the linking flow.** A link code is a bearer credential
  that grants write access to an estate. It is hashed, single-use and short-lived (§4.1), but a
  code forwarded to the wrong person before redemption binds that person. There is no second
  factor.
- **The nine items SPEC-005 §10 shipped GA with are unchanged.** Secrets at rest (SPEC-003's O1),
  revenue correctness, the Stripe dashboard's own configuration, deliverability, single-provider
  email, and the rest. This spec adds gateways to that list rather than subtracting anything from
  it.
- **A2P 10DLC remains unowned** (§2.2). Not needed here — Twilio is SPEC-007 — but it is the one
  item in the growth-bet backlog with a regulatory clock, and clocks run whether or not a document
  claims them.
