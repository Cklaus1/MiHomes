# Omnichannel Agent Gateway — PRD

## 1. Current State

MiHomes has **two independent gateway implementations**, each a self-contained channel:

| Component | WhatsApp | Telegram |
|---|---|---|
| **Protocol** | `WhatsAppBridge` — `send_message`, `send_template`, `get_message_status`, `register_webhook`. **Declared in `whatsapp/protocol.py`, with zero implementers** — a seam awaiting the Cloud API, not current behaviour | **None.** There is no `TelegramBot` Protocol; the class is `TelegramClient`, so Telegram currently *violates* the behind-a-Protocol rule this doc set states elsewhere |
| **Client** | `whatsapp/client.py` — HTTP client wrapping the Baileys bridge via `urllib`. **Retired by SPEC-006 Step 10** | `telegram/client.py` — direct Bot API via `urllib` |
| **Responder** | `whatsapp/responder.py` (**285 lines**) — delegates to `review_common` | `telegram/responder.py` (**271 lines**) — delegates to `review_common` |
| **Shared core** | `gateways/review_common.py` (**1,175 lines**) — `GatewayAdapter`, `REVIEW_SCHEMA`, `analyze_messages`, `dispatch_items`, `handle_approval_messages`, `is_trusted_sender`. Plus `gateways/dedup.py` and `gateways/pid.py` | Same module — this is the point |
| **Review** | `whatsapp/review.py` (**16 lines**) — thin re-export of the shared superset schema | `telegram/review.py` (**16 lines**) — same re-export |
| **Extractor** | `whatsapp/extractor.py` (159 lines) — auto-create issues/tasks with dedup | `telegram/extractor.py` — same pattern |

> **Corrected 2026-08-05** *(verified against `origin/main` @ `be8d398` — SPEC-006 §2, B2/B3/B5/B8)*. This table previously claimed both responders were **529 lines** — the size-parity claim that framed this document's entire divergence argument — and that WhatsApp handled **8** categories against Telegram's 15. Both were true of the pre-refactor code and are now wrong in a more important way: commit `c4954a0` extracted the shared core, so the responders are 285/271 and the category split **no longer exists**. There is one superset schema of **15 categories** serving both channels; the per-gateway `review.py` files are 16-line re-exports whose own docstring records that "this WhatsApp schema had lost 8 categories the dispatcher still handled".
>
> Consequence for this document: **the shared-core extraction it proposes as future work has already shipped.** See `docs/specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md` §0.3. What remains genuinely unbuilt is tenancy, webhook transport, and the Cloud API migration.
| **CLI** | `cli/whatsapp.py` (674 lines) — setup, webhook, template, monitor, review | `cli/telegram.py` — setup, monitor, review |
| **State** | `whatsapp_links` (phone_number → account_id, membership_id, role), `whatsapp_chat_links` (group_id → account_id, home_id) | `telegram_links` (chat_id → account_id, membership_id, role), `telegram_chat_links` (group_id → account_id, home_id) |

Both gateways share the **same internal message dict format**:

```python
{
    "id": str,           # gateway-specific message ID
    "timestamp": str,    # ISO 8601
    "jid": str,          # E.164 (WhatsApp) or chat_id (Telegram)
    "is_group": bool,
    "sender": str,       # E.164 or user_id
    "sender_name": str,
    "text": str | None,
    "has_media": bool,
    "media_path": str | None,
    "property_slug": str | None,
}
```

Both gateways share the **same AI services** (all gateway-agnostic):
- `services/ai/orchestrator.py` — `AIOrchestrator.ask()`, `structured_output()`, `stream()`
- `services/ai/context.py` — `assemble_context()` from 13+ data sources
- `services/ai/file_processor.py` — image, PDF, document processing
- `services/ai/roles.py` — role-based system prompts
- `services/ai/reports.py` — Situation Report, Estate Digest

Both gateways share the **same SPACE framework** for prioritization (Safety, Presence, Asset Protection, Compliance, Economy).

**The problem:** The responders have **diverged**. Telegram's responder includes richer intent handling (task_completion, issue_resolution, work_order_request, appointment_request, expense_log, book_addition, asset_addition, note_addition, vendor resolution) that WhatsApp lacks. Adding Twilio as a third copy would triple maintenance burden. The gateways live in `gateways/` with no shared coordination layer — each handles its own linking, its own RBAC resolution, its own message dedup, its own STOP/HELP handling.

