# Twilio Gateway — Product Requirements Document

**Purpose:** Add a third MiHomes chat gateway built on Twilio — SMS/MMS, the official WhatsApp Business API, and Voice — as the reliable, compliant, no-app-required channel for owners and staff.

**Status: Draft — 2026-07-27**

---

## 1. Rationale & Positioning

MiHomes already has two chat gateways that let owners and staff log issues, add and complete tasks, query status via the AI estate manager, and receive digests:

- **WhatsApp (Baileys)** — an unofficial Node bridge (`bridge/`, polled over HTTP at `http://localhost:7867` by `src/mihomes/services/gateways/whatsapp/client.py`). It is free, but fragile: pairing has been **blocked since the wacli-integration branch** (`tasks/todo.md` — *"Baileys 'cannot link device' error"*, listed under Known Issues), and unofficial WhatsApp automation risks number blocking.
- **Telegram** — native Bot API, plain `urllib` (`src/mihomes/services/gateways/telegram/client.py`). Free and reliable, but requires every participant to install Telegram and be added to a group.

Both share the same pipeline shape: **client → extractor/review → responder**, supervised by a polling monitor (`mihomes whatsapp monitor`, `mihomes telegram monitor`).

**Twilio unlocks channels neither gateway can reach:**

| Need | Why the current gateways fall short | Twilio answer |
|------|-------------------------------------|---------------|
| Reach a housekeeper/handyman who won't install an app | Telegram needs an app; Baileys needs the user on WhatsApp *and* in a group | **SMS** — works on any phone, zero install |
| Photo of a leak from a phone with no smart app | — | **MMS** — image → issue with attachment |
| Compliant, supported WhatsApp | Baileys is unofficial; pairing/blocking issues are a real pain point | **WhatsApp Business API** via Twilio — approved templates, no pairing hacks |
| Call-in status / emergency escalation | No voice channel exists | **Voice** — TwiML call-in and outbound Safety-alert calls |

**Positioning: Twilio is the "reliable, compliant, no-app-required" gateway.** Telegram stays the free power-user channel; Baileys stays a free best-effort option; Twilio is the paid channel we can put in front of a paying customer's staff and stand behind.

### 1.1 Channel comparison

| | Telegram | WhatsApp (Baileys) | Twilio SMS/MMS | Twilio WhatsApp | Twilio Voice |
|---|---|---|---|---|---|
| **App required** | Telegram app | WhatsApp app | **None** | WhatsApp app | **None** (any phone) |
| **Cost model** | Free | Free | Per segment / per MMS | Per conversation | Per minute |
| **Media** | Photos/docs/video | Photos/docs | **MMS images** (US/CA) | Photos/docs | Voice notes (transcribed) |
| **Reliability / compliance** | High | **Low — unofficial, pairing blocked** | High (needs A2P 10DLC) | **High — official API** | High |
| **Inbound transport** | Polled `getUpdates` (short-poll today; webhook planned — `TELEGRAM_PRD.md` §5) | HTTP poll of bridge | **Webhook** | **Webhook** | **Webhook (TwiML)** |
| **Best-fit user** | Tech-comfortable owner/staff | Existing WhatsApp households | Any staff, any phone | Compliant WhatsApp households | Emergency / hands-free |

---

## 2. Architecture — Mirror the Existing Gateway Pattern

The Twilio gateway is a new module `src/mihomes/services/gateways/twilio/` that mirrors the sibling gateways file-for-file:

```
src/mihomes/services/gateways/twilio/
    protocol.py     # Protocol interface (mirrors whatsapp/protocol.py)
    client.py       # Twilio REST + webhook signature validation (mirrors whatsapp/client.py, telegram/client.py)
    extractor.py    # dedup + auto-create for background intake (mirrors whatsapp/extractor.py)
    review.py       # AI classification into actionable items (mirrors whatsapp/review.py)
    responder.py    # intent → action + reply (mirrors whatsapp/responder.py, telegram/responder.py)
    voice.py        # TwiML generation for Voice call-in / escalation (Twilio-specific)
```

### 2.1 The key architectural difference: webhooks, not polling

