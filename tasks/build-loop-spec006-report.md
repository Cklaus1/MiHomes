# SPEC-006 Build Loop — end-of-run report

> **Status: COMPLETE**, with G9 blocked by U5 and shipped as ratchets.
> **Branch:** `worktree-spec-build-harness`. **Suite:** 2538 passed, 0 failed, 3 skipped
> (unchanged from baseline), 2 xfailed. **Reconciler:** `--collect` exits 0 at **25/25**.
> Baseline at start: 2405 passed (SPEC-005 complete, `a03f712`).

---

## 0. The correction this run opened with

The session inherited a claim that G0–G5 were done and G6 was next. **All three checks
disagreed:**

- `spec006_reconcile.py --collect` reported **1/25** node ids resolving — only A2.
- Nothing imported the three modules from commit `62f1cb2`; `grep` across `src/` and `web/`
  returned zero references.
- Those modules are a **different feature**: an *outbound* async delivery queue at
  `services/webhook.py`, where §5.4 specifies *inbound* receipt with raw-body verification at
  `gateways/webhook.py`.

That commit is labelled `spec006(G3,G4,G5)` and satisfies no §8 criterion. It was left in
place — committed work with 52 passing tests is not this run's to delete — and the build
started at G1. **One thing to watch:** G10's `omit` list names `gateways/webhook.py` by path,
so it cannot resolve against the stray `services/webhook.py`.

---

## 1. What shipped

| Group | Step | Criteria | Commit |
|---|---|---|---|
| G1 | 1 — the link-token table | A3, A4 | `f9d32f1` |
| G2 | 2 — sender identity | A5, A6, A7 | `fb3da73` |
| G3 | 3 — the linking flow | A8, A9, A10 | `b00c750` |
| G4 | 4 — `account` through the core | A11, A12, A13 | `e0f275a` |
| G5 | 5 — the webhook route | A14, A15, A16 | `3e2bfac` |
| G6 | 6 — the polling cutover | A17 | `e62c7e3` |
| G7 | 7 — WhatsApp Cloud API | A18, A19, A20 | `bc2a9e0` |
| G8 | 8 — `notify_staff`'s fallback | A21 | `f5b5f6d` |
| G9 | 9 — retire Baileys | A22, A23 — **blocked, ratcheted** | `5a163ba` |
| G10 | 10 — close the coverage gap | A24 | `5a163ba` |
| G0.2 + G11 | §2 regression gate + exit | A1, A25 | this commit |

---

## 2. Defects found by building — five, in the code the spec said was already right

**These are the run's most useful output.** Each was found because a criterion was written to
be hard to satisfy vacuously, and four of the five were in work this same run had just
committed.

### 2.1 The M22 defect, reintroduced by G5 and caught by G6