## 2. Gap Analysis

| Gap | Current State | Omnichannel Fix |
|---|---|---|
| **Identity resolution** | Each gateway resolves identity independently (WhatsApp: phone_number lookup; Telegram: chat_id lookup) | Centralized `resolve_identity(from_id, channel)` — single source of truth for phone/chat → (account, member, role) |
| **Responder duplication** | WhatsApp and Telegram responders diverged; adding Twilio would be a third copy | Shared `core/responder.py` with channel-specific adapters |
| **Message dedup** | Per-gateway dedup (WhatsApp: message key; Telegram: update_id) | Cross-channel dedup — same user on WhatsApp + Telegram should not trigger duplicate actions |
| **Linking flow** | WhatsApp: `/link <code>` via CLI; Telegram: `/link <code>` via bot | Unified linking flow: user gets a short-lived code from web app, enters it on any channel |
| **STOP/HELP handling** | WhatsApp: pre-check in responder; Telegram: separate handler | Centralized keyword router — all channels funnel through the same STOP/HELP/opt-out manager |
| **Proactive alerts** | WhatsApp: template messages; Telegram: direct send | Unified alert dispatcher — channel-aware (template for WhatsApp outside 24h window, direct for Telegram/SMS) |
| **Rate limiting** | Per-gateway (WhatsApp: 30 msg/sec; Telegram: no hard limit) | Cross-channel rate limiting — aggregate per-account limits across all channels |
| **Delivery tracking** | WhatsApp: `get_message_status`; Telegram: message_id tracking | Unified delivery status API — `get_delivery_status(message_id, channel)` |
| **Media pipeline** | Per-gateway media download (WhatsApp: ID → URL; Telegram: file_id → URL) | Shared `media_resolver(channel, media_id)` — single download → save → process pipeline |
| **Webhook routing** | Each gateway has its own FastAPI endpoint | Single `/api/gateway/webhook` router that dispatches to channel-specific handlers |
| **Account switching** | WhatsApp: `@estate2 log ...` prefix; Telegram: not implemented | Unified account-switch keyword across all channels |
| **Channel preferences** | Not implemented | User sets preferred channel per notification type (e.g., Safety alerts → SMS/Voice, routine → Telegram) |

## 3. Proposed Capabilities

Priorities: **P0** = first to build *within this growth bet*, **P1** = fast follow, **P2** = later.

> **Corrected 2026-08-05** *(SPEC-006 §2, B4)*. P0 previously read "**launch-blocking**", which contradicts canon: `SAAS_PRD.md` §10 classifies chat gateways as a **Phase 4+ growth bet**, and `SAAS_PRD:186` states they "remain single-tenant/founder-only until made tenant-aware … they are **not part of the hosted MVP**." Nothing in this document blocks GA. These priorities order work *inside* the growth bet only — the same hedge `TELEGRAM_PRD.md` already carries for its own phase mapping ("a dependency floor, not committed scope").
>
> Note also that the first row below — **shared responder core** — **has already shipped** (`gateways/review_common.py`, commit `c4954a0`). See §2's corrected comparison table.

| Capability | Priority | Notes |
|---|---|---|
| **Shared responder core** | **P0** | Extract from WhatsApp + Telegram; Twilio plugs in as third adapter |
| **Centralized identity resolution** | **P0** | `resolve_identity(from_id, channel)` → (account, member, role) |
| **Unified webhook router** | **P0** | Single `/api/gateway/webhook` endpoint dispatching to channel handlers |
| **Cross-channel message dedup** | **P0** | Same user on multiple channels → single action |
| **Unified linking flow** | **P0** | One code works across all channels |
| **Channel adapters** | **P0** | WhatsApp (Cloud API), Telegram (Bot API), Twilio (SMS/MMS/Voice/WhatsApp) |
| **STOP/HELP/opt-out manager** | **P1** | Centralized keyword router, per-channel compliance |
| **Unified alert dispatcher** | **P1** | Channel-aware proactive messaging (template vs direct vs voice) |
| **Cross-channel rate limiting** | **P1** | Aggregate per-account limits |
| **Shared media pipeline** | **P1** | `media_resolver(channel, media_id)` — single download → save → process |
| **Channel preferences** | **P1** | User sets preferred channel per notification type |
| **Emergency escalation (Voice)** | **P1** | Twilio Voice call on Safety-priority events |
| **/help command (unified)** | **P2** | Channel-appropriate help text |
| **Delivery status (unified)** | **P2** | `get_delivery_status(message_id, channel)` |
| **Location sharing** | **P2** | Channel-specific location format normalization |
| **Voice transcription** | **P2** | Twilio `<Record>` → STT → issue/task |

