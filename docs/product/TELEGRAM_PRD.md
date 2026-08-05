# Telegram Gateway — Product Requirements Document

Purpose: define what the MiHomes Telegram gateway does today, where it falls short of a real product, and how it becomes a secure, multi-tenant, chat-first interface to the MiHomes estate-management system.

Status: Draft — 2026-07-27

Related docs:
- `../architecture/MULTITENANCY.md` — tenant isolation, `account_id` rule, Postgres migration.
- `ONBOARDING_AUTH_RBAC.md` — identity model, roles, capability matrix, linking.
- `PRICING_AND_PACKAGING.md` — Free/Pro/Estate plan limits.

---

## 1. Current State (grounded in the code)

The Telegram gateway is a working, single-user, local-first chat interface. It is one of two messaging gateways; WhatsApp is the sibling (`src/mihomes/services/gateways/whatsapp/`, same `client/extractor/responder/review` shape plus a `protocol.py` bridge interface). Both normalize inbound messages into one internal message dict so the AI review/responder pipeline is shared.

### 1.1 Transport — long-polling, no webhook

The client (`src/mihomes/services/gateways/telegram/client.py`) is a dependency-free `urllib` wrapper over the Telegram Bot API. It **polls** via `getUpdates` (`get_updates()`, line 58) — there is **no webhook**. `allowed_updates` is restricted to `["message"]` (line 72), so callback queries, edited messages, and inline events are not requested today. The CLI `telegram monitor` (`src/mihomes/cli/telegram.py:267`) drives a short-poll loop (`timeout=0`) every `--interval` seconds (default 15s), advancing a stored `telegram.last_update_id` offset and de-duplicating on a `telegram.processed_ids` set persisted in the DB config table.

`normalize_update()` (client.py:108) converts a raw Telegram Update into the WhatsApp-compatible dict: `id`, `timestamp`, `jid` (the chat_id occupies the "jid" slot for pipeline compat), `isGroup`, `sender` (Telegram user_id — **no phone number is available**), `senderName`, `senderUsername`, `text`, `hasMedia`, `mediaPath`, `propertySlug`. It skips bot messages and join/leave system events. Media (largest photo, document, or video) is downloaded to `~/.mihomes/media/telegram/` via `download_file()`.

### 1.2 Message understanding — AI review

`review.py` (`analyze_messages()`) sends the formatted conversation plus any image attachments to the configured AI provider with a strict JSON schema (`REVIEW_SCHEMA`) and a long estate-management system prompt. It injects an **estate context block** (`_build_estate_context()`) — open issues, recently resolved issues, tracked assets, and staff for the linked property — so classification is grounded in real estate state. It handles provider per-request image limits by batching images and merging array-based categories (`books`, `assets`). Output is `{items, skipped}`.

### 1.3 Action — the responder

`responder.py` (`process_and_respond()`) is the command/response brain. It maps each classified item to a MiHomes service call and sends a confirmation back into the chat. Notable behaviors:
- **Approval pre-check** (`_handle_approval_message`): a message from `telegram.pto_approver_id` matching `APPROVE <id>` / `DENY <id> [reason]` approves/denies a PTO request before AI analysis runs. This is the only structured-command (non-NLP) path today.
- **Inventory chat routing**: if the reply chat equals `telegram.inventory_chat_id`, messages route straight to `handle_inventory_scan()`, which vision-parses room photos into `Asset` records.
- **Issue photos → Documents**: when an issue is created, attached images are copied into `web/static/uploads/` and linked as `Document` records on the issue.
- **AI replies**: questions are answered by the estate-manager advisor; new issues get a maintenance-expert assessment appended to the confirmation. Replies are constrained to 1–2 plain-text sentences, with `NO_RESPONSE` suppression.

The `extractor.py` (`extract_and_create()`) is a lighter, reply-free variant used for passive auto-creation of issues/tasks (dedupes via `telegram.last_update_id` offset + a `telegram-processed-ids.json` sidecar).

### 1.4 Supported capabilities (today)

Classification categories are defined in `REVIEW_SCHEMA` (review.py:19) and dispatched in `process_and_respond()`:

