# MiHomes — Project State & Next Actions

Last updated: 2026-07-29

---

## ACTIVE: Hardening Build Loop (branch `hardening-build`)

> Harness: `tasks/build-loop.md` · Spec: `tasks/hardening-spec.md` (v2, adversarially verified) · run via `/loop tasks/build-loop.md`.
> Mirror of the §4 DAG — authoritative checkboxes live in `build-loop.md`; this is the at-a-glance view. Group `[x]` = committed + full suite green.

- [ ] **G0 — Stop-the-bleeding (P0)**: D5 demo boot · D7+D8 watchdog · D6+H34+M45 uploads · D1 backup/restore · D3+H10+M32+M33 tools.py · M44 query_inventory · H8+H9 model · H11 round-limit
- [ ] **G-R4 — Reconciliation migration** (extra-gated): H7 batch · H2 drop HaEntity · H1+M11–M15+M0 FKs/indexes/uniques/enum-defaults · H5 downgrade · H6 recasting · H17 alerts.property_id · M14 vendor_properties
- [ ] **G-R5 — Money int-cents**: M1 TypeDecorator · cast migration · M2+M3+M4+M6 finance math
- [ ] **G-R1 — Ban silent swallows**: R1 census→`logger.exception` · smoke test (every tool+report) · L1 logging config
- [ ] **G-Svc — Silent-corruption sweep**: H15 double-count · H16 health period · H18 daily recurrence · H19 backfill · H20 calendar · H21 vendor soft-delete · H22 WO cost · H23 issue↔WO link · H12 date · H13 image-capability · H14 stream session · M5+M10 fuzzy · M34+M35+M37 provider content
- [ ] **G-R2 — Gateway dedup core**: R2 `review_common.py` · H24–H28 · H35 PTO notifier · H36 vendor name · M21 poison-guard · M22–M31 · L12–L15
- [ ] **G-Web — Web hardening**: M43 delete reports · H29 chart · H31+R3 error handlers · M40 ValueError · H30 CSRF/Host · H32 zip · M16 form parse · M17 active toggle · M18 XSS · M19+M20
- [ ] **G-CLI — CLI parsing + tail**: M39+M40 · M41 exit code · M42 dashboard · L2–L11 hygiene
- [ ] **G-Final — Compound stop**: full suite green · smoke green · spec reconciled · empty autogenerate · end-of-run report

**Stop condition (all four):** every DAG box `[x]` · every spec finding landed-or-deferred · full suite green · R1 smoke green. No intermediate review stops.

---

## What's Done

### Phase 1a — Core CLI (Complete)
- [x] Project skeleton: pyproject.toml, src layout, alembic, CLI entry point
- [x] Database foundation: SQLAlchemy models, TimestampMixin, SlugMixin, WAL mode
- [x] Slug system: generate, ensure_unique, resolve_identifier (ID or slug accepted everywhere)
- [x] Audit log: immutable changelog on every create/update/delete
- [x] Property CRUD: types, status, occupancy (occupy/vacate), climate zones
- [x] Space CRUD: rooms and areas per property
- [x] Staff management: profiles, roles, assignments, PTO tracking, workload/schedule
- [x] Vendor management: profiles, service categories, areas, ratings
- [x] Task system: CRUD, recurrence engine (daily/weekly/biweekly/monthly/quarterly/seasonal/annual), priority, assignment
- [x] Issue tracking: full lifecycle (reported → assessed → scheduled → in-progress → resolved → verified), severity
- [x] Budget + transactions: per-property/category budgets, expense logging, variance tracking
- [x] Notes: attachable to any entity
- [x] Init wizard: `mihomes init` + `mihomes init --demo` with sample data
- [x] Dashboard: Rich terminal layout across all properties
- [x] Audit CLI: `mihomes audit <entity> <id>`, `mihomes audit --recent`
- [x] 742 passing tests

### Phase 1b — Supporting Features (Complete)
- [x] Vendor contracts: tracking, renewal alerts
- [x] Recurring expenses: auto-generate transactions on schedule
- [x] Insurance policies: tracking, renewal alerts, coverage gap detection
- [x] Templates and checklists: create, manage, instantiate into tasks
- [x] Tags: polymorphic, attachable to any entity, filter by tag
- [x] Global search across all entity types
- [x] Alerts system: overdue tasks, expiring items, budget variances, SPACE-sorted
- [x] Unified reporting: `mihomes report` with property, vendor, spending, estate views
- [x] Configuration system: `mihomes config set/list/reset`
- [x] Backup/restore + `mihomes doctor` integrity checks
- [x] Data archival: `mihomes archive run/stats`
- [x] CSV import/export

### Phase 2 — AI Intelligence (Complete)
- [x] AIProvider abstraction: ClaudeProvider, OpenAIProvider, OllamaProvider, NIMProvider
- [x] `mihomes ai ask` with context-aware routing to 13 specialist roles
- [x] `mihomes ai review` for proactive recommendations
- [x] SPACE framework integrated into all AI prioritization
- [x] AI-assisted issue severity assessment
- [x] AI-assisted import (paste unstructured text → structured records)
- [x] Conversation history storage (AIConversations)
- [x] Weekly report service with AI narrative
- [x] Predictive maintenance service
- [x] Property health score service

### Phase 3 — Depth + Communication (Mostly Complete)
- [x] Asset management: appliances, vehicles, valuables, consumables
- [x] Work order workflow: estimate → approve → assign → complete → verify
- [x] Vendor performance tracking and comparison
- [x] Event and guest management with preference profiles
- [x] Document storage and expiration tracking
- [x] Seasonal checklists and built-in task templates
- [x] Google Calendar integration (bidirectional sync)
- [x] iCal file import
- [x] Zone management
- [x] Weather-aware task scheduling
- [x] Playbook system: `mihomes playbook run <name>` backed by knowledge/playbooks/
- [x] Resume ranker: AI-assisted candidate evaluation
- [x] WhatsApp gateway: bridge code, message parsing, review queue, estate context injection
- [ ] **WhatsApp pairing — BLOCKED** (Baileys "cannot link device" error on wacli-integration branch)
- [x] Staff PTO request workflow (CLI side)