## 4. Multi-Tenant Design

Every inbound message must resolve to **(account, member, role)** before any action. The omnichannel layer provides a single resolution function:

```python
def resolve_identity(from_id: str, channel: str) -> IdentityResult | None:
    """Resolve a sender identifier to (account, membership, role).

    Args:
        from_id: E.164 phone number (WhatsApp/Twilio) or chat_id (Telegram).
        channel: 'whatsapp', 'telegram', or 'twilio'.

    Returns:
        IdentityResult(account_id, membership_id, role, home_scopes) or None.
    """
```

**Identity sources:**
- **WhatsApp/Twilio:** `whatsapp_links` table — `phone_number` (E.164, hashed) → account_id, membership_id, role
- **Telegram:** `telegram_links` table — `chat_id` → account_id, membership_id, role
- **Unified linking:** A new `omnichannel_links` table can bridge channels — one code links a phone AND a chat_id to the same membership

**Group resolution:**
- `whatsapp_chat_links` — group_id → account_id, home_id
- `telegram_chat_links` — group_id → account_id, home_id
- Unified view: `omnichannel_chat_links` — (channel, group_id) → account_id, home_id

**Staff scoping & RBAC:** A linked phone/chat resolves to its membership, whose role and `membership_home_scopes` gate every action through `require_permission(user, current_account, action, target_home)`. No channel is a side door.

**Account-switch:** One phone/chat → one active account. For users in multiple accounts, a unified prefix (`@estate2 log ...`) works across all channels. Per-account numbers (Twilio Option B) is an Estate-tier upsell.

**Paid-plan gating:** Twilio channels are Pro/Estate only. Telegram remains free. WhatsApp Cloud API Developer tier is free for dev; Business tier is paid. Never offer Twilio on Free.

## 5. Architecture

### 5.1 Layered Design

```
┌─────────────────────────────────────────────────────────┐
│                   Omnichannel Router                     │
│  webhook_router → resolve_identity → dedup → RBAC →     │
│                  shared_responder → alert_dispatcher      │
└────────────────────────┬────────────────────────────────┘
                         │
          ┌──────────────┼──────────────┐
          │              │              │
   ┌──────┴──────┐ ┌────┴─────┐ ┌──────┴──────┐
   │ WhatsApp    │ │ Telegram │ │  Twilio     │
   │ Adapter     │ │ Adapter  │ │  Adapter    │
   └──────┬──────┘ └────┬─────┘ └──────┬──────┘
          │              │              │
   ┌──────┴──────┐ ┌────┴─────┐ ┌──────┴──────┐
   │ Cloud API   │ │ Bot API  │ │  Twilio API │
   └─────────────┘ └──────────┘ └─────────────┘
```

### 5.2 Channel Adapters

Each adapter implements a common `ChannelAdapter` Protocol:

```python
class ChannelAdapter(Protocol):
    """Protocol for channel-specific message ingestion and delivery."""

    def ingest(self, raw_payload: dict) -> list[InternalMessage] | None:
        """Convert raw channel payload to InternalMessage(s).
        Returns None if payload should be discarded (e.g., signature fail).
        """
        ...

    def deliver(self, message: OutboundMessage) -> DeliveryResult:
        """Send a message through this channel. Respects channel constraints
        (24h window, templates, rate limits)."""
        ...

    def get_name(self) -> str:
        """Return channel identifier: 'whatsapp', 'telegram', or 'twilio'."""
        ...
```

**WhatsApp adapter:** Wraps `WhatsAppBridge` Protocol. Handles Cloud API message format, 24h window checks, template selection, media ID resolution.

**Telegram adapter:** Wraps existing `TelegramBot` Protocol. Handles Bot API update format, inline keyboard responses, file downloads.