| Category | Action taken | Backing service | Reply |
|---|---|---|---|
| `issue` | Create issue (+ severity, room, reporter, photo→Document) | `services/issue.create_issue` | "…logged ✓" + maintenance-expert note |
| `issue_resolution` | Resolve matching open issue | `services/issue.resolve_issue` | "…resolved ✓" |
| `task` | Create task (+ assignee) | `services/task.create_task` | "…logged ✓" |
| `task_completion` | Complete matching open task | `services/task.complete_task` | "…marked complete ✓" |
| `work_order_request` | Create work order (+ vendor, est. cost) | `services/work_order.create_work_order` | "Work order … created ✓" |
| `vendor_activity` | Create task with due date | `services/task.create_task` | "…logged ✓" |
| `supply_need` | Update consumable stock | `services/consumable.update_stock` | "…inventory updated ✓" |
| `appointment_request` | Schedule appointment (+ vendor, date, type) | `services/appointment.create_appointment` | "…scheduled for … ✓" |
| `expense_log` | Record transaction (source=telegram) | `services/budget.add_transaction` | "Expense $… logged ✓" |
| `book_addition` | Create Book records (vision reads covers) | `services/book.create_book` | "Added N book(s) ✓" |
| `asset_addition` | Create Asset records (vision) | `services/asset.create_asset` | "Added N asset(s) ✓" |
| `note_addition` | Attach note to an entity | `services/note.add_note` | "Note added ✓" |
| `pto_request` | Create pending PTO + notify approver | `services/staff_pto` | "…Pending approval." |
| `question` | Answer via AI estate manager | `services/ai/orchestrator.ask` | 1–2 sentence answer |
| `informational` | Ignore | — | (none) |
| `APPROVE`/`DENY <id>` | Approve/deny PTO (approver only) | `services/staff_pto` | "…approved/denied ✓" |
| Inventory-chat photos | Room scan → Assets | `services/ai/assessors.parse_room_scan` | "Scanning… / Added N ✓" |

### 1.5 CLI surface (`mihomes telegram …`)

`setup` (BotFather instructions), `status` (bot info + linked chats + local watchdog PID), `discover` (find chat IDs seen by the bot), `link-chat` / `unlink-chat` (map chat_id → property slug), `send`, `monitor` (the polling loop), `review` (interactive AI review queue with `--accept 1,3` / `--auto`), `watchdog` / `autostart` / `stop` (background supervision, incl. Windows Task Scheduler registration).

### 1.6 Supervision — the watchdog

`scripts/watchdog.py` runs detached, checks every 60s, and restarts the Telegram monitor if its PID dies (WhatsApp monitor too, but only when `whatsapp.autostart` is set). It also runs a 15-min Google Calendar sync and, every Monday, sends a **weekly inventory reorder digest** to `telegram.owner_chat_id` via `TelegramClient.send_message`.

### 1.7 Configuration keys (SQLite `configuration` table)

`telegram.bot_token`, `telegram.chat_links` (JSON chat_id→property_slug), `telegram.owner_chat_id`, `telegram.pto_approver_id`, `telegram.inventory_chat_id`, `telegram.last_update_id`, `telegram.processed_ids`, `telegram.autostart`, `telegram.monitor_property`. **All are global** — there is no `account_id` scoping anywhere, consistent with the single-tenant baseline described in `../architecture/MULTITENANCY.md` §1.

---

## 2. Gap Analysis

What is missing or weak for a real, multi-tenant product:

1. **No multi-tenancy.** All config is global; `chat_links` maps a chat to a *property slug*, not to an `(account_id, membership, role)`. One deployment = one estate. There is no way for chat `A` to belong to family X and chat `B` to family Y.
2. **No identity linking.** `sender` is a raw Telegram user_id with no mapping to a MiHomes `users`/`memberships` row. Anyone in a linked group can command the bot; there is no per-user authorization.
3. **Polling, not webhook.** Fine for a laptop; wrong for a hosted multi-tenant service. `getUpdates` cannot fan out to many accounts, wastes a long-lived process per install, and has restart-replay hazards. Production needs a single webhook endpoint.
4. **Weak authorization.** Only the PTO approver path checks sender identity. Every other action is available to any group member. There is no mapping to the RBAC capability matrix (`ONBOARDING_AUTH_RBAC.md` §9.2).
5. **No interactive UX.** No inline keyboards, buttons, or quick replies (`allowed_updates` excludes `callback_query`). Approvals are typed commands (`APPROVE 12`), not tapped buttons.
6. **DM vs group is under-modeled.** `isGroup` is computed but unused for routing/authz. A staff DM and an owner DM are indistinguishable to the responder.
7. **No proactive notifications** beyond the weekly digest — no "task due", "critical issue logged", "low stock", "vendor arriving" pushes.
8. **No voice-note handling.** Voice/audio is not among the detected media types; there is no transcription path.
9. **No `/help`, `/start`, or onboarding in-chat.** `/start` is only mentioned as a way to make the bot visible for `discover`.
10. **No rate limiting or abuse controls.** Unknown chats are effectively ignored only because they aren't in `chat_links`, not by an explicit deny.
11. **No delivery/read status or send-failure retry** (Telegram send errors are swallowed).
12. **No multi-language.** Prompts and replies are English-only.
13. **Responder drift vs WhatsApp.** `telegram/responder.py` and `whatsapp/responder.py` share the same helper/dispatch shape but have **diverged** — Telegram handles many more categories (task_completion, expense_log, work_order, appointment, book/asset/note addition, issue_resolution) than WhatsApp. `TWILIO_PRD.md` §2.3 makes extracting a shared channel-agnostic responder core a prerequisite for a third gateway; new Telegram capabilities should land in that shared core, not deepen the fork.