G5's route keyed its dedup store on `gateway.telegram.processed`; the CLI monitor used
`telegram.processed_ids`. **Two disjoint stores** — an update handled by one transport
invisible to the other. That is verbatim what `dedup.py`'s docstring says the module exists to
prevent (*"each gateway had FOUR disjoint processed-id stores … messages were double-processed
into duplicate issues/tasks"*).

Fixed by importing `PROCESSED_IDS_KEY`/`MAX_PROCESSED_IDS` in both. The monitor's local
`cap=2000` also went: two caps on one key means the shorter evicts ids the longer still
considers handled. `test_both_transports_share_one_store` reads both modules' source, because
the behavioural test alone cannot catch it — a webhook that silently failed to write also
"does not double-process".

### 2.2 The webhook dispatched nothing at all, and three criteria were green anyway

**The sharpest finding.** G5's envelope omitted `propertySlug`. `responder.py:208` filters
`[m for m in messages if m.get("propertySlug") or property_slug]`, and the route passed
neither — so **every** delivery returned `{"logged": 0, "errors": ["No linked chat found"]}`.

A14, A15 and A16 all passed, because `WATCHED_TABLES` included `audit_log` and *sender
resolution* writes audit rows. `sum(counts) > 0` was satisfied before dispatch ever ran.

Found while writing A19, whose *"the same dict, not a compatible subset"* claim is the same
defect stated from the other side. Fixed three ways: the full eleven-key envelope; the route
resolving the chat→property map inside the scoped session; and `audit_log` out of the counted
tables (it stays in the *cleanup* list — a different list for a different reason).

### 2.3 A failing AI call rolls back every successful prior write in the batch

`review_common.py:372`'s `ai_response` catches any exception and calls `session.rollback()`.
H27's docstring says this prevents a half-applied transaction poisoning later creates; the
measured effect is that it also discards the **completed** ones. In the A11 enumeration it took
eleven prior categories' rows with it while `dispatch_items` still reported `logged: 1` for
each.

In production this fires whenever the AI provider is down or unconfigured: a batch containing
one question silently loses the issues logged before it. **Recorded in `opportunities.md`, not
fixed** — it is a transaction-semantics bug, not a tenancy one, and widening G4 to cover it
would have grown a security group into a refactor. Proposed fix: `session.begin_nested()` per
item.

### 2.4 Two doc repairs P1 recorded as landed had not

A1 is a regression gate, so it was run rather than trusted. It found `OMNICHANNEL:55` still
naming `core/responder.py` — a path that has never existed — and `WHATSAPP:32` still saying
`REVIEW_SCHEMA` has 8 categories, the drift F5 measured as repaired. B3 landed in two of its
three places and B5 in most of its. Both fixed in G0.2.

### 2.5 Four test-fixture hazards, each with a misleading symptom

| Symptom | Actual cause |
|---|---|
| 3 tenancy tests: `DID NOT RAISE LookupError` | `session` + `web_client_factory` both enter `account_context`; teardown unwinds reverse-of-setup, so the later token resets last and `reset()` restores its `old_value` — **re-binding** the tenant |
| A17: *"the poller's store does not know the webhook handled this update"* | `cli_database` repoints `DATABASE_URL` session-wide; the route resolves it at call time, so the webhook wrote to the CLI database while the assertion read `TEST_DATABASE_URL`. Same account, wrong database |
| `test_archive.py`: `assert 5 == 2`, later `11 == 2` | Fixtures that `commit()` escape the rollback; cleanup missed `audit_log`, then missed it again after `WATCHED_TABLES` changed |
| `test_global_tables_are_queryable_without_an_account` | `users` is GLOBAL, so an account-scoped cleanup loop cannot reach it |

**On the first of these I nearly shipped a wrong fix.** I diagnosed it as the route leaking
context and wrote a `contextvars.copy_context()` wrapper — while my own probe had already shown
a test returning 401 leaked too, and that test never reaches the code I "fixed". Reverted: a
false explanation committed into production code is worse than the bug it claims to fix.

---

## 3. Deviations from the spec and harness — eleven, each measured

| # | Claim | Measurement | Resolution |
|---|---|---|---|
| **D1** | §4.1: `String(36)` PKs | `memberships.id` is `PGUUID`; a `String(36)` FK does not build | `PGUUID(as_uuid=True)` throughout |
| **D2** | C8: budget an `EXPECTED_NON_LEADING` entry for `token_hash` | A `UniqueConstraint` in `__table_args__` emits a *constraint*, not an index — verified on `TelegramLink`. The entry would be **stale on arrival** and fail `test_every_declared_exception_still_exists` | No entry. The invite precedent needed one only because it declares `unique=True, index=True` |
| **D3** | C8: *"expect `test_u7_enforcement.py` to fire"* | **C8 was right; my first search was wrong** — the file is in `tests/integration/`, not `tests/unit/`. It fired exactly as predicted | `GatewayLinkToken` added to the denied-outright set with its reason |
| **D4** | §4.2's DDL omits the `membership_id` FK | Without it there is no CASCADE and A10 falls to application code | FK created in `0016` |
| **D5** | §5.2: `issue_link_token(..., account: Account)` | Written as `account_id: uuid.UUID` | Recorded, since A11 requires `dispatch_items` take `account: Account` and the two sit side by side |
| **D6** | SPEC-006 never reconciles with SPEC-003's existing `resolve_sender` | Same name, **opposite** unlinked behaviour (D16: staff-level; A6: raise) and opposite data flow (account in vs. account out). Zero grep matches for D16 in the spec | Both kept; new module per §3. The harness records which resolver governs which ingress so G5 need not rediscover it. Measured: `sender_authz` has **no callers at all** |
| **D7** | §5.4 specifies two verifiers | They are **not equally strong**: Telegram signs nothing — a caller-chosen secret token — while the real HMAC-over-raw-body is WhatsApp's | Both built, only Telegram wired. N4 survives on that path as *ordering discipline*, said so in `ALLOWLIST_MECHANISMS` |
| **D8** | F7: redelivery is already idempotent via `ProcessedIdStore` | True, and unusable at the edge: the store opens its own session and reads `Configuration`, so with no account bound both `contains()` and `add()` raise `LookupError` | Dedup runs **after** `resolve_sender`. Consequence stated: an unlinked sender's redeliveries are not deduped |
| **D9** | Step 6: `poll_lease` is A17's mechanism | Wrong primitive — a 90s poller-vs-poller lease that a stateless webhook can only contend for (dropping updates) or ignore. Also: **nothing in production calls it**, only its test | Step 6's own wording — *"cannot both **process**"* — settles it. The shared store is the mechanism |
| **D10** | G7.2 names six envelope keys | The real envelope has **eleven** | A19 asserts the whole set, and reads `TelegramClient.normalize_update`'s source so the constant cannot drift |
| **D11** | A18 as an `isinstance` check | `WhatsAppBridge` is a plain `Protocol`, so `isinstance` **and `issubclass`** both raise. Written the wrong way first | Per-method `inspect.signature`, method list read off the Protocol; non-subclassing asserted on `__mro__` |

Also: the harness predicted a *"third `PERMANENT_ALLOWLIST` entry"* — it is the **fourth**. Not
range-checked (C7); the gates name the count themselves.

---

## 4. G-Final — compound-stop verification

| | Condition | Result |
|---|---|---|
| **F.1** | full suite green (condition C) | ✅ **2538 passed**, 0 failed, 3 skipped (unchanged), 2 xfailed |
| **F.2** | every §8 criterion green by its own named node id (condition E) | ✅ **all 25**, run in two batches by node id |
| **F.3a** | every §6 step tasked (condition B) | ✅ 10 steps + 2 prerequisites |
| **F.3b** | `spec006_reconcile.py --collect` exits 0 (condition B) | ✅ **25/25**, `PENDING_TESTS_IN_EXISTING_FILES` **empty** |
| **F.4** | smoke green (condition D) | ✅ 18 passed |
| **F.5** | this report | ✅ |
| **A** | every checkbox `[x]` or `[!]` | ✅ G9 is `[!]` — see below |

**`PENDING_TESTS_IN_EXISTING_FILES` ended empty**, which matters: every entry — the three
inherited plus four added during the run (G3.1–3, G6.1) — expired with its group.
`TestPendingSetExpires` enforces that, and the set's recurrence is now documented: §8 groups
criteria by **file**, not by group, so landing a group creates the file a later group writes
into.

---

## 5. What is NOT done — and must not be read as done

### G9 is blocked, deliberately (U5)

N10 is a halt instruction: do not delete the Baileys bridge before the Cloud API is proven **in
production**. There is no production (U4: no Meta account), and O1 — whether the tier even
supports the inventory *group* the live product routes through — is unanswered (U2). Deleting
the only working WhatsApp transport under those conditions removes the rollback path for a
migration that has not started.

**A22 and A23 therefore ship as ratchets, not as claims.** Each measures the current footprint
(5 Baileys importers, 23 watchdog references) and fails in *both* directions — a new importer
is the likely regression while the migration is open; a removed one means the cutover has begun
and must update the expectation rather than quietly leaving a stale one.

### Unmet launch gates

| # | State after this run |
|---|---|
| **U1** | `telegram-bot` ↔ `origin/main` still unreconciled. The consumed modules were verified present *here*, which is not the same thing |
| **U2** | O1 open. Every tier-independent part built; `supports_groups` defaults **False**, and `send_group_message` raises `GroupsNotSupported` naming `whatsapp.inventory_group_jid` rather than degrading silently |
| **U3** | O2 open. Route, verification and handler ship; A14–A17 prove them. **`setWebhook` is called by nothing** — registration is a deploy-time action nobody owns, and `TELEGRAM_WEBHOOK_SECRET` must be set wherever it happens. `monitor` now prints a deprecation notice naming this gate |
| **U4** | No Meta account. Steps 7/9's *live* behaviour unprovable here; `FakeCloudClient` covers the rest |
| **U5** | No production, so N10's precondition cannot be met. See G9 above |
| **U6** | One bot token serves every account. A compromised token is a cross-account incident |
| **U7** | A link code is a bearer credential with no second factor |
| **U8** | `cloud_client.py` stays coverage-omitted, with its reason asserted by `STAY_OMITTED` |
| **U9** | Everything SPEC-005 §10 shipped GA with, unchanged. **This spec adds gateways to that list rather than subtracting from it** |

### Carried to `opportunities.md`

The AI-rollback defect (§2.3), with a proposed SAVEPOINT-per-item fix.

---

## 6. For the next session

1. **The exit criterion is green, but §6's exit sentence has a half this run could not prove.**
   *"Delivered through the Cloud API"* is asserted through the adapter seam (A20's
   `FakeCloudClient`), not a live call. U4 is why, and saying so is more honest than a test that
   mocks a network and calls the result proof.
2. **`62f1cb2`'s three orphan modules are still on the branch.** They satisfy no criterion and
   are a different feature. Deleting them is a founder call.
3. **The next real step is deployment, not code**: answering O2 (U3) unblocks `setWebhook`, and
   answering O1 (U2) unblocks Step 9's deletion. Both are product decisions.