**Twilio adapter:** New implementation. Handles Twilio webhook format (POST params with `From`, `Body`, `MediaUrl0..9`), SMS/MMS/WhatsApp/Voice dispatch, STOP/HELP keyword pre-check, Twilio signature validation.

### 5.3 Shared Responder Core

Extracted from the common patterns in `whatsapp/responder.py` and `telegram/responder.py`:

```python
async def process_and_respond(
    session: Session,
    messages: list[InternalMessage],
    gateway: ChannelAdapter,
    property_slug: str | None = None,
) -> list[DeliveryResult]:
    """Shared message processing pipeline.

    Pipeline:
    1. Pre-check: STOP/HELP/keywords, dedup, identity validation
    2. Media download (if has_media)
    3. AI classification (review.analyze_messages)
    4. Intent dispatch (issue_resolution, task_completion, etc.)
    5. Context assembly (assemble_context)
    6. AI response generation (orchestrator.ask)
    7. Action execution (create issue, complete task, etc.)
    8. Reply delivery via gateway
    """
```

**Divergence handling:** Telegram's richer intents (task_completion, work_order_request, appointment_request, expense_log, book_addition, asset_addition, note_addition, vendor resolution) are registered as dispatch handlers. WhatsApp's current handlers (inventory_scan, approve/deny, general inquiry) remain. New intents are added to the shared registry — no per-gateway code needed.

### 5.4 Centralized Identity Resolution

```python
def resolve_identity(from_id: str, channel: str) -> IdentityResult | None:
    """Single source of truth for sender identity."""
    if channel == "telegram":
        return _resolve_telegram(from_id)    # telegram_links lookup
    elif channel == "whatsapp":
        return _resolve_whatsapp(from_id)    # whatsapp_links lookup
    elif channel == "twilio":
        return _resolve_twilio(from_id)      # whatsapp_links lookup (shared phone table)
    return None
```

**Unified linking:** A new `omnichannel_link_codes` table stores short-lived, single-use codes (hashed at rest, expiring, single-use — modeled on `ONBOARDING_AUTH_RBAC.md` §10). When a user sends `/link <code>` on any channel, the code resolves to (account, membership) and links the current `from_id` on that channel.

### 5.5 Unified Webhook Router

Single FastAPI endpoint:

```python
@router.post("/api/gateway/webhook")
async def webhook_router(request: Request):
    channel = request.headers.get("X-Channel")  # 'whatsapp', 'telegram', 'twilio'
    adapter = get_adapter(channel)
    payload = await request.json()  # or form data for Twilio
    messages = adapter.ingest(payload)
    if messages is None:
        raise HTTPException(400, "Invalid payload")
    return await process_and_respond(db_session, messages, adapter)
```

**Channel-specific validation:**
- WhatsApp: verify `hub.verify_token` on initial registration
- Telegram: no signature (Bot API tokens are in headers)
- Twilio: validate `X-Twilio-Signature` (HMAC-SHA1) — reject on mismatch

### 5.6 Cross-Channel Dedup

```python
def is_duplicate(message: InternalMessage) -> bool:
    """Check if this message (or equivalent) has already been processed.

    Strategy:
    - Same channel + same message ID → duplicate
    - Different channel + same user + message within 60s → likely duplicate
    """
```

### 5.7 Unified Alert Dispatcher

Proactive notifications (digests, alerts, reminders) go through a single dispatcher that selects the best channel:

```python
async def dispatch_alert(
    account_id: int,
    alert_type: str,       # 'safety', 'digest', 'reminder', 'status'
    message: str,
    priority: str,         # 'safety' (top of SPACE) → 'economy'
    channel_preference: str | None = None,  # user preference, if set
):
    """Send alert through the best available channel.

    Priority routing:
    - Safety/critical → Twilio Voice call (if phone linked), else SMS
    - High → WhatsApp template (if in 24h window) or direct
    - Medium → Telegram or WhatsApp (depending on user preference)
    - Low → Telegram (free channel)
    """
```

**Channel preferences:** New `user_channel_preferences` table:

```sql
CREATE TABLE user_channel_preferences (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    membership_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL,  -- 'safety', 'digest', 'reminder', 'status'
    preferred_channel TEXT NOT NULL,  -- 'telegram', 'whatsapp', 'sms', 'voice'
    secondary_channel TEXT,           -- fallback channel
    UNIQUE(account_id, membership_id, notification_type)
);
```