WhatsApp and Telegram are **pull** today — a monitor loop calls `client.get_messages()` / `client.get_updates()` on an interval (`mihomes whatsapp monitor`, `mihomes telegram monitor`); note `TELEGRAM_PRD.md` §5 moves hosted Telegram to a webhook too, so "webhook-first" is the shared hosted-gateway direction, and Twilio is webhook-*only*. Inbound SMS, WhatsApp messages, delivery status, and voice events arrive as **HTTP POST webhooks** from Twilio. In the multi-tenant SaaS these land on **FastAPI routes** in the existing web app (`src/mihomes/api/`), served at **`app.mihomes.ai`** (the apex is marketing — `GTM_LAUNCH_PLAN.md` §5), e.g.:

```
POST /webhooks/twilio/sms          # inbound SMS/MMS (TwiML response)
POST /webhooks/twilio/whatsapp     # inbound WhatsApp Business message
POST /webhooks/twilio/voice        # inbound call → TwiML <Gather>/<Say>
POST /webhooks/twilio/status       # delivery/read status callbacks
```

Each webhook handler: (1) **validates `X-Twilio-Signature`** (§5), (2) resolves the sending phone number to a MiHomes account/member (§4), (3) normalizes the payload into the **same internal message dict** the other gateways use, and (4) hands it to the shared responder. Twilio replaces the polling monitor with an inbound HTTP surface; there is **no long-running poll process** for Twilio.

### 2.2 The normalized message dict is the integration seam

Telegram already proves the pattern: `TelegramClient.normalize_update()` converts a raw update into the exact dict shape WhatsApp emits — `id, timestamp, jid, isGroup, sender, senderName, text, hasMedia, mediaPath, propertySlug` — *"so review.py and responder.py work unchanged"* (`telegram/client.py:108-165`). Twilio does the same: a webhook payload (`From`, `Body`, `MediaUrl0`, `NumMedia`, …) is normalized into that dict, with the Twilio phone number in the `sender` slot and the resolved property in `propertySlug`.

### 2.3 Shared AI brain — do NOT re-implement intent logic

**Finding (verified against the code, 2026-07-27):** the WhatsApp and Telegram responders **duplicate substantial intent→action logic.** `whatsapp/responder.py` and `telegram/responder.py` each define their own `_ai_response`, `_issue_expert_reply`, `_answer_question`, `_resolve_staff_slug`, `_parse_event_date`, `_strip_markdown`, `_handle_approval_message`, `handle_inventory_scan`, and the per-category dispatch loop (issue/task/question/pto_request/supply_need/vendor_activity). Telegram has since grown richer — it additionally dispatches `task_completion`, `issue_resolution`, `work_order_request`, `appointment_request`, `expense_log`, `book_addition`, `asset_addition`, and `note_addition`, plus a `_resolve_vendor` helper, none of which exist in the WhatsApp responder — the two have **diverged**, which is exactly the drift a third copy would worsen.

**Recommendation:** before Twilio adds a third responder, extract the channel-agnostic core into a shared module, e.g. `src/mihomes/services/gateways/core/responder.py`, exposing:

```python
def process_and_respond(session, messages, reply, property_slug=None) -> dict: ...
```

where `reply` is a small channel adapter (`send_text(target, text)`, `send_media(...)`). The core owns classification (`review.analyze_messages`), the intent dispatch loop, and all DB writes. Each gateway keeps only its transport (`client.py`) and a thin `reply` adapter. Twilio then reuses the brain instead of copying it. The intent classifier and the AI advisor are **already shared** downstream — `review.py` calls `get_provider()` (`src/mihomes/services/ai/provider.py`) and the responder calls `orchestrator.ask(...)` with SPACE-aware roles — so only the dispatch loop needs lifting. This refactor is a prerequisite for the Twilio responder, not optional cleanup.

### 2.4 Protocol interface

Twilio implements a Protocol modeled on `src/mihomes/services/gateways/whatsapp/protocol.py` (which already anticipates templates and webhooks — `send_template`, `register_webhook`), extended for inbound parsing and voice:

```python
"""Twilio gateway protocol — SMS/MMS, WhatsApp Business, and Voice."""

from typing import Protocol


class TwilioGateway(Protocol):
    """Protocol for the Twilio messaging/voice gateway.

    Business logic depends only on this Protocol — never on the Twilio SDK
    (mirrors AIProvider in ai/provider.py and EmailProvider/BillingProvider
    in ../architecture/BILLING_AND_EMAIL.md).
    """

    def send_message(self, to: str, body: str, media_urls: list[str] | None = None) -> dict:
        """Send an SMS/MMS or WhatsApp message. Returns {message_sid, status}."""
        ...

    def send_template(self, to: str, template_name: str, parameters: dict | None = None) -> dict:
        """Send a pre-approved WhatsApp Business template (outside the 24h window)."""
        ...

    def parse_webhook(self, headers: dict, form: dict, url: str) -> dict | None:
        """Validate X-Twilio-Signature, then normalize an inbound webhook into the
        internal message dict (id, timestamp, jid, sender, senderName, text,
        hasMedia, mediaPath, propertySlug). Returns None if the signature is invalid."""
        ...

    def build_voice_response(self, intent: str, context: dict) -> str:
        """Return TwiML (XML) for a voice call — <Say>/<Gather>/<Record>."""
        ...

    def get_message_status(self, message_sid: str) -> dict:
        """Delivery status: queued, sent, delivered, read, failed, undelivered."""
        ...
```

---

## 3. Channels & Capabilities

Priorities: **P0** = launch-blocking for the gateway, **P1** = fast follow, **P2** = later.

### 3.1 SMS / MMS
| Capability | Priority | Notes |
|---|---|---|
| Log issue from text ("pool pump making noise") | **P0** | Routes through shared `review.analyze_messages` |
| Log issue with **MMS photo** of a leak → issue + attached image | **P0** | `MediaUrl0` downloaded → saved as Document, exactly like `whatsapp/responder.py:468-495` |
| Add / complete task | **P0** | Reuses task + task_completion dispatch |
| Status query answered by AI advisor | **P1** | `orchestrator.ask(role="estate_manager")` |
| Receive alerts / digests | **P1** | Outbound `send_message`; digest reuses `services/ai/reports.py` |
| STOP / HELP keyword handling | **P0** | Compliance requirement — §5 |

### 3.2 WhatsApp Business (via Twilio)
| Capability | Priority | Notes |
|---|---|---|
| All SMS capabilities, over official WhatsApp | **P0** | Replaces Baileys for paying customers |
| Rich media (photos, docs) | **P0** | Same Document pipeline |
| Proactive alerts via **approved templates** | **P1** | Required outside the 24h session window — §5 |

### 3.3 Voice
| Capability | Priority | Notes |
|---|---|---|
| Call-in status ("press 1 for open issues at Belle Estate") | **P2** | TwiML `<Gather>` → `orchestrator.ask` → `<Say>` |
| Voice-note intake → transcribe → issue/task | **P2** | `<Record>` → transcription provider → `review.analyze_messages` |
| **Emergency escalation call to owner** on a Safety alert | **P1** | See §3.4 |

### 3.4 Emergency escalation ties to SPACE

MiHomes prioritizes with the **SPACE framework — Safety is the top priority** (`CLAUDE.md`). Voice is the natural escalation for a Safety-priority event (gas leak, flood, intrusion, fire alarm): when an inbound issue or alert is classified Safety/critical, the gateway places an **outbound Twilio Voice call** to the owner (and optionally SMS fallback) with a spoken summary via TwiML `<Say>`, retrying until answered. This is the one capability where a channel that *rings a phone* materially beats chat — no other gateway can force attention.

---

## 4. Multi-Tenant Design — Phone ↔ Account Mapping

Per `../architecture/MULTITENANCY.md`, every row is scoped to an account; the gateway must resolve an inbound phone number to **(account, member, role)** before it can act. Two options:

| Option | How | Pros | Cons |
|---|---|---|---|
| **A. Shared number + verified linking** | One Twilio number (or Messaging Service) for all tenants. A member links their phone by texting back a **short-lived, single-use code** MiHomes SMS'd them from the web app ("Connect phone"). This is a **new** verification flow — `ONBOARDING_AUTH_RBAC.md` has no SMS step today; model it on that doc's invite-token discipline (§10: hashed at rest, expiring, single-use) and on the Telegram `/link <code>` flow (`TELEGRAM_PRD.md` §4.1). Number → membership stored per account. | Cheap (one number), simple provisioning, works on Pro | An unlinked/unknown number can't command; a phone maps to exactly one account at a time → **account-switch problem** |
| **B. Per-account numbers** | Provision a dedicated Twilio number (or **subaccount**) per tenant. Inbound number *is* the account key. | Clean isolation, no linking step, own caller ID, matches Estate expectations | **Per-number monthly cost per tenant** — only viable on Estate |

**Recommendation: A for Pro, B for Estate.** Default to the shared-number + verification model (one number, lowest cost, TCPA-clean opt-in). Offer per-account numbers/subaccounts as an **Estate** upsell where isolation and branded caller ID matter.