---

## 3. Proposed New Capabilities (prioritized)

MoSCoW, each tied to an existing MiHomes entity/feature.

| Pri | Capability | Ties to | Notes |
|---|---|---|---|
| **P0 / Must** | Account+identity linking (`/link <code>`) | `memberships`, `accounts` | Map telegram_user_id/chat_id → membership. Foundation for everything below. |
| **P0 / Must** | RBAC-gated actions | capability matrix §9.2 | Every responder action checks `(role, action, home)` before executing. |
| **P0 / Must** | Webhook transport | new FastAPI route | Replace polling for hosted deployments (§5). |
| **P0 / Must** | Ignore unlinked/unauthorized chats explicitly | security §6 | Deny-by-default, not accidental. |
| **P1 / Should** | Inline-keyboard flows (approve/deny work orders, complete tasks, confirm auto-created items) | work_order, task, PTO | Adds `callback_query` to `allowed_updates`; taps replace typed `APPROVE 12`. |
| **P1 / Should** | Photo-based issue logging as a first-class flow | issue + document | Already partially works (photo→Document); make it explicit with confirmation buttons. |
| **P1 / Should** | Proactive notifications (task due, critical issue, low stock, appointment reminder) | task, issue, consumable, appointment, `alert` model | Outbound push to the right linked user, role-scoped. |
| **P1 / Should** | Voice note → transcription → task/issue | task, issue | Download `voice`/`audio`, transcribe, feed into `analyze_messages`. |
| **P1 / Should** | NL queries via AI advisor in DM | `ai/orchestrator.ask` | Already exists for group questions; extend to authenticated DMs with role/home scope. |
| **P1 / Should** | Staff-scoped bot experience | `membership_property_scopes` | A housekeeper's chat only sees/acts on their assigned home. |
| **P2 / Could** | `/help` + in-chat onboarding | — | Command menu via `setMyCommands`; guided first-run. |
| **P2 / Could** | Digest customization (frequency, content, per-user) | consumable digest, budget | Owner/admin configurable; currently hard-coded weekly Monday. |
| **P2 / Could** | Location sharing for property check-ins | property, staff | Staff "on-site" check-in via Telegram location. |
| **P2 / Could** | Multi-language replies | AI prompts | Detect user language; localize confirmations. |
| **P2 / Could** | Delivery/retry + send-failure surfacing | client | Retry with backoff; log undelivered notifications. |

---

## 4. Multi-Tenant Design for Telegram

**Recommendation: one shared MiHomes bot + an explicit linking step**, not a bot per account. Per-account bots would force each owner to create a BotFather bot and paste a token (high onboarding friction) and multiply webhook/secret management. A single `@MiHomesBot` with a linking flow is the right fit — mirroring the "shared table + `account_id`" decision in `../architecture/MULTITENANCY.md` §2.

### 4.1 Linking flow