### 5.8 Shared Media Pipeline

```python
async def resolve_media(channel: str, media_id: str) -> str | None:
    """Download media from any channel and save to local storage.

    Returns local file path or None if media is invalid/expired.
    """
    if channel == "whatsapp":
        url = await whatsapp_media_api.get_url(media_id)  # 5-min expiry
    elif channel == "telegram":
        url = await telegram_file_api.get_url(media_id)
    elif channel == "twilio":
        url = f"https:{media_id}"  # Twilio provides direct URL in MediaUrl0..9

    # Common download → save → process pipeline
    return await download_and_save(url)
```

### 5.9 Rate Limiting

Cross-channel rate limiting per account:

```python
class AccountRateLimiter:
    """Track message rates per account across all channels.

    Limits:
    - WhatsApp: 30 msg/sec per phone number (Cloud API)
    - Twilio SMS: carrier-dependent (typically ~1 msg/sec per number)
    - Telegram: no hard limit, but aggressive polling triggers bans
    - Aggregate: configurable per-account cap (e.g., 100 msg/hour)
    """
```

Implemented as an in-memory sliding window with DB persistence for multi-instance deployments.

## 6. Security and Abuse

1. **Deny by default.** Unknown numbers/chats get a canned reply and are not run through AI. Only linked identities can command.
2. **Webhook validation per channel.** WhatsApp: `hub.verify_token`. Telegram: token in bot init (no per-request signature). Twilio: `X-Twilio-Signature` HMAC-SHA1 — reject on mismatch.
3. **STOP/HELP/opt-out.** All channels funnel through the same keyword router. Opt-out state stored in DB, checked before AI classification. Carriers and law require honoring `STOP`, `UNSUBSCRIBE`, `HELP`, `START`.
4. **PII handling.** Phone numbers (E.164) are PII. Hashed at rest in `whatsapp_links`. Never logged in plaintext.
5. **Token secrecy.** Access tokens, bot tokens, and verify tokens never logged. Stored encrypted in config.
6. **Per-phone/chat rate limits.** Cross-channel aggregate limits prevent abuse. Exceeding limits triggers a warning, not a hard block (graceful degradation).
7. **Replay detection.** Message IDs are unique per channel. Dedup prevents replay attacks.
8. **Media size cap.** Enforce consistent limits across channels (e.g., 25 MB max per file).

## 7. Unified Internal Message Format

Same dict format as current gateways, with an added `channel` field:

```python
{
    "id": str,              # gateway-specific message ID
    "timestamp": str,       # ISO 8601
    "jid": str,             # E.164 (WhatsApp/Twilio) or chat_id (Telegram)
    "is_group": bool,
    "sender": str,          # E.164 or user_id
    "sender_name": str,
    "text": str | None,
    "has_media": bool,
    "media_id": str | None, # channel-specific media identifier
    "media_path": str | None,  # resolved local path (after download)
    "property_slug": str | None,
    "channel": str,         # 'whatsapp', 'telegram', or 'twilio'
}
```

The `channel` field enables channel-aware behavior in the shared responder (e.g., template selection for WhatsApp, inline keyboards for Telegram, voice fallback for Twilio).

## 8. AI Orchestration

All gateway-agnostic — no changes needed:

- **`AIOrchestrator.ask()`** — role-based queries with system prompts from `roles.py`
- **`structured_output()`** — typed responses (REVIEW_SCHEMA, etc.)
- **`stream()`** — real-time response streaming
- **`assemble_context()`** — 13+ data sources with token budgeting
- **`file_processor.py`** — image, PDF, document processing
- **`assessors.py`** — SPACE framework prioritization
- **`reports.py`** — Situation Report, Estate Digest

**Channel-aware system prompts:** The orchestrator receives the `channel` field and can adjust tone/format (e.g., shorter messages for SMS, rich formatting for Telegram/WhatsApp).

**Intent dispatch registry:** The shared responder maintains a registry of intent handlers. Each handler is a function registered once, callable from any channel:

```python
INTENT_HANDLERS = {
    "issue_resolution": handle_issue_resolution,
    "task_completion": handle_task_completion,
    "work_order_request": handle_work_order_request,
    "appointment_request": handle_appointment_request,
    "expense_log": handle_expense_log,
    "book_addition": handle_book_addition,
    "asset_addition": handle_asset_addition,
    "note_addition": handle_note_addition,
    "vendor_resolution": handle_vendor_resolution,
    "inventory_scan": handle_inventory_scan,
    "approve_deny": handle_approve_deny,
    "general_inquiry": handle_general_inquiry,
}
```

## 9. Database Schema Changes

### New tables

```sql
-- Unified link codes (single code works across all channels)
CREATE TABLE omnichannel_link_codes (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL,       -- hashed short-lived code
    code_expires_at DATETIME NOT NULL,
    account_id INTEGER NOT NULL,
    membership_id INTEGER NOT NULL,
    used BOOLEAN DEFAULT FALSE,
    UNIQUE(code_hash)
);

-- Channel preferences
CREATE TABLE user_channel_preferences (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    membership_id INTEGER NOT NULL,
    notification_type TEXT NOT NULL,  -- 'safety', 'digest', 'reminder', 'status'
    preferred_channel TEXT NOT NULL,  -- 'telegram', 'whatsapp', 'sms', 'voice'
    secondary_channel TEXT,
    UNIQUE(account_id, membership_id, notification_type)
);

-- Cross-channel dedup
CREATE TABLE omnichannel_dedup (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,
    message_id TEXT NOT NULL,
    sender_id TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(channel, message_id),
    UNIQUE(channel, sender_id, created_at)  -- 60s window dedup
);

-- Unified chat links (replaces whatsapp_chat_links + telegram_chat_links views)
CREATE TABLE omnichannel_chat_links (
    id INTEGER PRIMARY KEY,
    channel TEXT NOT NULL,           -- 'whatsapp', 'telegram', 'twilio'
    group_id TEXT NOT NULL,          -- group JID or chat_id
    group_name TEXT,
    account_id INTEGER NOT NULL,
    home_id INTEGER,
    UNIQUE(channel, group_id)
);
```

### Existing tables — no changes needed

- `whatsapp_links` — keep as-is (phone_number → membership)
- `telegram_links` — keep as-is (chat_id → membership)
- `whatsapp_chat_links` — keep as-is (deprecated view over `omnichannel_chat_links`)
- `telegram_chat_links` — keep as-is (deprecated view over `omnichannel_chat_links`)
- `whatsapp_messages` — keep as-is (add `channel` column for cross-channel queries)
- `telegram_messages` — keep as-is (add `channel` column)

### Config table changes

Remove bridge-related config keys. Add omnichannel config:

```sql
-- Remove
-- config_key = 'whatsapp_bridge_pid'
-- config_key = 'whatsapp_bridge_url'

-- Add
-- config_key = 'whatsapp_phone_number_id'
-- config_key = 'whatsapp_access_token'
-- config_key = 'whatsapp_verify_token'
-- config_key = 'whatsapp_waba_id'
-- config_key = 'telegram_bot_token'
-- config_key = 'telegram_bot_username'
-- config_key = 'twilio_account_sid'
-- config_key = 'twilio_auth_token'
-- config_key = 'twilio_phone_number'
-- config_key = 'twilio_messaging_service_sid'
-- config_key = 'omnichannel_webhook_url'
```

## 10. CLI Commands

Unified CLI surface:

| Command | Description |
|---|---|
| `mihomes gateway setup` | Configure channel credentials (interactive) |
| `mihomes gateway status` | Show status of all configured channels |
| `mihomes gateway link` | Start unified linking flow (generate code) |
| `mihomes gateway unlink` | Unlink current channel identity |
| `mihomes gateway monitor` | Start omnichannel webhook listener |
| `mihomes gateway review` | Batch AI analysis of pending messages |
| `mihomes gateway extract` | Auto-create issues/tasks from analyzed messages |
| `mihomes gateway send` | Send message to a linked phone/chat |
| `mihomes gateway send-group` | Send to a linked group |
| `mihomes gateway webhook` | Verify/register webhook endpoint |
| `mihomes gateway template` | Manage WhatsApp/Twilio template messages |
| `mihomes gateway preferences` | Set channel preferences for notifications |

**Removed commands:** `bridge-start`, `bridge-stop`, `bridge-status`, `pair`, `groups`, `autostart` (all Baileys-specific).