- **Staff scoping & RBAC:** a linked staff phone resolves to its membership, whose role and `membership_property_scopes` gate every action through the same `require_permission(user, current_account, action, target_property)` check as web routes (`ONBOARDING_AUTH_RBAC.md` §9.4) — SMS is not a side door around the capability matrix. Unscoped/unpermitted requests get the same 404-style "not found" (never confirm existence). The current responder's fuzzy staff-phone match (`whatsapp/responder.py:327-358`) is a single-tenant convenience for *reporter attribution only* — in the multi-tenant gateway, identity comes from the verified link, never from fuzzy phone matching. (Twilio does populate the phone reliably, unlike Telegram, which has no phone.)
- **Account-switch problem (Option A):** one phone → one active account. A person who belongs to two accounts must either use a keyword prefix (`@estate2 log ...`) or we require per-account numbers (Option B). Document as an Estate-tier reason to choose B.
- **Cost gating:** phone numbers and per-message fees are a **real per-tenant cost**, so Twilio channels are **paid-plan only**. Per `PRICING_AND_PACKAGING.md` (freemium line = *household free, business paid*), SMS/Voice are **Pro/Estate**; per-account numbers/subaccounts are **Estate**. **Never offer Twilio on Free** — a free household would carry ongoing carrier cost with no revenue.

---

## 5. Webhooks, Security & Compliance

These are real gotchas, not nice-to-haves:

1. **Webhook signature validation (P0).** Every inbound Twilio POST carries `X-Twilio-Signature` (HMAC-SHA1 of the full URL + sorted POST params, keyed by the Auth Token). The FastAPI handler **must validate it and reject on mismatch** before doing anything — this is the only thing stopping a forged POST from creating issues or triggering calls. Implemented in `client.parse_webhook()`, which returns `None` on failure.
2. **Only linked numbers can command (P0).** After signature validation, resolve `From` to a linked membership (§4). Unknown numbers get a canned "This number isn't linked to a MiHomes account" reply and are **not** run through the AI or allowed to write.
3. **STOP / HELP / opt-out (P0).** Carriers and law require honoring `STOP`, `UNSUBSCRIBE`, `HELP`, `START`. Twilio's Messaging Service Advanced Opt-Out can auto-handle these, but MiHomes must **track opt-out state** and never send to an opted-out number. Handle these keywords *before* AI classification (same pre-check slot as the APPROVE/DENY handler in `whatsapp/responder.py:198`).
4. **A2P 10DLC registration (P0 for US SMS).** US application-to-person SMS over 10-digit long codes requires **A2P 10DLC brand + campaign registration** (TCPA). Unregistered traffic is filtered/blocked by carriers. **Registration takes days-to-weeks and must start early** (§8).
5. **WhatsApp Business rules (P0 for that channel).** Business-initiated messages outside the **24-hour customer-service window** require **pre-approved message templates** (`send_template`). Template approval is a Meta review process with lead time. Free-form replies are only allowed inside the 24h window opened by a user's inbound message.
6. **Number / Messaging Service setup.** Use a **Twilio Messaging Service** (sender pool + opt-out + sticky sender) rather than a bare number, so scaling senders and opt-out handling are centralized.

---

## 6. Cost Model

Twilio is **pay-per-use** — the structural reason it is paid-plan-gated, in contrast to Telegram (free) and Baileys (free but fragile). Illustrative only:

> **ILLUSTRATIVE — validate against current Twilio pricing before GA.** Figures are US, rough order-of-magnitude.

| Item | Illustrative unit cost | Notes |
|---|---|---|
| Phone number | ~$1.15 / month | Per number; per-tenant under Option B |
| Outbound SMS | ~$0.0079 / segment | 160 GSM-7 chars per segment; long messages = multiple |
| Inbound SMS | ~$0.0079 / message | |
| MMS (US/CA) | ~$0.02 / message | Image intake |
| WhatsApp conversation/message | ~$0.005–0.08 | Varies by category (service vs utility vs marketing); note Meta has been shifting WhatsApp pricing from per-conversation toward per-template-message — re-verify the current model, not just the rates |
| Voice (outbound) | ~$0.014 / minute | Escalation calls |
| A2P 10DLC | registration + small monthly campaign fee | One-time + recurring |

