# WhatsApp Gateway — Product Requirements Document

Purpose: define the MiHomes WhatsApp gateway using the **WhatsApp Business Cloud API** (direct HTTPS from Python), covering the migration path from the free Developer API to the paid Business API, unified internal message format, AI orchestration, structured output, context assembly, file/media processing, supervision, CLI commands, database schema, security, performance, testing, deployment, and documentation.

Status: Draft — 2026-07-28

Related docs:
- `TELEGRAM_PRD.md` — sibling gateway; shared internal message format and AI pipeline.
- `../architecture/MULTITENANCY.md` — tenant isolation, `account_id` rule, Postgres migration.
- `ONBOARDING_AUTH_RBAC.md` — identity model, roles, capability matrix, linking.
- `TWILIO_PRD.md` — third gateway; shared channel-agnostic responder core is a prerequisite.
- `SAAS_PRD.md` — single-user to multi-tenant trajectory.

---

## 1. Current State (grounded in the code)

The WhatsApp gateway is a working, single-user, local-first chat interface. It is one of two messaging gateways; Telegram is the sibling (`src/mihomes/services/gateways/telegram/`, same `client/extractor/responder/review` shape). Both normalize inbound messages into one internal message dict so the AI review/responder pipeline is shared — though the implementations have diverged.

### 1.1 Transport — Node.js Baileys bridge + Python HTTP client

The WhatsApp gateway sits on a **two-process architecture**:

- **Bridge** (`bridge/index.js`, 359 lines): A Node.js Express server that runs `@whiskeysockets/baileys` ^6.7.0, the unofficial WhatsApp protocol library. It manages the WebSocket connection to WhatsApp's servers, handles auto-reconnect (except on logged-out events), displays QR codes in the terminal and serves them via HTTP, and exposes an Express REST API on `localhost:7867`. State is stored in-memory: `linkedGroups` (Map of group JID to property slug) and `messageStore` (array of last 1000 messages). Both are persisted to disk as JSON files (`group-links.json`, `messages.jsonl`). Media files download to `../.mihomes/media/whatsapp/`. Auth sessions live in `../.mihomes/whatsapp-auth/`.

- **Python client** (`src/mihomes/services/gateways/whatsapp/client.py`): A dependency-free `urllib` wrapper over the bridge's HTTP API. Methods: `get_status()`, `get_qr()`, `send_message()`, `send_group_message()`, `get_messages()`, `get_groups()`, `link_group()`, `unlink_group()`, `clear_messages()`, `is_connected()`. Base URL defaults to `http://localhost:7867` (configurable).

- **Real-time messages**: The bridge emits `messages.upsert` events. The Python `responder.py` does NOT poll for these — instead, the bridge's Express server has an endpoint that the Python process calls in a polling loop (driven by `mihomes whatsapp monitor`). This is different from Telegram's direct API polling; WhatsApp requires the bridge as an intermediary because Baileys manages the raw WebSocket.

### 1.2 Message understanding — AI review

`review.py` (`analyze_messages()`) sends formatted conversation history to the configured AI provider with a JSON schema (`REVIEW_SCHEMA`, 8 categories). It injects an estate context block — open issues, tracked assets, staff for the linked property. Output is `{items, skipped}`.

> **Corrected 2026-08-05** *(verified against `origin/main` @ `be8d398` — SPEC-006 §2, B5)*. This paragraph claimed the WhatsApp `REVIEW_SCHEMA` had **8 categories vs Telegram's 15**, listing eight that "do not exist in the WhatsApp schema". **That split no longer exists.** Commit `c4954a0` unified both gateways on a single superset schema of **15 categories** in `services/gateways/review_common.py`; the per-gateway `review.py` files are now 16-line re-exports. Their own docstring records what happened: *"this WhatsApp schema had lost 8 categories the dispatcher still handled"* — the drift was real, and it has been repaired. The sentence above (`REVIEW_SCHEMA`, 8 categories) is stale for the same reason.

### 1.3 Action — the responder

`responder.py` (**285 lines** as of `c4954a0` — was 529 before the shared core landed) handles real-time messages. It classifies inbound messages via the AI review pipeline and dispatches to MiHomes service calls. Dispatch itself now lives in `services/gateways/review_common.py`; what remains here is the WhatsApp `GatewayAdapter` and its client wiring. Notable behaviors:

- **AI routing**: Messages are sent to the AI orchestrator for classification. Based on the category, the responder creates issues, tasks, or sends confirmation replies.
- **AI replies**: Questions are answered by the estate-manager advisor. Replies are plain text, constrained in length. `NO_RESPONSE` suppression exists.
- **Issue logging**: When a message is classified as an issue, a MiHomes issue record is created with severity, room, and reporter fields.