1. In the web app (signed in via Google, `ONBOARDING_AUTH_RBAC.md` §3), the user requests **"Connect Telegram."** MiHomes issues a short-lived, single-use **link code** bound to `(user_id, account_id, membership_id)` — same discipline as invite tokens (`ONBOARDING_AUTH_RBAC.md` §10): stored **hashed**, single-use, expiring (minutes, not days — the code transits a chat message).
2. The user opens `@MiHomesBot` and sends `/link <code>` (or taps a deep link `https://t.me/MiHomesBot?start=<code>`).
3. The bot resolves the code, verifies it is unexpired/unused, and writes a **`telegram_links`** row: `(telegram_user_id, account_id, membership_id, role, linked_at, revoked_at)`. Do **not** persist `role` denormalized as authoritative — resolve role from the membership on every request so role changes and revocations take effect immediately. Group chats additionally get a `telegram_chat_links` row: `(telegram_chat_id, account_id, home_id)` — the multi-tenant successor to today's global `telegram.chat_links`. Only an **owner/admin** membership may link a group chat to a home (a staff member must not be able to bind a chat the estate didn't sanction).
4. Every subsequent update resolves `telegram_user_id → membership → (account_id, role, home scopes)`. A membership that is `revoked` (or an expired/removed seat) fails resolution — **revoking a membership implicitly revokes the link**. All service calls run **tenant-scoped** (the `account_id` rule) and **role-checked** (§6).

### 4.2 The account-switch problem

A user in two accounts (e.g. an estate manager for two families, per `ONBOARDING_AUTH_RBAC.md` §2) has two `telegram_links` rows for the same `telegram_user_id`. Resolution rules:
- **In a linked group chat**, the account is unambiguous — it comes from `telegram_chat_links.account_id` for that chat. Group membership defines context.
- **In a DM**, when a user has >1 account, the bot must disambiguate. Maintain a per-user **current account** (a `telegram_dm_context` row); expose `/account` to switch via an inline keyboard listing the user's accounts (mirrors the web account switcher, `ONBOARDING_AUTH_RBAC.md` §7). Default to the most recently used.

### 4.3 Staff scoping

Staff links carry `membership_property_scopes`. A staff user's actions and queries are restricted to their assigned home(s); a group chat linked to a home they aren't scoped to yields a 404-style "not found" (never revealing existence — matches `ONBOARDING_AUTH_RBAC.md` §9.4 step 4).

---

## 5. Webhook vs. Polling

**Recommendation: webhook in production; keep polling only for local/dev.**

- Register a single webhook: `setWebhook(url="https://app.mihomes.ai/webhooks/telegram/<path-token>", secret_token=<X-Telegram-Bot-Api-Secret-Token>)`. Telegram POSTs each update to the FastAPI app (the same app that already serves the web UI and will host the auth/OIDC routes). Note: the app lives on **`app.mihomes.ai`** — the apex is the marketing site (`GTM_LAUNCH_PLAN.md` §5). The `secret_token` header is the primary check; the random path segment is defense-in-depth, not a secret to rely on (paths end up in logs).
- **Verify** every request against the `X-Telegram-Bot-Api-Secret-Token` header and the secret path segment; reject anything else. The secret is the shared-bot analogue of the WhatsApp `register_webhook` interface already sketched in `whatsapp/protocol.py`.
- The handler resolves `telegram_chat_id`/`telegram_user_id` → account, then dispatches to the existing `process_and_respond()` pipeline **within a tenant-scoped session**. Because the pipeline already takes a normalized message dict, the change is mostly transport + a resolver, not a rewrite.
- Add `callback_query` and `voice` to `allowed_updates` when interactive flows and voice land.

**Migration.** Keep `telegram monitor` (polling) for single-user local installs and dev. In hosted deployments, `setWebhook` is mutually exclusive with `getUpdates` — the watchdog's monitor supervision is replaced by the always-on FastAPI process. Provide `mihomes telegram set-webhook` / `delete-webhook` CLI commands and document the switchover.

---

## 6. Security & Abuse

- **Deny by default — once links exist.** Only linked, non-revoked `telegram_user_id`s can command the bot; messages from unknown users/chats are dropped silently (no reply that confirms the bot exists to a stranger). **Ordering caveat:** on day one *no* links exist, so enforcing this literally silences the bot for everyone including the owner. Until the linking flow has run, an unlinked sender in an already-linked chat is treated at **staff** level rather than denied outright — the narrowest role, not the widest (`../specs/SPEC-003-phase2-onboarding-team-rbac.md` D16). SPEC-006 replaces that bridge with real per-account resolution, where an unlinked sender gets a linking prompt instead of an account.
- **Per-role capability gating.** Reuse the single `require_permission(user, current_account, action, target_property)` check from `ONBOARDING_AUTH_RBAC.md` §9.4. Each responder category declares the action it needs (e.g. `expense_log` → `View finances`/manage finances; `pto_request` approval → staff-management). Staff attempting an owner/admin action get a clean "not permitted."
- **Token secrecy.** `telegram.bot_token` and the webhook `secret_token` are secrets — never logged, stored in the tenant/config store, rotated on suspected compromise.
- **Webhook authenticity.** Validate the secret header + path on every POST; rate-limit and drop malformed payloads.
- **Human-in-the-loop.** The `telegram review` queue remains the safety net for passive auto-creation — an operator can inspect AI-extracted items before they become records (`--accept`/`--auto`).
- **Audit.** Every privileged Telegram-initiated action (approvals, resolutions, expense logs) writes to the `audit_log`, tagged with the resolving membership — same requirement as web actions.
- **Abuse controls.** Per-user/per-chat rate limits; ignore edited-message replays; cap media download size.