## 11. Supervision and Monitoring

No bridge process. Watchdog supervises the Python monitor process that handles webhook listening (Telegram long-poll) or webhook server (WhatsApp/Twilio FastAPI endpoint).

**Health checks:**
- WhatsApp: Cloud API returns HTTP status codes (200 = OK, 429 = rate limited, 500 = error)
- Telegram: `/getMe` health check, polling timeout monitoring
- Twilio: webhook endpoint health, SMS delivery status callbacks

**Metrics:**
- Messages processed per channel per account
- Errors by channel and type
- API latency by channel
- Delivery success rate by channel
- Opt-out rate (STOP per 100 recipients)
- Cost per active account (Twilio)

## 12. Performance Requirements

| Metric | Target | Notes |
|---|---|---|
| Message ingestion latency | < 2s (p95) | From webhook receipt to AI dispatch |
| Reply latency | < 10s (p95) | From message receipt to delivered reply |
| WhatsApp rate limit | 30 msg/sec per number | Client-side queuing |
| Twilio SMS rate | ~1 msg/sec per number | Carrier-dependent |
| Media download | < 5s (p95) | Including URL resolution |
| Media size cap | 25 MB | Consistent across channels |
| Cross-channel dedup window | 60s | Same user, different channels |

## 13. Testing Strategy

**Unit tests:**
- `test_channel_adapters.py` — mock each adapter's `ingest()` and `deliver()`
- `test_identity_resolution.py` — resolve_identity with linked/unlinked identities
- `test_responder_core.py` — shared pipeline with mock gateway
- `test_dedup.py` — cross-channel dedup logic
- `test_media_pipeline.py` — resolve_media for each channel
- `test_rate_limiter.py` — AccountRateLimiter sliding window

**Integration tests:**
- Mock webhook handler for each channel
- Template formatting (WhatsApp/Twilio)
- 24h window logic
- STOP/HELP keyword routing
- Channel preference selection

**E2E tests:**
- Meta test number for real WhatsApp Cloud API calls
- Telegram test bot for real Bot API calls
- Twilio test credentials for real SMS/MMS

**Load tests:**
- 50 msg/min across 10 groups, mixed channels
- Cross-channel dedup under concurrent messages
- Rate limiter under burst traffic

## 14. Deployment Considerations

### 14.1 Local Development

Single Python process. No bridge. Telegram long-poll + FastAPI webhook endpoint for WhatsApp/Twilio. Polling fallback for local Twilio development (use ngrok or similar for webhook URL).

### 14.2 Hosted Production

- **WhatsApp:** Single Cloud API connection per WABA. Webhook on FastAPI endpoint. S3 for media storage.
- **Telegram:** Single bot, long-poll or webhook.
- **Twilio:** Messaging Service for shared-number mode. Dedicated numbers for Estate per-tenant mode. Webhook on public FastAPI endpoint.

### 14.3 Migration Path

1. Extract shared responder core from WhatsApp + Telegram responders
2. Add `ChannelAdapter` Protocol and implement WhatsApp + Telegram adapters
3. Add centralized identity resolution and webhook router
4. Add cross-channel dedup and rate limiter
5. Add Twilio adapter (when Twilio phase begins)
6. Deprecate per-gateway CLI commands in favor of unified `mihomes gateway`

## 15. Documentation Requirements

- Gateway adapter implementation guide (how to add a new channel)
- Channel adapter Protocol reference
- Shared responder core API reference
- Identity resolution and linking flow documentation
- Webhook router configuration (per-channel)
- Channel preferences setup guide
- STOP/HELP/opt-out compliance guide
- Per-channel rate limit configuration
- Emergency escalation (Voice) setup guide
- Cost monitoring and alerting guide (Twilio)

## 16. Phasing

> **These are STAGE numbers internal to this growth bet, not product phases** *(corrected
> 2026-08-05 — SPEC-006 §2, B4)*. Product phase numbering is **canon across the whole doc set**
> (`SAAS_PRD.md` §10, `docs/specs/README.md`) and runs 0–4, ending at GA. This table previously
> presented its own "Phase 0–4" using the same numerals for entirely different work — so its
> "Phase 0" collided with canon Phase 0 (landing + waitlist, which contains **zero** gateway code).
> Read every row below as *Stage N of the Phase 4+ gateway growth bet*.
>
> **Stage 0 has already shipped** as `gateways/review_common.py` (commit `c4954a0`) — with the seam
> named `GatewayAdapter`, not `ChannelAdapter`, and living at `gateways/review_common.py`, not
> `core/responder.py`. Stages 1 and part of 2 are specced in
> `docs/specs/SPEC-006-gateways-tenancy-webhook-cloud-api.md`; the Twilio stages are SPEC-007.