**Parity with Telegram** *(corrected 2026-08-05, verified against `origin/main` @ `be8d398` — SPEC-006 §2 B1)*: this section previously claimed five divergences from Telegram — no PTO approval flow, no inventory chat routing, no photo-to-Document linking, no maintenance-expert assessment, no structured APPROVE/DENY commands. **All five were false**, and `PRD_REVIEW.md` §G1 verified each against the code. Since `c4954a0` both gateways share a single dispatcher (`services/gateways/review_common.py`), so parity is now structural rather than coincidental: `handle_approval_messages`, `dispatch_items` and `is_trusted_sender` are the same functions for both channels, and the only per-channel code is the `GatewayAdapter` that delivers the reply.

> The deleted claim was load-bearing: it was the stated premise for §2 gap #6, three §3 P1 rows, and §8.2. **Treat those four sections as withdrawn** pending a rewrite against the shared-core reality.

### 1.4 Passive review queue

`review.py` also provides `review_queue()` — a batch AI analysis of recent messages that surfaces items for human review. The CLI `mihomes whatsapp review` presents an interactive queue with `--accept` / `--auto` flags. This mirrors the Telegram review flow but with fewer categories to classify.

### 1.5 Auto-extraction

`extractor.py` (159 lines) provides `extract_and_create()` — fetches new messages from the bridge, runs AI analysis, and auto-creates issues/tasks without sending confirmation replies. Uses a local JSON file (`whatsapp-processed-ids.json`) for deduplication.

### 1.6 Supported capabilities (today)

| Category | Action taken | Backing service | Reply |
|---|---|---|---|
| `issue` | Create issue (+ severity, room, reporter) | `services/issue.create_issue` | "Issue logged" |
| `task` | Create task (+ assignee) | `services/task.create_task` | "Task logged" |
| `question` | Answer via AI estate manager | `services/ai/orchestrator.ask` | 1-2 sentence answer |
| `informational` | Ignore | — | (none) |
| Bridge QR/pairing | Display QR code for WhatsApp linking | `bridge` HTTP API | Terminal output |
| Group linking | Map WhatsApp group to property | `client.link_group()` | Confirmation |

### 1.7 CLI surface (`mihomes whatsapp …`)

`setup` (pairing instructions), `pair` (scan QR code to link phone), `status` (bridge connection + linked groups), `link` / `unlink` (map group JID to property slug), `monitor` (polling loop for new messages), `review` (interactive AI review queue), `extract` (auto-create issues/tasks), `bridge-start` / `bridge-stop` / `bridge-status` (manage the Node.js bridge process).

### 1.8 Supervision — the watchdog gap

`scripts/watchdog.py` runs detached, checks every 60s, and restarts the Telegram monitor if its PID dies. It **does** supervise the WhatsApp monitor when `whatsapp.autostart` is set in configuration, but the **Node.js bridge process itself is not watched**. If the bridge crashes (memory leak, Baileys reconnect loop), it stays down until someone notices and runs `bridge-start` manually.

### 1.9 Configuration keys (SQLite `configuration` table)

`whatsapp.bridge_url` (default `http://localhost:7867`), `whatsapp.chat_links` (JSON group JID to property slug), `whatsapp.owner_chat_id`, `whatsapp.last_message_id`, `whatsapp.processed_ids` (local JSON file), `whatsapp.autostart`, `whatsapp.monitor_property`. **All are global** — no `account_id` scoping. The group mapping is file-based (`group-links.json` in the auth directory), not database-backed like Telegram's config table.

---

## 2. Gap Analysis

What is missing or weak for a real, multi-tenant product:

1. **No multi-tenancy.** All config is global; `chat_links` maps a group JID to a *property slug*, not to an `(account_id, membership, role)`. One deployment = one estate.
2. **No identity linking.** `sender` is a raw phone number with no mapping to a MiHomes `users`/`memberships` row. Anyone in a linked group can command the bot.
3. **File-based state.** Group links, processed message IDs, and auth sessions live in JSON files on disk. This doesn't survive restarts cleanly, can't be queried via the API, and doesn't support multi-tenant scoping. Telegram already uses the database config table — WhatsApp should too.
4. **Node.js bridge is a single point of failure.** The bridge is not supervised by the watchdog. It has no health endpoint, no graceful shutdown, no metrics. If it crashes, WhatsApp goes dark.
5. **Weak authorization.** No action checks sender identity or role. Every category is available to any group member.
6. **Divergent capability surface.** WhatsApp handles 4 categories; Telegram handles 15+. This creates maintenance burden and inconsistent user experience across gateways.
7. **No proactive notifications.** No "task due", "critical issue logged", "low stock" pushes to WhatsApp.
8. **No interactive UX.** No quick-reply buttons, confirmation flows, or inline actions. Everything is typed or passive.
9. **No media handling beyond basic download.** Images are downloaded but not vision-analyzed (unlike Telegram's inventory chat). Documents, voice notes, and videos are not processed.
10. **No rate limiting or abuse controls.** Unknown groups are effectively ignored because they aren't in `chat_links`, but there's no explicit deny or logging.
11. **No delivery/read status.** Send failures are swallowed. No retry logic.
12. **No `/help` or onboarding.** No guided first-run experience.
13. **Message dedup is file-based.** `whatsapp-processed-ids.json` is a local sidecar — same pattern Telegram used to have before moving to the DB config table. Should be unified.
14. **Baileys is unmaintained.** The library is effectively abandoned. A WhatsApp protocol update can break it at any time with no fix available.

---

## 3. Proposed New Capabilities (prioritized)

MoSCoW, each tied to an existing MiHomes entity/feature.

| Pri | Capability | Ties to | Notes |
|---|---|---|---|
| **P0 / Must** | Account+identity linking (`/link <code>`) | `memberships`, `accounts` | Map phone number → membership. Foundation for everything below. |
| **P0 / Must** | RBAC-gated actions | capability matrix §9.2 | Every responder action checks `(role, action, home)` before executing. |
| **P0 / Must** | Database-backed state | `configuration` table | Move group links, processed IDs from JSON files to DB. |
| **P0 / Must** | Migrate from Baileys to Cloud API | `client.py`, `protocol.py` | Replace bridge with direct HTTPS client (like Telegram). |
| **P0 / Must** | Ignore unlinked/unauthorized chats explicitly | security §6 | Deny-by-default, not accidental. |
| **P1 / Should** | Align WhatsApp categories to Telegram's 15 | `responder.py`, `review.py` | Add `issue_resolution`, `work_order_request`, `appointment_request`, `expense_log`, `task_completion`, `book_addition`, `asset_addition`, `note_addition`. |
| **P1 / Should** | Photo-based issue logging with confirmation | issue + document | Images attached to issues get linked as Document records. |
| **P1 / Should** | Proactive notifications | task, issue, consumable, appointment | Outbound push to the right linked user, role-scoped. |
| **P1 / Should** | Quick-reply buttons for confirmations | task, issue | WhatsApp `replyMarkup` for approve/deny, complete task. |
| **P1 / Should** | Media processing pipeline | `ai/file_processor.py` | Vision analysis for images, document text extraction, voice note transcription. |
| **P1 / Should** | Inventory chat routing | asset | Special group for property cataloging: photos → room scan → asset creation. |
| **P1 / Should** | PTO request + approval flow | staff_pto | Submit PTO via chat; approver gets notification with approve/deny buttons. |
| **P2 / Could** | `/help` + in-chat onboarding | — | WhatsApp `list` messages for command menu. |
| **P2 / Could** | Digest customization | consumable digest, budget | Frequency, content, per-user configuration. |
| **P2 / Could** | Multi-language replies | AI prompts | Detect user language; localize confirmations. |
| **P2 / Could** | Delivery/retry + send-failure surfacing | client | Retry with backoff; log undelivered messages. |
| **P2 / Could** | Location sharing for property check-ins | property, staff | Staff "on-site" check-in via WhatsApp location. |

---

## 4. Multi-Tenant Design for WhatsApp

**Recommendation: one shared WhatsApp number + explicit linking step**, not a number per account. Per-account numbers would require separate lines (high cost) and multiply Cloud API instances. A single number with a linking flow is the right fit — mirroring the Telegram decision.

### 4.1 Linking flow

1. In the web app (signed in via Google, `ONBOARDING_AUTH_RBAC.md` §3), the user requests **"Connect WhatsApp."** MiHomes issues a short-lived, single-use **link code** bound to `(user_id, account_id, membership_id)` — same discipline as invite tokens: stored **hashed**, single-use, expiring (minutes).
2. The user opens WhatsApp and messages the MiHomes number with `/link <code>`.
3. The gateway resolves the code, verifies it is unexpired/unused, and writes a **`whatsapp_links`** row: `(phone_number, account_id, membership_id, role, linked_at, revoked_at)`. Do **not** persist `role` denormalized as authoritative — resolve from the membership on every request. Group chats additionally get a **`whatsapp_chat_links`** row: `(whatsapp_group_jid, account_id, home_id)`. Only an **owner/admin** membership may link a group.
4. Every subsequent message resolves `phone_number → membership → (account_id, role, home scopes)`. A revoked membership fails resolution — **revoking a membership implicitly revokes the link**. All service calls run **tenant-scoped** and **role-checked** (§6).

### 4.2 The account-switch problem

Same as Telegram (§4.2): a user in two accounts has two `whatsapp_links` rows. In a linked group, the account comes from `whatsapp_chat_links.account_id`. In a 1:1 chat with >1 account, maintain a `whatsapp_dm_context` row with `/account` to switch. Default to most recently used.

### 4.3 Staff scoping

Staff links carry `membership_home_scopes`. A staff user's actions are restricted to their assigned home(s). A group chat linked to a home they aren't scoped to yields a clean "not found" message.

---

## 5. Cloud API Architecture

This section defines the target architecture: Python talks directly to the WhatsApp Business Cloud API via HTTPS, with webhooks for incoming messages. The Node.js Baileys bridge is replaced.

### 5.1 WhatsApp Business Cloud API — Overview

The Cloud API is a REST service at `https://graph.facebook.com/vX.X/{phone-number-id}/`. It uses Bearer token authentication and standard JSON request/response bodies. There is no WebSocket or bridge — Python calls the API directly using `requests` or `urllib`.

**Two API tiers:**

| Tier | Cost | Setup | Features |
|---|---|---|---|
| **Developer API** (free) | Free | Meta Developer account + test phone number | Send/receive messages to verified numbers only. No group support. Ideal for development and testing. |
| **Business API** (paid) | Per-conversation pricing | WABA + business verification + app review | Full production: groups, template messages, higher rate limits. Pricing varies by conversation category (utility, marketing, authentication, service). |

**Migration path**: Start with Developer API for development and testing. When ready for production, migrate to Business API by updating the access token and phone number ID in configuration. The Python client code changes minimally — mostly token and phone-number-id values.

> ⚠️ **Group support is an unresolved blocker, not a detail** *(added 2026-08-05 — SPEC-006 §2, B6;
> `PRD_REVIEW.md` §G5)*. The table above states the Developer tier supports "verified numbers only.
> **No group support**", yet §16's migration promises "no behavior change for existing users" — and
> **the live product is group-based**: `whatsapp.inventory_group_jid` routes an inventory *group*,
> and the CLI ships `groups`, `link-group`, `unlink-group` and `send-group`. Migrating to a tier
> without group support is a **total loss of function**, not a transport swap. This document
> asserts group messaging works (§ later), contradicts itself here, and re-asks the same question
> in §17 Q8 — answering, denying and re-opening it in three places.
>
> **Resolution:** the founder has decided (2026-08-05) that WhatsApp stays in the product and
> migrates off Baileys to the official Cloud API. **Which tier, and whether groups survive it, is
> tracked as `O1` in `docs/specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md` §1.3** — openly
> open, rather than contradicted across three sections. The tier-independent work (the
> `WhatsAppBridge` Protocol implementation, the adapter, the webhook) proceeds regardless.

### 5.2 Authentication

```
Authorization: Bearer <EAA-access-token>
```

- **Developer API**: Short-lived test token from Meta Developer dashboard. Refreshable to long-lived token (60 days).
- **Business API**: Long-lived page access token linked to WABA. Generated via Meta Business Manager.

Token is stored in the DB config table as `whatsapp.access_token` and `whatsapp.phone_number_id`.

### 5.3 Outbound messages — direct HTTPS POST

Send a message by POSTing to the Cloud API:

```
POST https://graph.facebook.com/v21.0/{phone-number-id}/messages
Authorization: Bearer <token>
Content-Type: application/json

{
  "messaging_product": "whatsapp",
  "to": "+1234567890",
  "type": "text",
  "text": { "body": "Your issue has been logged." }
}
```

**Message types supported**: text (quick-reply buttons via `messaging_template`), image, document, video, audio (voice note), location, sticker, template (pre-approved templates for outside the 24-hour window).

**Group messages**: Set `"to"` to the group JID in WhatsApp format (`1234567890-1234567890@g.us`).

**Rate limits**: Developer API — 80 messages/conversation/24h per phone number. Business API — higher limits based on tier.

### 5.4 Inbound messages — Webhook

The Cloud API delivers incoming messages via webhook POST to a registered callback URL:

```
POST https://<your-server>/wa/webhook
```

**Webhook registration**:

```
POST https://graph.facebook.com/v21.0/{app-id}/webhooks
Authorization: Bearer <app-token>
Content-Type: application/json

{
  "callback_url": "https://<your-server>/wa/webhook",
  "fields": ["messages"]
}
```

**Verification handshake** (on first setup): Meta sends a GET with `hub.mode=verify`, `hub.challenge` (random string to return), and `hub.verify_token` (arbitrary secret you choose). Your server returns the challenge in the response body to complete verification.

**Webhook payload** (incoming message):

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "WABA_ID",
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "metadata": { "display_phone_number": "+1234567890" },
        "contacts": [{ "wa_id": "1234567890", "name": "John" }],
        "messages": [{
          "from": "1234567890",
          "id": "msg-id",
          "timestamp": "1700000000",
          "text": { "body": "The AC is making a noise" },
          "type": "text"
        }]
      }
    }]
  }]
}
```

**Fallback polling**: If webhook is not available (local dev, no public HTTPS endpoint), the Python client falls back to polling the Messages API endpoint with a cursor. This is the same approach Telegram uses. Polling is less reliable (up to 5-minute delay) but works for development.

### 5.5 Media handling

**Incoming media**: The webhook payload contains a media `id`, not a URL. To download:

1. GET `https://graph.facebook.com/v21.0/{media-id}` with the same Bearer token to get a JSON response with a `url` field.
2. GET the URL to download the file. The URL expires in 5 minutes. Media IDs expire after 30 days.