### Phase 4 — Automation (Partial)
- [x] Automation service and CLI (`mihomes automation`)
- [x] Cron helper (`mihomes cron setup`)
- [x] Weather API integration + weather-triggered task scheduling
- [ ] Scheduled AI digest (daily/weekly via cron) — not wired end-to-end
- [ ] Smart reorder alerts for consumable inventory — service exists, alerts not triggered

### Bonus — Beyond PRD (Built)
- [x] **Home Assistant integration**: `src/mihomes/ha/`, `src/mihomes/cli/ha.py`, `src/mihomes/services/ha_sync.py`, `src/mihomes/models/ha_entity.py`
- [x] **HA custom component**: `custom_components/mihomes/` (sensors, binary sensors, todo integration)
- [x] **Docker deployment**: `Dockerfile`, `docker-compose.yml` (MiHomes + Home Assistant stack)
- [x] **HA addon**: `addon/` (installable from HA addon store)
- [x] **FastAPI + HTMX web UI** (ui-frontend branch): dashboard, properties, issues, tasks, staff, vendors, budget, contracts, assets, alerts, work orders — all with inline editing across 7 detail tabs
- [x] **REST API**: `src/mihomes/api/` — routes, schemas, HA-facing endpoints
- [x] **Knowledge base**: hiring playbook, onboarding, emergency, daily ops, housekeeper, communication, separation SOPs
- [x] **Hiring system**: candidate evaluation files, resume ranker, phone screening rubric (25-pt)

---

## What's Next

### Active: Telegram Bot (telegram-bot branch)
Replace the WhatsApp/Baileys bridge with a Telegram bot — no Node.js, no pairing, plain urllib REST to the Bot API.

**Files to create:**
- [x] `src/mihomes/services/gateways/telegram/__init__.py`
- [x] `src/mihomes/services/gateways/telegram/client.py` — urllib REST client (getUpdates, sendMessage, getFile, getMe)
- [x] `src/mihomes/services/gateways/telegram/review.py` — reuse WhatsApp review.py (same message dict contract, update prompt string only)
- [x] `src/mihomes/services/gateways/telegram/responder.py` — adapt from WhatsApp responder (swap JID → chat_id, swap client calls)
- [x] `src/mihomes/services/gateways/telegram/extractor.py` — adapt from WhatsApp extractor
- [x] `src/mihomes/cli/telegram.py` — CLI: setup, status, chats, link-chat, unlink-chat, send, monitor, review, watchdog

**Files to modify:**
- [x] `scripts/watchdog.py` — replaced bridge health check with Telegram bot health check
- [x] `src/mihomes/cli/__init__.py` — registered `telegram` app
- [x] `pyproject.toml` — updated coverage omit

**Config keys (no DB migration):**
- `telegram.bot_token` — from BotFather
- `telegram.chat_links` — JSON `{chat_id: property_slug}`
- `telegram.last_update_id` — deduplication offset
- `telegram.pto_approver_id` — Telegram user_id of approver (replaces phone-based lookup)

**Design decisions:**
- Internal message dict keys unchanged (`jid`, `senderName`, `text`, `hasMedia`, `mediaPath`, `propertySlug`) — `chat_id` fills the `jid` slot
- No `python-telegram-bot` dependency — plain urllib REST matches existing WhatsApp client pattern
- Staff reporter matching: name-only (Telegram gives no phone numbers)
- Media: download to `~/.mihomes/media/telegram/` before review pipeline sees it

**Prerequisite (user):** Create a bot via BotFather in Telegram → get token → `mihomes config set telegram.bot_token <token>`

### Unblock (Highest Priority)
- [x] ~~Fix Baileys pairing error~~ — replaced by Telegram bot (no pairing required)

### Web UI (ui-frontend branch)
- [ ] **Merge ui-frontend → main** — branch is stable enough; divergence debt compounds weekly
- [ ] **AI chat panel on dashboard** — input + streaming response div; `mihomes ai ask` equivalent in the browser
- [ ] **Mobile-responsive views** for issue logging and task completion — field use case (walk-through inspections)
- [ ] **WhatsApp review queue** web UI — when Baileys is unblocked, `mihomes whatsapp review` needs a web surface
- [ ] Search page
- [ ] Audit trail page
- [ ] Reports page

### Operational
- [ ] **Pre-commit hook** (ruff + basic lint) — same bug categories keep surfacing in code review; a hook eliminates them at source
- [ ] **Update PRD** to include HA integration as a formal phase and the Docker/addon deployment story
- [ ] Confirm `mihomes recurring generate` end-to-end works in production
- [ ] Wire health score into dashboard web UI (service exists, not surfaced)

### Hiring (Active)
- [ ] Finalize trial day candidates: Brandi Beam (comp + background check decision with Chris)
- [ ] Backups if Brandi doesn't clear: Shakita Baker, Zion (both at 17.5/25)
- [ ] Sherri Martinez (16.5/25) — hold as third backup

---

## Known Issues / Tech Debt
- `tasks/todo.md` was stale for months (fixed 2026-05-14)
- WhatsApp bridge pairing blocked since wacli-integration branch
- HA integration not documented in PRD (§10 phases)
- `src/mihomes/api/` (REST API layer) is untracked — should be committed with web UI
- No pre-commit hook; code review is the only quality gate