**ILLUSTRATIVE per-account monthly (Pro, shared number, light use):** ~$1 number share + ~50 messages × $0.008 ≈ **under $1.50/account/month** — comfortably inside the Pro price point (**$20/mo / $200/yr — PLACEHOLDER**, per `PRICING_AND_PACKAGING.md` §1; both figures pending validation). Estate (per-account number + heavier use + voice) runs higher and is priced accordingly. Note `PRICING_AND_PACKAGING.md` §3.1 currently has **no Twilio/SMS entitlement keys** — add e.g. `sms_gateway: bool`, `dedicated_number: bool` there when this ships. Track **cost per active account** as a guardrail metric (§8).

---

## 7. Provider Abstraction

Twilio must sit **behind the gateway Protocol** (§2.4) so business logic never imports the Twilio SDK directly. This is the same discipline already codified across MiHomes:

- **AI:** `AIProvider` Protocol + `get_provider()` factory (`src/mihomes/services/ai/provider.py`).
- **Email/Billing:** *"All third-party services must be accessed through internal provider interfaces… No business logic may depend directly on vendor SDKs"* (`../architecture/BILLING_AND_EMAIL.md`), which itself cites the AI layer and calendar gateway as precedent.

`TwilioGateway` (client.py) is the **only** module that imports `twilio` (or hand-rolls REST like the Telegram client's plain `urllib`). The shared responder core (§2.3), review, and CLI depend on the Protocol, never on Twilio types. This keeps a future swap (e.g. to a different SMS/voice vendor) a one-file change and keeps the responder testable with a fake gateway.

---

## 8. Phasing & Success Metrics

Mapped to the product phases (Phase 0 landing → 1 multitenant → 2 onboarding/RBAC → 3 billing → 4 GA):

| Phase | Twilio work |
|---|---|
| **0–1** | None in product. **Start A2P 10DLC brand + WhatsApp Business/template registration paperwork now** — weeks of lead time, gates everything downstream. |
| **2** | Phone-linking/verification flow built alongside onboarding/invites — a **new** flow (there is no SMS-verify in `ONBOARDING_AUTH_RBAC.md` today) reusing its invite-token discipline and the membership/scope model (§4). |
| **3 (realistic earliest)** | Ship SMS/MMS on **Pro** behind billing/entitlements; webhook routes + signature validation; shared responder-core refactor (§2.3); STOP/HELP + opt-out. Twilio is a paid channel, so it can't precede billing. |
| **3–4** | WhatsApp Business channel; Voice escalation; per-account numbers/subaccounts on **Estate**. |

**Success metrics:**
- Messages handled per active account (SMS / WhatsApp / voice).
- Issues created via SMS/MMS (esp. **MMS-photo issues** — the no-app win).
- Emergency escalation calls delivered / answered (Safety-priority).
- **Opt-out rate** (STOP per 100 recipients) — health & compliance signal.
- **Cost per active account** vs plan margin — the gating guardrail.
- Delivery success rate (delivered ÷ sent) per channel.

---

## 9. Open Questions & Risks

- **Registration timelines.** A2P 10DLC and WhatsApp Business/template approval can take weeks and can be rejected — the critical-path risk. Start early; don't let GA depend on a pending approval.
- **Per-tenant number cost at scale.** Option B (per-account numbers) doesn't pencil out below Estate. **Do not offer Twilio on Free** under any option.
- **Account-switch (Option A).** One phone → one account is a real limitation for people in multiple estates; may force Option B for those users.
- **International SMS.** Pricing, deliverability, and regulatory rules vary sharply by country; scope initial launch to US/CA and treat international as a later, per-region effort.
- **Voice transcription provider.** Twilio built-in vs a dedicated STT (accuracy vs cost) — decide before Voice intake (P2); must also sit behind a Protocol per §7.
- **Responder-core refactor scope.** WhatsApp and Telegram responders have diverged (§2.3); unifying them is a prerequisite and carries regression risk to two live gateways — sequence it carefully with tests.

---

*Cited source files:* `src/mihomes/services/gateways/whatsapp/{protocol,client,extractor,review,responder}.py`, `src/mihomes/services/gateways/telegram/{client,responder}.py`, `src/mihomes/services/ai/provider.py`, `src/mihomes/cli/{whatsapp,telegram}.py`, `tasks/todo.md`. *Cross-refs:* `../architecture/MULTITENANCY.md`, `../architecture/BILLING_AND_EMAIL.md`, `ONBOARDING_AUTH_RBAC.md`, `PRICING_AND_PACKAGING.md`.