**Outbound media**:

1. POST the file to `https://graph.facebook.com/v21.0/{phone-number-id}/media` to upload.
2. Receive a media ID.
3. Send the message using `type: "image"` (or document/video/audio) with the media ID.

Supported types: image, document, video, audio (voice note), sticker. Max size: 100MB (document), 16MB (other).

### 5.6 24-hour customer service window

The Cloud API enforces a **24-hour customer service window**. After a user sends a message, the business has 24 hours to respond freely. After the window closes:

- **Free messages**: Only allowed within the 24-hour window. The user initiates the conversation.
- **Template messages**: Required outside the window. Templates are pre-approved by Meta and support named or positional variables. Examples:
  - `"body": "Your work order #{{1}} is scheduled for {{2}}"`
  - `"body": "Reminder: PTO request for {{1}} is pending approval"`
- **Template categories**: Meta classifies templates into utility, authentication, marketing, and service. Utility and authentication are cheapest; marketing is most expensive.
- **Template approval**: Submit templates via the API or Meta Business Manager. Approval typically takes minutes to hours.

**Implications for MiHomes**:

- Proactive notifications (task due, critical issue) sent outside the 24-hour window require template messages.
- The responder must track the last inbound message timestamp per chat to determine if a free reply or template is needed.
- Template usage incurs per-conversation costs on the Business API. Developer API does not support templates (no production usage).
- The AI should be aware of this constraint when generating replies — avoid sending proactive messages late at night if the window has closed and templates are expensive.