---

## 7. Success Metrics

- **Messages handled** per account per week (inbound classified + acted on).
- **Records created via Telegram**: issues, tasks, work orders, expenses, assets — count and % of total record creation.
- **Response latency**: median time from inbound message to confirmation reply (target < 10s for text, < 30s with vision).
- **% handled without human review**: share of auto-created items not corrected/deleted within 7 days (proxy for classification accuracy).
- **Notification actionability**: % of proactive notifications that get a tap/reply.
- **Link adoption**: % of active memberships with a connected Telegram identity.
- **Delivery reliability**: % of outbound notifications delivered on first attempt.

---

## 8. Phasing

Mapped to the product phases in `../architecture/MULTITENANCY.md` §8 and `ONBOARDING_AUTH_RBAC.md`:

- **Phase 0 (landing/waitlist)** — no Telegram work; the current single-user gateway keeps running locally as-is.
- **Phase 1 (multitenant foundation)** — no Telegram work. The `telegram_links` tables are **not** in the Phase 1 baseline (`MULTITENANCY.md` §5.2 does not create them); they ship with the Telegram work itself as a 4+ growth bet, specced in `../specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md`. *(Corrected 2026-08-05: this bullet previously created those tables in Phase 1 and cited the "one local install = one account" bridge, which was dropped — `MULTITENANCY.md` §6.)*
- **Phase 2 (onboarding + RBAC)** — **the earliest the core Telegram multi-tenant work *can* land**, because it depends on memberships, linking-token infrastructure, and `require_permission`: `/link <code>` linking flow, RBAC-gated actions, account switch in DMs, staff scoping, webhook transport, `/help`. Caveat: `SAAS_PRD.md` §6.2/§10 classifies expanded Telegram as a **post-GA growth bet (Phase 4+)** — Phase 2 here is a dependency floor, not committed Phase 2 scope; nothing in Phases 2–4 core waits on it.
- **Phase 3 (billing)** — plan gates: which plans get proactive notifications or voice transcription (decide in `PRICING_AND_PACKAGING.md` — its current entitlement table has **no Telegram keys yet**; add them there, not here). Note Telegram linking itself consumes **no seat** — seats are memberships, enforced at invite time (`ONBOARDING_AUTH_RBAC.md` §6.4); a link is just an identity binding for an existing seat. Staff-scoped Telegram is implicitly Pro/Estate because Free has no staff role.
- **Phase 4 (GA)** — inline-keyboard flows polished, digest customization, multi-language, delivery/retry hardening, launch with the hosted app at **app.mihomes.ai**.

---

## 9. Open Questions

1. **One bot or many?** Recommendation is a single shared `@MiHomesBot`, but a large Estate customer may want a white-labeled bot — do we support optional per-account tokens later?
2. **Group-chat identity trust.** In a linked group, do we act on any linked member's message, or require per-user linking for privileged actions even inside a trusted group?
3. **Unlinked members in a linked group.** If a group is linked to an account but a sender isn't individually linked, do we (a) act with the group's default role, (b) treat as read-only context, or (c) ignore? Leaning (b/c) for privileged actions.
4. **Account resolution in DMs** for multi-account users — is a sticky "current account" enough, or should every action confirm its target account?
5. **Voice/transcription provider** — reuse the AI provider's audio capability, or a dedicated STT service? Cost and latency implications.
6. **Notification opt-in granularity** — per-user, per-category, per-home; where does this UI live (web vs `/settings` in chat)?
7. **Link revocation UX** — where does a user disconnect Telegram, and does revoking a membership auto-revoke the linked chat?
8. **Migration of existing `telegram.chat_links`** — auto-map the single local install's chats into its bridged account, or require re-linking?