| Stage | Work | Dependencies |
|---|---|---|
| **0: Responder core** — ✅ **SHIPPED** (`c4954a0`) | Extract shared core from WhatsApp + Telegram. Add the adapter seam. Implement WhatsApp + Telegram adapters. Add unit tests. | Current WhatsApp + Telegram responders |
| **1: Identity + routing** — specced as SPEC-006 | Centralized sender→account resolution. Webhook router. Cross-channel dedup. Unified linking flow. | Stage 0; product Phases 1–2 (tenancy, memberships) |
| **2: Compliance + preferences** | STOP/HELP/opt-out manager. Channel preferences table + alert dispatcher. Rate limiter. | Stage 1 |
| **3: Twilio adapter** — SPEC-007 | Implement Twilio adapter (SMS/MMS). Add Twilio to the shared core. STOP/HELP for Twilio. | Stages 0–2; product Phase 3 (billing/entitlements) |
| **3-4: Twilio advanced** | WhatsApp Business channel via Twilio. Voice escalation. Per-account numbers. Media pipeline unification. | Stage 3, A2P 10DLC registration |
| **4: Channel maturity** *(renamed from "GA" — see the note above; product GA is Phase 4 and does not wait on any of this)* | Multi-language support. Location sharing. Voice transcription. Unified `/help`. Delivery status API. | Stages 0–3 |

## 17. Open Questions & Risks

- **Responder-core refactor scope.** WhatsApp and Telegram responders have diverged significantly. Unifying them carries regression risk to two live gateways. Sequence carefully with comprehensive tests.
- **A2P 10DLC + template approval.** Twilio SMS and WhatsApp Business via Twilio require registrations that take weeks. Start early; don't let GA depend on pending approvals.
- **Cross-channel dedup window.** 60s may be too short for users who intentionally message on multiple channels (e.g., "also sending via SMS"). Consider a longer window or user-configurable dedup.
- **Account-switch at scale.** One phone → one account is a real limitation. The `@estate2` prefix works but is friction. Per-account numbers (Option B) solves it but costs more.
- **Media storage.** Local vs S3 for unified media pipeline. Local is simpler for single-instance; S3 needed for multi-instance deployments.
- **Voice transcription provider.** Twilio built-in vs dedicated STT (accuracy vs cost). Must sit behind a Protocol per the provider abstraction pattern.
- **Telegram has no phone number.** Unlike WhatsApp and Twilio, Telegram users are identified by `user_id`/`chat_id`, not phone numbers. This means: (a) Telegram users can't receive Voice escalation calls, (b) Telegram users can't use the shared phone-based linking flow without also linking a phone, (c) cross-channel dedup between Telegram + WhatsApp requires the user to have linked both channels.
- **Twilio WhatsApp vs native WhatsApp.** Twilio's WhatsApp channel uses the WhatsApp Business API under the hood (via Meta). It shares the same 24h window and template rules as native Cloud API. The question is whether to use Twilio's WhatsApp channel (consolidated billing, shared number) or native Cloud API (direct, more control). Recommendation: use native Cloud API for WhatsApp, Twilio for SMS/MMS/Voice only.

---

*Cited source files:* `src/mihomes/services/gateways/whatsapp/{protocol,client,responder,review,extractor}.py`, `src/mihomes/services/gateways/telegram/{client,responder}.py`, `src/mihomes/services/ai/{orchestrator,context,file_processor,roles,reports}.py`, `src/mihomes/cli/{whatsapp,telegram}.py`, `docs/product/TELEGRAM_PRD.md`, `docs/product/WHATSAPP_GATEWAY_PRD.md`, `docs/product/TWILIO_PRD.md`. *Cross-refs:* `../architecture/MULTITENANCY.md`, `ONBOARDING_AUTH_RBAC.md`, `PRICING_AND_PACKAGING.md`, `PRD.md`.