### 5.7 Message status and delivery

The Cloud API provides message status events via webhook:

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "changes": [{
      "value": {
        "messaging_product": "whatsapp",
        "statuses": [{
          "id": "msg-id",
          "status": "delivered" | "read" | "sent" | "failed",
          "timestamp": "1700000000",
          "recipient_id": "1234567890"
        }]
      }
    }]
  }]
}
```

Statuses: `sent` (accepted by WhatsApp), `delivered` (reached device), `read` (opened by user), `failed` (delivery failed). Failed messages should be retried once after 30 seconds, then logged.

### 5.8 Comparison: Baileys bridge vs Cloud API

| Aspect | Baileys Bridge (current) | Cloud API (target) |
|---|---|---|
| Process | Node.js Express server (separate) | Direct HTTPS from Python |
| Auth | QR code pairing | Bearer token |
| Inbound | WebSocket → bridge HTTP endpoint | Webhook POST or polling |
| Outbound | Python → bridge HTTP → WhatsApp | Python → Cloud API → WhatsApp |
| State | File-based (JSON) | DB-backed |
| Supervision | Not in watchdog | N/A (no separate process) |
| Groups | Supported | Supported |
| Media | Bridge downloads to disk | Resolve URL via Media API |
| Proactive | Anytime (WebSocket is always on) | 24h window + templates |
| Cost | Free (unofficial) | Free dev tier; per-conversation paid |
| Maintenance | Baileys unmaintained, breaks on updates | Official Meta API, maintained |
| Reliability | Bridge crash = WhatsApp dark | No single point of failure |

**Bottom line**: Cloud API eliminates the bridge process, file-based state, QR pairing, and Node.js dependency. The Python gateway layer (`responder.py`, `review.py`, `extractor.py`) stays almost identical. Only `client.py` changes — from a bridge wrapper to a Cloud API client.

---

## 6. Security & Abuse

- **Deny by default.** Only linked, non-revoked phone numbers can command the bot. Messages from unknown numbers/groups are dropped silently.
- **Per-role capability gating.** Reuse `require_permission(user, current_account, action, target_home)` from `ONBOARDING_AUTH_RBAC.md` §9.4. Each responder category declares the action it needs. Staff attempting owner/admin actions get a clean "not permitted."
- **Phone number privacy.** Phone numbers are stored hashed in `whatsapp_links` (same as Telegram user IDs). Never logged or displayed in replies.
- **Link code security.** Short-lived (5 min), single-use, hashed storage. Brute-force resistant (rate-limited to 3 attempts per code).
- **Human-in-the-loop.** The `whatsapp review` queue remains the safety net for passive auto-creation.
- **Audit.** Every privileged WhatsApp-initiated action writes to the `audit_log`, tagged with the resolving membership.
- **Abuse controls.** Per-phone-number rate limits (10 messages/minute); ignore message replays within 5s window; cap media download size at 100MB (WhatsApp default for documents).
- **Webhook security.** Verify `hub.verify_token` on setup. Optionally sign webhook payloads with a secret and verify the signature on receipt. Reject requests not from Meta IP ranges (`https://developers.facebook.com/docs/graph-api/webhooks/webhook-reference/#verifying-payloads`).
- **Token security.** Access token stored encrypted in DB config table. Never logged or exposed in error messages. Rotated via Meta Business Manager.

---

## 7. Unified Internal Message Format

Both gateways MUST use the same internal message dict format. The WhatsApp gateway already produces the same shape as Telegram's `normalize_update()`:

```python
{
    "id": str,           # unique message ID (WhatsApp: message key)
    "timestamp": int,    # Unix epoch seconds
    "jid": str,          # WhatsApp group JID or phone number
    "isGroup": bool,
    "sender": str,       # phone number (WhatsApp) or user_id (Telegram)
    "senderName": str | None,
    "senderUsername": str | None,  # None for WhatsApp (no username concept)
    "text": str | None,
    "hasMedia": bool,
    "mediaPath": str | None,       # local file path for downloaded media
    "propertySlug": str | None,    # resolved from chat link mapping
}
```

> **Corrected 2026-08-05** *(SPEC-006 §2, B3/B8)*. **This action is already done, and two of its details were wrong.** The shared module exists as **`src/mihomes/services/gateways/review_common.py`** — not `shared/normalizer.py`; no `shared/` directory was ever created. And there is no `normalize_message()` function to extract: Telegram has `normalize_update()`, while WhatsApp normalizes in **Node**, so there was no Python WhatsApp normalizer in the first place. `TWILIO_PRD.md` §2.3's "extract the channel-agnostic core before a third responder lands" was satisfied by commit `c4954a0`.

---

## 8. AI Orchestration — Unifying the Responder

### 8.1 Category alignment

> **Corrected 2026-08-05** *(SPEC-006 §2, B3/B5)*. **Done.** The WhatsApp schema was extended to Telegram's 15 categories by commit `c4954a0` — as a single superset `REVIEW_SCHEMA` living at **`src/mihomes/services/gateways/review_common.py`**, not at `shared/schema.py`. The block below describes the intended end state, which now exists; read it as a record of what shipped, not as work to do.

```python
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {
                        "enum": [
                            "issue", "issue_resolution", "task", "task_completion",
                            "work_order_request", "vendor_activity", "supply_need",
                            "appointment_request", "expense_log",
                            "book_addition", "asset_addition", "note_addition",
                            "pto_request", "question", "informational",
                        ]
                    },
                    # ... common fields
                }
            }
        },
        "skipped": {"type": "integer"},
    }
}
```

### 8.2 Shared responder core

> **Corrected 2026-08-05** *(SPEC-006 §2, B3)*. **Done.** The common dispatch logic was extracted by commit `c4954a0` into **`src/mihomes/services/gateways/review_common.py`** (1,175 lines — `dispatch_items`, `analyze_messages`, `handle_approval_messages`, `is_trusted_sender`, and the `GatewayAdapter` seam), not `shared/responder.py`. Both responders shrank to ~285 lines. Do **not** re-extract it (SPEC-006 N1).

- `dispatch(item, message)` — takes a classified item and normalized message, resolves the user's role/home, checks permissions, calls the appropriate service.
- `_ai_response(role, category, context)` — role-based AI reply generation (estate manager for questions, maintenance expert for issues, etc.).
- `_send_confirmation(client, message, text)` — gateway-specific send wrapper.

Each gateway's `responder.py` becomes a thin wrapper: load config, resolve tenant, call `dispatch()`, format the reply. This eliminates the current divergence where Telegram handles 11 categories and WhatsApp handles 4.

### 8.3 Context assembly

Reuse `assemble_context()` from `src/mihomes/services/ai/context.py` — it already pulls from 13+ data sources with token budgeting. No changes needed; both gateways already call it.

### 8.4 File/media processing

Reuse `process_upload()` from `src/mihomes/services/ai/file_processor.py`. The WhatsApp Cloud API resolves media URLs and downloads to disk; the shared processor handles images (vision), text documents (PDF, TXT), and other types. Add voice note transcription as a P1 item.

---

## 9. Database Schema Changes

New tables and columns to support multi-tenancy and database-backed state:

### 9.1 `whatsapp_links` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `phone_hash` | TEXT NOT NULL | SHA-256 of phone number; unique |
| `phone_last4` | TEXT NOT NULL | Last 4 digits for display only |
| `phone_number` | TEXT NOT NULL | Full number (encrypted); unique |
| `account_id` | INTEGER NOT NULL | FK to accounts |
| `membership_id` | INTEGER NOT NULL | FK to memberships |
| `role` | TEXT NOT NULL | Resolved from membership; not authoritative |
| `linked_at` | DATETIME NOT NULL | |
| `revoked_at` | DATETIME | Non-null = revoked |
| `dm_context_account_id` | INTEGER | Current account for DM (multi-account users) |

### 9.2 `whatsapp_chat_links` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | INTEGER PK | |
| `group_jid` | TEXT NOT NULL | Full WhatsApp group JID; unique |
| `account_id` | INTEGER NOT NULL | FK to accounts |
| `home_id` | INTEGER NOT NULL | FK to properties |
| `linked_by_membership_id` | INTEGER NOT NULL | Who linked it (owner/admin only) |
| `linked_at` | DATETIME NOT NULL | |
| `revoked_at` | DATETIME | |

### 9.3 `whatsapp_messages` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | TEXT PK | WhatsApp message ID |
| `account_id` | INTEGER NOT NULL | |
| `group_jid` | TEXT | NULL for 1:1 chats |
| `phone_hash` | TEXT NOT NULL | |
| `timestamp` | DATETIME NOT NULL | |
| `body` | TEXT | |
| `has_media` | BOOLEAN | |
| `media_type` | TEXT | image, document, voice, etc. |
| `classified_as` | TEXT | AI classification result |
| `created_at` | DATETIME NOT NULL | |

Retention: purge older than 30 days (configurable). This replaces `messages.jsonl`.

### 9.4 Configuration table changes

Move these from file-based to DB config:

| Key | Current location | New location |
|---|---|---|
| `whatsapp.group_links` | `group-links.json` | `configuration` table |
| `whatsapp.last_message_id` | `whatsapp-processed-ids.json` | `configuration` table |
| `whatsapp.access_token` | (not stored) | `configuration` table |
| `whatsapp.phone_number_id` | (not stored) | `configuration` table |
| `whatsapp.webhook_url` | (not stored) | `configuration` table |
| `whatsapp.webhook_verify_token` | (not stored) | `configuration` table |

---

## 10. CLI Commands

### 10.1 Current commands (`mihomes whatsapp …`)

| Command | Description |
|---|---|
| `setup` | Pairing instructions |
| `pair` | Scan QR code to link phone |
| `status` | Bridge connection + linked groups |
| `link` | Map group JID to property slug |
| `unlink` | Remove group mapping |
| `monitor` | Polling loop for new messages |
| `review` | Interactive AI review queue |
| `extract` | Auto-create issues/tasks |
| `bridge-start` | Start the Node.js bridge |
| `bridge-stop` | Stop the Node.js bridge |
| `bridge-status` | Check bridge process state |

### 10.2 New commands (Cloud API)

| Command | Description |
|---|---|
| `setup` | Configure access token, phone number ID, webhook URL |
| `status` | API connection status + linked groups + user count |
| `link` | Map group JID to property slug (kept for migration) |
| `unlink` | Remove group mapping |
| `monitor` | Polling loop for new messages (fallback when no webhook) |
| `webhook` | Register webhook URL with Meta; start local webhook server |
| `webhook-verify` | Test webhook verification handshake |
| `review` | Interactive AI review queue |
| `extract` | Auto-create issues/tasks |
| `template` | Submit a message template for Meta approval |
| `template-list` | List submitted templates and their status |
| `send` | Send a test message (text or template) |
| `link-chat` | Link a 1:1 chat or group to a user account (multi-tenant) |
| `unlink-chat` | Remove a chat link |
| `chat-list` | List all linked chats with account/user info |
| `review --accept N,M` | Accept specific items from review queue |
| `review --auto` | Auto-accept all items in review queue |
| `extract --property slug` | Extract messages for a specific property |

The `monitor` command works identically across both gateways. The `webhook` command starts a lightweight HTTP server (using the existing `http.server` or a minimal Flask/FastAPI endpoint) to receive incoming messages from Meta.

---

## 11. Supervision and Monitoring

### 11.1 Watchdog integration

1. The Cloud API requires no separate process — the Python gateway IS the WhatsApp process. Watchdog continues to supervise the Telegram monitor and the main `mihomes` process.
2. If webhook-based delivery is used, no polling loop runs — the webhook server handles inbound messages directly.
3. If polling fallback is used (`mihomes whatsapp monitor`), the monitor PID is stored in the DB config table and watched by watchdog (same as Telegram).

### 11.2 Health monitoring

1. Health check: `GET https://graph.facebook.com/v21.0/{phone-number-id}` with Bearer token. Returns 200 if the token and phone number ID are valid.
2. Python client calls this on startup and periodically (every 5 minutes). If unhealthy, log a warning and skip sends.
3. CLI `mihomes whatsapp status` shows API health, last successful sync, and linked chat count.

### 11.3 Metrics

The Python client tracks metrics in-memory and exposes them via CLI `status`:

```
WhatsApp Gateway
  API: connected (v21.0)
  Webhook: registered (https://myserver.com/wa/webhook)
  Messages received: 1,234
  Messages sent: 567
  Failed sends: 3
  Linked chats: 5
  Last message: 2026-07-28 10:30:00
```

---

## 12. Performance Requirements

| Metric | Target | Notes |
|---|---|---|
| Message classification latency | < 5s p50, < 15s p95 | Text-only; vision adds ~10s |
| Reply confirmation latency | < 10s p50, < 30s p95 | Includes AI response generation |
| Media download latency | < 5s | URL resolution + download |
| Media upload latency | < 10s | Upload to Media API + receive ID |
| Message store retention | 30 days | Configurable; DB-backed |
| Concurrent groups | Unlimited | API handles this; DB queries scoped by group_jid |
| Rate limit per phone | 80 messages/conversation/24h | Cloud API default; queue excess |

---

## 13. Testing Strategy

### 13.1 Unit tests

- `test_normalizer.py` — validate message dict shape for WhatsApp and Telegram inputs.
- `test_responder_dispatch.py` — test shared responder dispatch with mocked services.
- `test_review_schema.py` — validate AI classification output against schema.
- `test_cloud_client.py` — test the Cloud API client with a mock HTTP server (no real API calls).
- `test_template_builder.py` — test template message construction with variables.

### 13.2 Integration tests

- `test_webhook.py` — start webhook server, verify verification handshake.
- `test_group_linking.py` — link/unlink groups, verify DB state.
- `test_message_flow.py` — send message through Cloud API → webhook → Python → AI review → service call → confirmation reply.
- `test_dedup.py` — verify duplicate messages are not processed twice.

### 13.3 E2E tests

- Full flow: configure token → link group → send message → verify issue created → send resolution → verify issue resolved.
- Multi-group scenario: two groups linked to different properties; verify messages route to correct property.
- Template flow: submit template → wait for approval → send template message outside 24h window.

### 13.4 Load tests

- Simulate 50 messages/minute across 10 groups; verify no message loss and acceptable latency.

---

## 14. Deployment Considerations

### 14.1 Local install (current → Cloud API)

- **Before**: Two processes: Python `mihomes` + Node.js bridge.
- **After**: Single process: Python `mihomes` with Cloud API client. Optional webhook server (embedded in the same process or separate).
- All state on local disk (DB + media directory).
- For local development without a public HTTPS endpoint: use polling fallback (`mihomes whatsapp monitor`) or ngrok/Tailscale for webhook exposure.

### 14.2 Hosted multi-tenant (future)

- One Cloud API instance per WABA (WhatsApp Business Account).
- Python gateway processes share the API via HTTPS.
- Media stored in cloud storage (S3) instead of local disk.
- Webhook URL is a public HTTPS endpoint (e.g., `https://api.mihomes.app/wa/webhook`).

### 14.3 Migration from Baileys to Cloud API

1. Run `mihomes whatsapp setup` to configure Cloud API token and phone number ID.
2. Run `mihomes whatsapp bridge-migrate` to move `group-links.json` and `messages.jsonl` to DB tables.
3. Existing `whatsapp.chat_links` config entries are migrated to `whatsapp_chat_links` rows.
4. Phone numbers are hashed; last 4 digits preserved for display.
5. Verify webhook registration or start polling mode.
6. Decommission the Node.js bridge process.

---

## 15. Documentation Requirements

- Update `CLAUDE.md` conventions to include WhatsApp CLI commands alongside Telegram.
- Add WhatsApp section to the how-to guide (Millena's docs).
- Add Cloud API setup guide (token generation, phone number setup, webhook registration).
- Add migration guide for moving from Baileys bridge to Cloud API.
- Update the shared gateway docs in `src/mihomes/services/gateways/README.md` (if it exists; create if not).
- Document the linking flow in `ONBOARDING_AUTH_RBAC.md` (add WhatsApp to the identity linking section).
- Document 24-hour window behavior and template message usage.

---

## 16. Phasing

Mapped to the product phases in `../architecture/MULTITENANCY.md` §8 and `ONBOARDING_AUTH_RBAC.md`:

- **Phase 0 (current)** — **Migrate from Baileys to Cloud API (Developer API)**. Replace bridge with direct HTTPS client. Move group links and processed IDs to DB. Add webhook support with polling fallback. Category alignment to Telegram's 15. No behavior change for existing users beyond reliability improvements.
- **Phase 1 (multitenant foundation)** — Introduce `whatsapp_links` / `whatsapp_chat_links` tables. No linking flow yet — existing manual `link` command continues to work. Media processing pipeline (vision, documents, voice).
- **Phase 2 (onboarding + RBAC)** — `/link <code>` linking flow, RBAC-gated actions, account switch in DMs, staff scoping. Depends on membership/linking-token infrastructure from `ONBOARDING_AUTH_RBAC.md`.
- **Phase 3 (Business API migration)** — Migrate from Developer API to Business API. Enable template messages for proactive notifications outside the 24-hour window. Proactive notifications (task due, critical issue, low stock). Quick-reply buttons for confirmations.
- **Phase 4 (GA)** — Digest customization, multi-language, delivery/retry hardening, location sharing, PTO approval flow, inventory chat routing.

---

## 17. Open Questions

1. **Phone number as identity.** WhatsApp sends the full phone number; Telegram sends a user_id. Phone numbers are PII — is hashing sufficient, or do we need explicit consent flows?
2. **Group JID format stability.** WhatsApp group JIDs include the broadcast list ID and suffix. Are these stable across group renames/recreates?
3. **Media storage.** Local disk works for single-user. For multi-tenant hosted, do we use S3 or a shared network volume?
4. **Webhook vs polling for local dev.** Without a public HTTPS endpoint, webhook delivery is impossible. Should we embed ngrok as a dev dependency, or stick with polling (up to 5-minute delay)?
5. **Template message costs.** Meta charges per 24-hour conversation window. How does this compare to the free Baileys approach for typical usage patterns? What's the expected monthly cost at scale?
6. **Unlinked members in a linked group.** Same question as Telegram (§9.3): act with group's default role, treat as read-only context, or ignore?
7. **Link revocation UX.** Where does a user disconnect WhatsApp, and does revoking a membership auto-revoke the linked phone number?
8. **Developer API group support.** Does the free Developer API support group messaging, or is it 1:1 only? This affects whether we can test group features before Business API migration.
9. **Webhook IP filtering.** Meta publishes a list of IP ranges for webhook delivery. Should we enforce this at the firewall level, or is token verification sufficient?
10. **Token rotation.** How often do Developer API tokens expire, and what's the UX for rotating them without breaking the gateway?