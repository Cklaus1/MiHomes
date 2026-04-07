# MiHomes — Product Requirements Document

**Version:** 1.0
**Date:** 2026-03-27
**Status:** Draft
**License:** [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — Creative Commons Attribution-NonCommercial 4.0 International

---

## 1. Vision

MiHomes is an AI-first, multi-home estate management system that unifies property operations, staff coordination, vendor management, financial tracking, and lifestyle services into a single CLI-powered platform. It replaces the fragmented toolset (spreadsheets, notes, separate apps) that estate managers and multi-home owners currently rely on with a cohesive, intelligent system that proactively manages, prioritizes, and optimizes across all properties.

## 2. Problem Statement

Managing multiple homes involves coordinating dozens of recurring tasks (weekly cleaning, monthly HVAC filters, quarterly pest control, seasonal opening/closing), tracking issues as they arise, managing staff and vendors, staying on budget, and ensuring every property is ready when needed. Today this is done with a patchwork of tools — no existing software addresses the full scope of multi-home estate management, especially with AI assistance.

**Key gaps in existing software:**
- Property management platforms (Buildium, AppFolio) target landlords/tenants, not owner-occupied estates
- Short-term rental tools (Breezeway, Guesty) focus on guest turnover, not lifestyle management
- Personal home apps (HomeZada, Centriq) handle one home, lack staff/vendor depth
- No platform combines property ops + vehicles + staff + events + lifestyle + AI advisory

## 3. Target Users

- **Primary:** Multi-home owners managing 2-10+ properties (primary residence, vacation homes, investment properties)
- **Secondary:** Estate managers, family office staff, property managers for high-end portfolios
- **Initial:** Single technical user running locally via CLI (the owner or their estate manager)

## 4. Technical Foundation

| Decision | Choice | Rationale |
|---|---|---|
| Runtime | Python 3.11+ | Rich CLI ecosystem, strong AI/LLM library support |
| Database | SQLite | Zero-config, local-first, portable, sufficient for single-user |
| CLI Framework | Typer + Rich | Modern, type-hinted CLI with rich terminal output |
| AI Integration | Multi-provider (Claude default, OpenAI, Ollama, NVIDIA NIM) | Best reasoning for advisory; abstraction avoids lock-in |
| WhatsApp | Baileys (@whiskeysockets/baileys) | Free, open-source, no Business API costs, full group/media support |
| Architecture | Local-first, single-user | Privacy, simplicity, no infrastructure cost |

### Data Model (Core Entities)

```
Properties        — homes, addresses, features, type, status, climate_zone
                    type enum: primary, vacation, seasonal, investment, rental, estate, other
                    status enum: open, closed, caretaker-mode, under-renovation
                    climate_zone: determines seasonal task scheduling (e.g., northeast, southeast, mountain, tropical)
Spaces            — rooms/areas within properties
Staff             — household employees, roles, certifications, assignments
                    includes: phone, email, whatsapp_phone (required for Phase 3 gateway)
Vendors           — contractors, service providers, ratings, contracts
Contracts         — vendor/service contracts with terms, renewal dates, costs
Tasks             — recurring and one-off work items, with priority (urgent/high/medium/low)
TaskSchedules     — recurrence rules (weekly/monthly/quarterly/seasonal/annual)
RecurringExpenses — repeating financial items (utilities, payroll, subscriptions, contract payments)
                    links to: property, vendor, category; generates Transactions on schedule
Templates         — reusable checklists and task templates (pre-arrival, seasonal closing, etc.)
TemplateItems     — individual steps within a template
Issues            — problems discovered, linked to property/space
WorkOrders        — vendor/staff assignments for tasks or issues
Assets            — inventory items, appliances, vehicles, valuables
InsurancePolicies — property, liability, vehicle, and valuable articles policies
                    fields: policy number, carrier, type, coverage limit, deductible,
                    premium, renewal date, linked properties/assets/vehicles
Budgets           — per-property and per-category budget targets
Transactions      — income and expenses, linked to properties/vendors/categories
                    source: manual, recurring_expense, work_order, whatsapp
Events            — planned gatherings, guest visits
Guests            — guest profiles, preferences, dietary info
Documents         — files, warranties, manuals, contracts, policies
Notes             — free-form notes linked to any entity
Tags              — user-defined labels, polymorphic (attachable to any entity)
TagAssignments    — join table linking tags to entities
AuditLog          — immutable changelog of all create/update/delete operations with timestamps
Configurations    — system settings (calendar provider, AI provider, currency, etc.)
CalendarEvents    — synced or manually entered occupancy and schedule events
Alerts            — persisted alert records with lifecycle (generated/seen/acknowledged/resolved)
                    sources: overdue tasks, expiring items, budget variances, AI recommendations
                    fields: type, source_entity, severity, message, status, snoozed_until
AIConversations   — history of AI advisory sessions
WhatsAppThreads   — staff messaging threads linked to tasks/issues/properties (Phase 3)
```

### Entity Identification Strategy

Every entity has a numeric `id` (auto-incrementing primary key) used internally and in foreign keys. User-facing entities also have a `slug` (URL-safe, unique identifier) for CLI convenience:

- **Auto-generated** from name: "Beach House" → `beach-house`, "ABC Plumbing" → `abc-plumbing`
- **User-overridable** at creation: `mihomes property add "Beach House" --slug bh`
- **CLI accepts either** ID or slug: `mihomes property show 3` or `mihomes property show beach-house`
- Slugs are unique per entity type (two properties can't share a slug, but a property and a vendor can)
- Slug collisions append a numeric suffix: `beach-house`, `beach-house-2`

## 5. Project Structure

```
mihomes/
├── pyproject.toml                # project metadata, dependencies, CLI entry point
├── alembic/                      # database migration scripts
│   └── versions/
├── src/
│   └── mihomes/
│       ├── __init__.py
│       ├── cli/                  # Typer command definitions (one module per entity)
│       │   ├── __init__.py       # main app, registers all sub-commands
│       │   ├── property.py       # mihomes property add/list/show/occupy/vacate
│       │   ├── task.py           # mihomes task add/list/complete/upcoming
│       │   ├── issue.py
│       │   ├── staff.py
│       │   ├── vendor.py
│       │   ├── budget.py
│       │   ├── asset.py
│       │   ├── ai.py             # mihomes ai ask/review/search
│       │   ├── dashboard.py
│       │   ├── config.py
│       │   ├── report.py
│       │   └── ...
│       ├── models/               # SQLAlchemy ORM models (one module per entity)
│       │   ├── __init__.py
│       │   ├── property.py
│       │   ├── task.py
│       │   ├── issue.py
│       │   └── ...
│       ├── services/             # business logic layer (no CLI or DB dependencies)
│       │   ├── recurrence.py     # task recurrence calculation engine
│       │   ├── alerts.py         # alert generation and aggregation
│       │   ├── search.py         # cross-entity search
│       │   ├── budget.py         # budget calculations, variance detection
│       │   └── ...
│       ├── ai/                   # AI provider abstraction and role definitions
│       │   ├── __init__.py
│       │   ├── provider.py       # AIProvider protocol
│       │   ├── claude.py         # ClaudeProvider
│       │   ├── openai.py         # OpenAIProvider
│       │   ├── ollama.py         # OllamaProvider (Phase 3)
│       │   ├── nim.py            # NIMProvider — NVIDIA NIM (OpenAI-compatible, e.g. Qwen3.5-122B-A10B)
│       │   ├── router.py         # role routing logic
│       │   ├── context.py        # context window assembly and management
│       │   └── roles/            # system prompt templates per AI role
│       │       ├── estate_manager.py
│       │       ├── maintenance.py
│       │       └── ...
│       ├── gateways/             # external integrations
│       │   ├── whatsapp/         # Baileys bridge communication
│       │   │   ├── client.py     # Python client for the Node.js bridge
│       │   │   ├── parser.py     # message classification and issue extraction
│       │   │   └── review.py     # review queue management
│       │   └── calendar/         # CalendarProvider implementations
│       │       ├── provider.py   # CalendarProvider protocol
│       │       ├── google.py
│       │       ├── outlook.py
│       │       └── ical.py
│       ├── db.py                 # database connection, session management
│       └── config.py             # configuration loading and defaults
├── bridge/                       # Node.js WhatsApp bridge (Baileys)
│   ├── package.json
│   ├── index.ts                  # Baileys connection, local HTTP API server
│   └── tsconfig.json
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/
└── templates/                    # CSV import templates, built-in task templates
```

**Key architectural patterns:**
- **CLI → Service → Model**: CLI modules are thin (parse args, call service, format output). Services contain business logic. Models handle persistence.
- **No service depends on CLI**: Services are testable without Typer.
- **No model depends on service**: Models are pure data definitions and queries.
- **Gateway protocol pattern**: `WhatsAppGateway`, `CalendarProvider`, `AIProvider` — all use Python Protocol classes so implementations are swappable.
- **Bridge pattern for Node.js**: The Baileys WhatsApp bridge is a separate Node.js process communicating over localhost HTTP. This avoids mixing Python and Node.js runtimes while keeping the bridge independently deployable and restartable.

## 6. First-Run & Onboarding

When a user runs any `mihomes` command for the first time:

1. **Auto-detect** that `~/.mihomes/` does not exist
2. **Run `mihomes init`** interactively:
   - Create `~/.mihomes/` directory structure: `db/`, `media/`, `backups/`, `exports/`
   - Create SQLite database with full schema
   - Prompt for basic configuration:
     - Default currency (default: USD)
     - AI provider and API key (optional, can skip — AI features disabled until configured)
     - First property (optional, can add later)
   - Display a welcome message with quick-start commands
3. **`mihomes init` is also manually runnable** to reset or reconfigure
4. **`mihomes init --demo`** loads sample data (3 properties, tasks, vendors, issues) so users can explore the CLI before entering real data

## 7. Feature Specification

### 7.1 Property Management (Core)

**Properties & Spaces**
- Register properties with address, type, features, square footage, seasonal status
- Property types: primary, vacation, seasonal, investment, rental, estate, other
- Property status: open, closed, caretaker-mode, under-renovation
- Define spaces (rooms, garages, yards, pools, outbuildings) per property
- Track occupancy status and family calendar integration
- Seasonal opening/closing checklists per property

**Task Management**
- Create tasks with recurrence: weekly, biweekly, monthly, quarterly, seasonal, annual, or custom cron
- Seasonal recurrence is climate-zone-aware per property:
  - Each property has a `climate_zone` field (set during property creation or via edit)
  - Seasons are configurable per climate zone. Defaults: Spring (Mar-May), Summer (Jun-Aug), Fall (Sep-Nov), Winter (Dec-Feb)
  - Seasonal tasks specify which season(s) they apply to: `--recurrence seasonal:spring,fall`
  - Tasks auto-schedule to the first week of the applicable season for that property's climate zone
  - Example: "Gutter cleaning" with `seasonal:spring,fall` at a Northeast property triggers in March and September
- Priority levels: urgent, high, medium, low (AI can suggest priority using SPACE framework)
- Assign tasks to staff or vendors
- Track completion with timestamps and notes
- Overdue task alerts and escalation
- Task templates for common operations (pre-arrival prep, seasonal closing, post-storm inspection)
- Instantiate a template into a set of linked tasks with one command
- Dependency chains (task B cannot start until task A completes)
- Tags for flexible cross-cutting categorization (e.g., "pre-sale", "guest-visit", "insurance")

**Issue Tracking**
- Log issues found at any property/space with severity (critical/high/medium/low)
- Attach photos (file paths) and notes
- Link issues to work orders for resolution
- Track issue lifecycle: reported → assessed → scheduled → in-progress → resolved → verified
- AI-assisted severity assessment and prioritization

**Work Orders**
- Generate from tasks or issues
- Assign to vendors or staff with due dates
- Track cost estimates vs. actuals
- Approval workflow for work above a cost threshold

### 7.2 Staff Management

- Staff profiles: name, role, properties assigned, certifications, contact info
- Role types: housekeeper, groundskeeper, property manager, driver, chef, security, personal assistant, other
- Task assignment and workload visibility
- Availability and scheduling (which staff at which property, when)
- **PTO tracking:** Log time-off requests per staff member, approval status, and days used per year
- Performance notes (not ratings — qualitative observations)
- Certification and license expiration tracking

### 7.3 Vendor Management

- Vendor profiles: company, contacts, service categories, service areas, insurance info
- Link vendors to properties and service categories
- Track performance: response time, quality notes, reliability, pricing history
- Contract management: terms, renewal dates, auto-renewal alerts
- Preferred vendor designation per property per service category
- Compare vendor costs against budget benchmarks

### 7.4 Financial Management

- **Budgets:** Set annual/quarterly/monthly budgets per property and per category (maintenance, landscaping, cleaning, utilities, staff, events, vehicles, supplies)
- **Transactions:** Log expenses and income, categorize by property/vendor/category
- **Budget vs. Actual:** Real-time tracking with variance alerts
- **Forecasting:** Project future spend based on known scheduled tasks, contracts, and historical patterns
- **Reports:** Spending by property, by category, by vendor, by time period
- **Cost anomaly detection:** Flag expenses significantly above historical norms

### 7.5 Asset & Inventory Management

- **Appliances & Systems:** Model, serial number, install date, warranty expiration, maintenance schedule, location
- **Vehicles:** Make, model, year, VIN, location, registration/insurance dates, maintenance schedule, mileage
- **Valuables:** Art, collectibles, wine — with provenance, appraisal value, insurance coverage, storage conditions
- **Consumables:** Cleaning supplies, pantry basics, linens — with par levels and reorder alerts
- **Equipment:** Tools, outdoor gear, AV systems, smart home devices — with firmware/warranty tracking

### 7.6 Event & Hospitality Management

- Plan events with guest lists, vendor coordination, budgets, timelines
- Guest profiles with preferences (dietary, room, activities)
- Pre-arrival checklists triggered by guest visits
- Post-event task generation (cleanup, inventory restock, vendor review)
- Event history and cost tracking for future reference

### 7.7 Seasonal & Weather Awareness

- Property climate zone tracking
- Seasonal task templates (winterization, spring opening, hurricane prep, wildfire prep)
- Weather alert integration (future: API-based; initial: manual input)
- Automated seasonal checklist activation based on calendar

### 7.8 Documents & Knowledge Base

- Store documents linked to any entity (warranties, contracts, manuals, insurance policies, permits)
- Expiration date tracking with renewal alerts
- SOP library for staff procedures
- Property-specific house manuals

### 7.9 Tags & Labels

- User-defined tags attachable to any entity (properties, tasks, issues, vendors, assets, etc.)
- Polymorphic tagging via `TagAssignments` join table
- Filter and search by tag across all entity types: `mihomes search --tag "pre-sale"`
- Bulk operations by tag: `mihomes task list --tag "guest-visit"` to see all guest-related tasks across properties
- AI can suggest tags when creating entities based on context

### 7.10 Templates & Checklists

- Create reusable templates with ordered steps: `mihomes template add "Summer Opening" --steps "Turn on water main, Test all faucets, ..."`
- Assign default staff/vendor, estimated duration, and dependencies per step
- Instantiate a template into real tasks: `mihomes template run "Summer Opening" --property beach-house --due 2026-05-01`
- Built-in starter templates for common operations (seasonal opening/closing, pre-arrival, post-storm, new vendor onboarding)
- Templates are versionable — editing a template does not affect previously instantiated tasks

### 7.11 Vendor Contracts

- Track contract terms: start date, end date, auto-renewal flag, notice period, cost/rate
- Link contracts to vendors, properties, and service categories
- Renewal alerts triggered by notice period (e.g., "Contract with ABC Landscaping auto-renews in 30 days unless cancelled")
- Cost history per contract for renegotiation context
- Store contract documents (PDFs) linked to the contract record

### 7.12 Audit Trail

- Immutable `AuditLog` table records every create, update, and delete operation
- Each entry: timestamp, entity type, entity ID, action, field changes (old → new), actor (admin or "whatsapp:Sarah")
- `mihomes audit <entity-type> <id>` to view full history of any record
- `mihomes audit --recent` to see latest changes across the system
- Foundation for future multi-user accountability and compliance

### 7.13 Search

- Global search across all entity types: `mihomes search "plumbing"` returns matching properties, tasks, issues, vendors, assets, notes, and documents
- Scoped search: `mihomes search "leak" --type issues --property beach-house`
- Tag-based search: `mihomes search --tag "urgent"`
- AI-enhanced search (Phase 2): `mihomes ai search "what did we do about the roof last year?"` uses AI to interpret intent and query across entities and audit history

### 7.14 Configuration

- System settings managed via CLI: `mihomes config set <key> <value>`
- Key settings:
  - `calendar.provider` — manual | ical | google | outlook
  - `ai.provider` — claude | openai | ollama | nim
  - `ai.api_key` — stored securely (not in SQLite)
  - `currency.default` — USD, EUR, etc.
  - `notifications.format` — rich | brief | json
  - `data.directory` — path to `~/.mihomes/` (DB, media, backups)
- `mihomes config list` to view all current settings
- `mihomes config reset <key>` to restore defaults
- Settings stored in `Configurations` table with the exception of secrets (API keys stored in OS keyring or `.env` file)

### 7.15 Backup & Restore

- `mihomes backup` — creates a timestamped copy of the SQLite database and media directory into `~/.mihomes/backups/`
- `mihomes backup --output /path/to/backup.tar.gz` — export to a specific location
- `mihomes restore <backup-file>` — restores from a backup with confirmation prompt
- `mihomes doctor` — integrity checks: orphaned media files, missing references, database consistency, stale calendar events
- Future (Phase 4): automated backup scheduling via cron helper

### 7.16 WhatsApp Staff Gateway (Phase 3)

- Bidirectional messaging between MiHomes and staff via **Baileys** (`@whiskeysockets/baileys`)
- **Outbound (MiHomes → Staff):**
  - Task assignments with details and due dates
  - Reminders for overdue or upcoming tasks
  - Issue alerts relevant to staff's assigned properties
  - Daily/weekly digest of their task list
- **Inbound (Staff → MiHomes):**
  - Reply "done" (or similar) to mark tasks complete, with optional notes
  - Send photos that auto-attach to the relevant task or issue
  - Report new issues: "Issue: water stain on ceiling in guest bedroom" → AI parses and creates issue record
  - Request information: "What's my schedule this week?" → MiHomes responds with task list
- **Group chats:** Property-specific staff groups for coordination
- **Thread tracking:** All messages stored in `WhatsAppThreads` and linked to the relevant task/issue/property for audit trail
- **Media capture:** All photos/videos sent in threads are downloaded via Baileys `downloadMediaMessage()`, stored in `~/.mihomes/media/whatsapp/`, and linked to the relevant entity. AI analyzes images for context (damage severity, before/after comparison, equipment model identification)
- Architecture: `NotificationGateway` protocol with `WhatsAppGateway` (Baileys), `CLIGateway` implementations

#### 7.16.0 Baileys Integration Details

**Why Baileys over WhatsApp Business API:**
- Zero cost (no per-message fees, no BSP subscription)
- Uses your existing personal/team WhatsApp number — no separate business number required
- Full group chat access including reading all messages (Business API cannot join existing groups)
- No message template approval process — send any message anytime
- No 24-hour conversation window restrictions
- Runs locally (aligns with local-first architecture)

**How it works:**
- Baileys is a Node.js/TypeScript library that implements the WhatsApp Web Multi-Device protocol via WebSocket
- Registers as a "linked device" on your WhatsApp account (like WhatsApp Web)
- Phone does NOT need to stay online after initial QR code pairing
- MiHomes runs a lightweight Node.js sidecar process (`mihomes-whatsapp-bridge`) that connects via Baileys and communicates with the Python CLI via a local socket/REST API

**Setup flow:**
- `mihomes whatsapp setup` — starts the bridge, displays QR code in terminal
- User scans QR code with their WhatsApp mobile app (one-time)
- Session credentials stored in `~/.mihomes/whatsapp-auth/` — persists across restarts
- `mihomes whatsapp link-group <group-name> --property lakeside-estate` — links an existing WhatsApp group to a property
- `mihomes whatsapp status` — shows connection status, linked groups, message stats

**Runtime architecture:**
- `mihomes-whatsapp-bridge` runs as a background daemon (Node.js)
- Communicates with MiHomes Python process via local HTTP API on `localhost`
- Bridge handles: WebSocket connection, message encryption/decryption, media download, event streaming
- Python side handles: AI analysis, issue extraction, task creation, admin notifications

**Risks and mitigations:**
- **Account ban risk:** Baileys violates WhatsApp's ToS. Mitigations: use a dedicated WhatsApp number (not personal), keep message volume low (estate management is naturally low-volume), avoid bulk/broadcast messaging, maintain organic chat activity on the number
- **Protocol changes:** WhatsApp can break Baileys with protocol updates. Mitigation: WhiskeySockets community is active and typically patches within days; gateway degrades gracefully (messages queue locally until connection restores)
- **Session expiry:** WhatsApp may force re-linking. Mitigation: `mihomes whatsapp status` checks health; alerts admin if session needs re-authentication

#### 7.16.1 Passive Issue Detection (Conversation Intelligence)

Staff group chats contain a constant stream of operational signal mixed with social conversation. MiHomes AI monitors group messages passively and extracts actionable items — without requiring staff to use any special format or commands.

**How it works:**
1. All group messages are ingested and stored in `WhatsAppThreads`
2. AI periodically (or in near-real-time) analyzes the conversation stream
3. AI classifies each message or message cluster into categories:
   - **Issue / Problem** — something is broken, damaged, malfunctioning, missing, or needs repair
   - **Task / Request** — someone is asking for something to be done
   - **Task Completion** — someone confirming work was done
   - **Delivery / Scheduling** — coordination about upcoming arrivals, installations, visits
   - **Informational** — status updates, FYIs, social/personal (no action needed)
   - **Supply Need** — something needs to be purchased, replaced, or restocked
   - **Vendor Activity** — a vendor visit, repair, or installation is happening or planned
4. For actionable items, AI extracts: what, where (property/space), who reported it, severity estimate, any associated photos, and related context from the thread
5. Extracted items are surfaced to the admin as **suggested issues/tasks** in a review queue — NOT auto-created
6. Admin reviews via `mihomes whatsapp review` and confirms, edits, or dismisses each suggestion
7. Confirmed items become real Issues, Tasks, or Work Orders linked back to the original WhatsApp messages

**Real-world example** — given this actual staff group conversation:

```
[10:33 AM] Sarah: The bush where the herb garden is needs to be trimmed
[10:33 AM] Sarah: There are tons of spiders there
[10:34 AM] Sarah: The sun room near master needs to be cleaned as well
[11:08 AM] Sarah: Deer treatment
[1:41 PM]  James: Hey Marco, The back left tire on the Rivian is running low
                   on air and needs to be brought back up to 42 PSI
[1:42 PM]  Marco: Not a problem James 👍🏼
[5:42 PM]  Sarah: Marco, Do we have a tire patch?
[5:42 PM]  Sarah: James tire may have a hole in it
[12:37 PM] Sarah: We need a new water gallon in master please
[12:38 PM] Marco: I'll replace right away 👍🏼
[4:06 PM]  Sarah: Marco, Kevin is coming to check the backyard bar ice maker
[2:21 PM]  Lisa: NO WATER
[3:39 PM]  Sarah: I just noticed. I told James
[3:52 PM]  Sarah: I check the correspondence on this issue and they fixed it
                   by tightening the drain plug back in May.
[10:52 AM] Sarah: Patio furniture/cushions needs to be washed, bar area,
                   pool chair cushions needs to be cleaned as well
[9:34 AM]  Lisa: Ac is out at your office Sarah!
[9:34 AM]  Lisa: It needs to buy a new cover for the cart
[10:31 AM] Sarah: I'd like to check the sump pumps. Make sure good condition
[10:31 AM] Sarah: Check pool equipment in back. The pool company has ordered
                   parts to get it fixed.
[10:41 AM] Lisa: [re: cart cover] It is ripped, and it can't be sewn
```

**AI would extract and present to admin:**

```
╭─ WhatsApp Review Queue (12 items from property: Lakeside Estate) ─╮
│                                                                     │
│  ISSUES                                                             │
│  1. 🔴 No water (recurring)                    Severity: Critical   │
│     Reported by: Lisa, 7/8 2:21 PM                                 │
│     Context: Previously fixed May (drain plug). Recurrence          │
│     suggests deeper problem.                                        │
│     → [Create Issue] [Dismiss] [Edit]                               │
│                                                                     │
│  2. 🟡 AC out in office                        Severity: High      │
│     Reported by: Lisa, 7/11 9:34 AM                                │
│     Note: Sarah says she messaged Tyler (vendor?) about it         │
│     → [Create Issue] [Dismiss] [Edit]                               │
│                                                                     │
│  3. 🟡 Rivian back left tire — possible puncture Severity: Medium  │
│     Reported by: Sarah, 6/25 5:42 PM                               │
│     Context: Initially low air (James), Sarah suspects hole.       │
│     Asset: Rivian (auto-linked)                                     │
│     → [Create Issue] [Dismiss] [Edit]                               │
│                                                                     │
│  4. 🟡 Pool equipment needs parts/repair       Severity: Medium    │
│     Reported by: Sarah, 7/11 10:31 AM                              │
│     Context: Pool company has ordered parts                         │
│     → [Create Issue] [Dismiss] [Edit]                               │
│                                                                     │
│  5. 🟢 Cart cover ripped, cannot be sewn       Severity: Low       │
│     Reported by: Lisa, 7/11 10:41 AM                               │
│     Action needed: Purchase replacement                             │
│     → [Create Issue] [Create Supply Request] [Dismiss]              │
│                                                                     │
│  6. 🟢 Spider infestation near herb garden     Severity: Low       │
│     Reported by: Sarah, 6/25 10:33 AM                              │
│     → [Create Issue] [Dismiss] [Edit]                               │
│                                                                     │
│  TASKS                                                              │
│  7. Trim bush (herb garden area)               Assignee: —         │
│     Reported by: Sarah, 6/25 10:33 AM                              │
│     → [Create Task] [Dismiss] [Edit]                                │
│                                                                     │
│  8. Clean sun room near master                 Assignee: —         │
│     Reported by: Sarah, 6/25 10:34 AM                              │
│     → [Create Task] [Dismiss] [Edit]                                │
│                                                                     │
│  9. Deer treatment (landscaping)               Assignee: —         │
│     Reported by: Sarah, 6/25 11:08 AM                              │
│     → [Create Task] [Dismiss] [Edit]                                │
│                                                                     │
│ 10. Wash patio furniture/cushions, bar area,   Assignee: —         │
│     pool chair cushions                                             │
│     Reported by: Sarah, 7/9 10:52 AM                               │
│     → [Create Task] [Dismiss] [Edit]                                │
│                                                                     │
│ 11. Check sump pumps condition                 Assignee: —         │
│     Reported by: Sarah, 7/11 10:31 AM                              │
│     → [Create Task] [Dismiss] [Edit]                                │
│                                                                     │
│  COMPLETED (auto-detected)                                          │
│ 12. ✅ Ice machine installed (backyard bar)                         │
│     Confirmed by: Marco, 7/1 2:14 PM                               │
│     Vendor: Kevin (visited 6/27)                                    │
│     → [Log Completion] [Dismiss]                                    │
│                                                                     │
│  SKIPPED (no action needed)                                         │
│  - Water gallon replacement (Marco handled immediately)             │
│  - Bread/food sharing messages (social)                             │
│  - Pet medication discussion (personal)                             │
│  - Washer/dryer delivery coordination (informational)               │
│  - Max's cat missing (personal, not property issue)                 │
╰─────────────────────────────────────────────────────────────────────╯
```

**Key intelligence features:**
- **Correlates related messages:** Links James's tire air request → Sarah's puncture suspicion → upgrades from simple task to potential issue
- **Detects recurring problems:** "NO WATER" cross-referenced with May drain plug fix → flags as recurring with higher severity
- **Identifies vendor activity:** Kevin's ice maker visit → Marco confirms installation → logs as completed work
- **Filters noise:** Bread sharing, pet medication, social messages correctly classified as non-actionable
- **Captures implicit tasks:** "Deer treatment" with no verb is still recognized as a task request
- **Links to assets:** "Rivian" auto-matched to vehicle in asset inventory
- **Escalates intelligently:** "NO WATER" gets Critical severity; spider infestation gets Low
- **Preserves context:** Each extracted item links back to the original messages and any associated photos for full traceability

#### 7.16.2 PTO Request Handling

Staff submit time-off requests naturally in the WhatsApp group chat. The bot detects the request, logs it as pending, notifies the approver, and waits for a decision — all without staff needing to learn any special format or command.

**Detection — what triggers a PTO request:**
- Natural language: "I'd like to take Friday off", "Can I have next Monday?", "Requesting PTO for Dec 24-26", "I need a day off this week"
- AI classifies these as `pto_request` category (new category added to the conversation intelligence classifier)
- Requests are **never auto-approved** — always go to pending state

**Request lifecycle:**
1. Staff sends PTO request in the group chat (or direct message to the bot)
2. Bot logs the request: staff member, dates requested, property coverage affected
3. Bot replies in chat: *"🏠 PTO request logged for [name] — [dates]. Pending approval."*
4. Bot sends a direct WhatsApp message to the configured approver: *"🏠 PTO request from [name]: [dates]. Reply APPROVE [name] [dates] or DENY [name] [dates]."*
5. Approver replies via WhatsApp to approve or deny
6. Bot updates request status and notifies the requester: *"🏠 Your PTO request for [dates] has been approved/denied."*
7. On approval: PTO is blocked on the staff member's schedule and synced to Google Calendar

**Coverage conflict detection:**
- On logging a request, the bot checks if the staff member has open tasks assigned at any property during the requested dates
- If conflicts exist, the approver notification includes a warning: *"⚠️ [name] has [N] tasks assigned during this period at [property]."*
- Admin can still approve — this is advisory only, not a block

**PTO balance tracking:**
- Days used per staff member tracked per calendar year (no complex accrual — manual/simple)
- Admin can view with `mihomes staff pto <name>`
- Balance shown in `mihomes staff show <name>`

**Approver configuration:**
- Set via `mihomes config set staff.pto_approver_phone <number>`
- Defaults to `owner.whatsapp_phone` if not set separately

**Data model additions:**
```
StaffPTORequest
  id, staff_id, requested_dates (JSON list of dates), status (pending/approved/denied),
  requested_at, decided_at, decided_by, notes, property_id (coverage context)
```

**CLI commands:**
- `mihomes staff pto <name>` — show PTO history and balance for a staff member
- `mihomes staff pto-requests` — list all pending PTO requests across all staff
- `mihomes staff pto-approve <request-id>` — approve via CLI (alternative to WhatsApp)
- `mihomes staff pto-deny <request-id> [--reason]` — deny via CLI

**What this intentionally does NOT do:**
- No accrual policies, earned-days math, or rollover tracking — out of scope for estate management
- No payroll integration
- No multi-level approval chains — one approver is sufficient
- Does not block task assignment during PTO — admin retains full control

### 7.17 Insurance Tracking

- Track insurance policies across all properties, vehicles, and valuable assets
- Policy types: property/homeowners, liability/umbrella, valuable articles (art, jewelry, wine), vehicle, workers compensation, event liability
- Fields per policy: policy number, carrier, agent contact, type, coverage limit, deductible, annual premium, renewal date, linked entities (properties, assets, vehicles)
- Renewal alerts triggered by configurable lead time (default: 60 days before renewal)
- Coverage gap detection: AI compares insured values against current asset/property values and flags underinsurance
- Claim tracking: log claims against policies with status, amounts, and documentation
- `mihomes insurance list` — all policies across the estate
- `mihomes insurance list --expiring 90` — policies renewing in 90 days
- `mihomes insurance gaps` — AI-assisted coverage adequacy review

### 7.18 Recurring Expenses

- Define repeating financial items that auto-generate transactions on schedule
- Types: utility bills, staff payroll, subscriptions, service contracts, HOA dues, insurance premiums, loan payments
- Fields: amount (fixed or estimated), frequency (weekly/biweekly/monthly/quarterly/annual), vendor, property, category, start date, end date (optional)
- `mihomes recurring add "Pool Service" --amount 350 --frequency monthly --vendor pool-pros --property beach-house --category maintenance`
- `mihomes recurring list` — all active recurring expenses
- `mihomes recurring generate` — create pending transactions for current period (run manually or via cron)
- Estimated vs. actual: recurring expenses generate "expected" transactions; user confirms or adjusts the actual amount when the bill arrives
- Feeds into budget forecasting: AI uses recurring expenses + known one-time costs to project future spend

### 7.19 Alerts

- Centralized alert system aggregating time-sensitive items across all subsystems
- `mihomes alerts` — show all pending alerts, sorted by urgency (SPACE framework)
- `mihomes alerts --format brief` — one-line-per-alert output for cron piping
- `mihomes alerts --format json` — machine-readable output for custom integrations
- Alert sources:
  - Overdue tasks
  - Issues above a severity threshold unresolved for N days
  - Budget variance exceeding threshold
  - Warranty/contract/insurance/certification expirations within configured lead time
  - Recurring expenses pending confirmation
  - Seasonal preparation reminders based on property climate zone and calendar
  - AI-generated proactive recommendations (Phase 2)
- Alert lifecycle: generated → seen → acknowledged → resolved (or auto-resolved when underlying item is addressed)
- Alert suppression: `mihomes alerts snooze <alert-id> --days 7` to defer non-urgent items

### 7.20 Data Retention & Archival

- Active data: all current/open items live in main tables with full query performance
- Completed/resolved items: remain in main tables indefinitely (they're small and useful for AI pattern analysis)
- Archival policy for high-volume tables:
  - `AuditLog`: entries older than 2 years archived to `audit_log_archive` table (still queryable but excluded from default queries). Configurable: `mihomes config set retention.audit_years 2`
  - `WhatsAppThreads`: message content older than 1 year archived; thread metadata retained. Configurable: `mihomes config set retention.whatsapp_years 1`
  - `AIConversations`: conversations older than 1 year archived. Configurable: `mihomes config set retention.ai_years 1`
  - `Transactions`: never archived (financial records must be retained for tax/legal purposes)
- `mihomes archive run` — manually trigger archival based on configured retention periods
- `mihomes archive stats` — show data volume per table and what would be archived
- Archived data remains in the SQLite file (separate tables) and is included in backups
- `mihomes search` and `mihomes audit` can optionally query archived data with `--include-archived`

---

## 8. AI System Design

### 8.1 Philosophy

MiHomes treats AI as a **team of specialist advisors**, not a single chatbot. Each AI role has a defined scope, data context, and decision framework. The user interacts through a unified interface, but the system routes queries to the appropriate specialist context.

### 8.2 AI Expert Roles

| Role | Scope | Key Capabilities |
|---|---|---|
| **Estate Manager** | Cross-property orchestration | Prioritization, status overview, resource allocation, proactive planning |
| **Maintenance Advisor** | Building systems & repairs | Preventive scheduling, failure prediction, vendor matching, warranty tracking |
| **Financial Analyst** | Budgets & spending | Variance analysis, cost optimization, forecasting, anomaly detection |
| **Vendor Strategist** | Contractor relationships | Performance analysis, contract negotiation advice, vendor matching |
| **Hospitality Planner** | Events & guests | Guest preference recall, event planning, checklist generation |
| **Housekeeping Supervisor** | Cleaning & linens | Schedule optimization, supply management, quality standards |
| **Grounds Manager** | Landscaping & exterior | Seasonal planning, irrigation advice, weather-responsive scheduling |
| **Fleet Manager** | Vehicles & watercraft | Maintenance scheduling, registration tracking, seasonal prep |
| **Security Advisor** | Access & safety | Access audit, vacancy protocols, emergency preparedness |
| **Energy Analyst** | Utilities & sustainability | Usage analysis, efficiency recommendations, cost reduction |
| **Asset Curator** | Inventory & valuables | Insurance adequacy, replacement planning, condition tracking |
| **Compliance Monitor** | Regulations & permits | Deadline tracking, regulatory changes, HOA compliance |
| **Lifestyle Assistant** | Personal & family | Subscriptions, appointments, travel coordination, preference management |

### 8.3 AI Role Routing

When a user issues an `ai ask` or `ai review` command, the system must decide which AI role(s) to activate:

**Routing strategy:**
1. **Keyword + entity analysis:** Parse the user's query for entity references (property names, vendor names, asset types) and domain keywords ("budget" → Financial Analyst, "leak" → Maintenance Advisor, "party" → Hospitality Planner)
2. **Multi-role queries:** If a query spans multiple domains (e.g., "the roof is leaking and I need to know if insurance covers it"), activate multiple roles. The Estate Manager role serves as orchestrator, synthesizing inputs from Maintenance Advisor and Asset Curator (insurance)
3. **Explicit routing:** User can force a role: `mihomes ai ask --role financial "should I renegotiate the landscaping contract?"`
4. **Fallback:** If no specific role matches, route to Estate Manager as the generalist

**Implementation:**
- Each role is a **system prompt template** that includes: role description, decision framework, SPACE priorities relevant to this role, and instructions for structured output
- Each role has a **data fetcher** that assembles the relevant context (e.g., Maintenance Advisor gets: property details, asset inventory for that property, recent work orders, vendor list, warranty dates)
- Roles do NOT call the AI independently in parallel — the orchestrator builds a single prompt with the relevant role context(s) and makes one AI call
- The role's system prompt is prepended; the assembled data context is injected as a structured data block; the user's query is appended

### 8.4 AI Context Window Management

With potentially 100 properties and 100K+ tasks, sending everything to the AI is impossible. Context must be carefully selected:

**Context selection strategy:**
1. **Query-scoped data:** If the query references a specific property, only fetch data for that property. If it references a vendor, fetch that vendor's history.
2. **Relevance ranking:** For broad queries ("what should I prioritize this week?"), fetch:
   - All overdue tasks (capped at 50, sorted by SPACE priority)
   - All open issues (capped at 30, sorted by severity)
   - Budget summaries per property (aggregated, not individual transactions)
   - Upcoming deadlines within 30 days (warranties, contracts, certifications)
   - Recent audit log entries (last 7 days, capped at 20)
3. **Summary over detail:** Send aggregated summaries rather than raw records. Example: "Beach House: 12 open tasks, 3 overdue, 2 critical issues, Q1 budget 78% spent" rather than all 12 task records
4. **Progressive detail:** If the AI's response indicates it needs more data, the system can do a follow-up call with additional context (e.g., AI says "I need the maintenance history for the HVAC" → fetch and re-query)
5. **Token budget:** Each AI call targets a maximum context of ~50K tokens for data, leaving room for system prompt and response. Data is truncated with a "... and N more items, use --detail for full context" note
6. **Conversation continuity:** `AIConversations` stores prior exchanges so follow-up questions can reference previous context without re-fetching everything

### 8.5 AI Interaction Model

```
mihomes ai ask "The Hamptons house has a water stain on the guest bedroom ceiling"
```

The system:
1. Identifies relevant AI roles (Maintenance Advisor, potentially Insurance Manager)
2. Gathers context: property details, recent work orders, weather history, roof age, insurance coverage
3. Returns assessment: likely causes, recommended next steps, vendor suggestions, whether to file a claim
4. Optionally creates an issue and/or work order from the recommendation

```
mihomes ai review
```

Proactive daily/weekly review:
- Overdue tasks across all properties
- Upcoming deadlines (warranties expiring, contracts renewing, permits due)
- Budget variance alerts
- Seasonal preparation reminders
- Maintenance predictions based on equipment age and history

### 8.6 SPACE Prioritization Framework

When AI makes recommendations, it uses the **SPACE** framework:

| Priority | Meaning | Example |
|---|---|---|
| **S** — Safety | Risk to people or property | Gas leak, structural issue, security breach |
| **P** — Presence | Family occupying or arriving soon | Pre-arrival repairs, comfort issues |
| **A** — Asset Protection | Delay causes damage | Water leak, pest infestation, weather exposure |
| **C** — Compliance | Legal/regulatory deadlines | Permits, tax filings, HOA rules, inspections |
| **E** — Economy | Financial impact | Cost of delay, budget optimization, contract deadlines |

Every AI recommendation includes its SPACE classification so the user understands the reasoning.

---

## 9. CLI Interface Design

### 9.1 Command Structure

```bash
mihomes <entity> <action> [options]
```

### 9.2 Core Commands

```bash
# Setup & Help
mihomes init                               # first-run setup wizard
mihomes init --demo                        # load sample data for exploration
mihomes version                            # show version, Python version, DB path
mihomes help                               # command overview with examples
mihomes <command> --help                   # detailed help for any command

# Properties
mihomes property add "Beach House" --address "123 Ocean Dr" --type vacation --climate-zone northeast
mihomes property list
mihomes property show beach-house
mihomes property status                    # overview of all properties
mihomes property occupy beach-house --from 2026-06-01 --to 2026-08-31
mihomes property vacate beach-house        # mark as unoccupied now

# Tasks
mihomes task add "Clean gutters" --property beach-house --recurrence quarterly
mihomes task list --property beach-house --overdue
mihomes task complete <task-id> --notes "All clear, no blockages"
mihomes task upcoming --days 14            # what's due in the next 2 weeks

# Issues
mihomes issue add "Leak under kitchen sink" --property beach-house --severity high
mihomes issue list --open --sort severity
mihomes issue resolve <issue-id> --notes "Replaced P-trap, tested for 24hrs"

# Spaces (rooms/areas within properties)
mihomes space add "Master Bedroom" --property beach-house --type bedroom
mihomes space add "Pool Area" --property beach-house --type outdoor
mihomes space list --property beach-house

# Staff & Vendors
mihomes staff add "Maria Santos" --role housekeeper --property beach-house
mihomes staff show maria-santos
mihomes staff schedule maria-santos            # view assigned tasks and workload
mihomes staff schedule --property beach-house  # all staff schedules for a property
mihomes staff workload                         # summary of task counts per staff member
mihomes vendor add "ABC Plumbing" --category plumbing --area "South Shore"
mihomes vendor rate <vendor-id> --quality 4 --reliability 5 --notes "Fast response"

# Financial
mihomes budget set --property beach-house --category maintenance --annual 25000
mihomes expense add 450 --vendor abc-plumbing --property beach-house --category maintenance
mihomes budget report --property beach-house --period Q1-2026
mihomes expense report --by-category --period 2026

# Assets
mihomes asset add "Sub-Zero Refrigerator" --property beach-house --space kitchen \
  --model "BI-36U" --installed 2020-03-15 --warranty-expires 2025-03-15
mihomes asset list --property beach-house --warranty-expiring 90
mihomes vehicle add "Range Rover" --property beach-house --year 2024 --vin XXX

# Events
mihomes event add "July 4th Party" --property beach-house --date 2026-07-04 --guests 40
mihomes guest add "John Smith" --dietary vegan --room-preference "ocean view suite"

# Edit & Delete (available on all entities)
mihomes property edit beach-house --name "Oceanfront Villa"
mihomes task edit <task-id> --priority urgent --assignee marco
mihomes issue edit <issue-id> --severity critical
mihomes vendor edit abc-plumbing --area "North Shore, South Shore"
mihomes property delete mountain-lodge      # requires confirmation
mihomes task delete <task-id>               # requires confirmation
# Pattern: mihomes <entity> edit <id-or-slug> --<field> <value>
# Pattern: mihomes <entity> delete <id-or-slug> (always prompts for confirmation)

# Notes (attachable to any entity)
mihomes note add --to property:beach-house "Neighbor contact: Tom at 555-1234"
mihomes note add --to issue:42 "Spoke with vendor, scheduling for next week"
mihomes note add --to vendor:abc-plumbing "Ask for Mike, he knows our systems"
mihomes note list --to property:beach-house

# Documents
mihomes document add warranty.pdf --to asset:sub-zero-fridge --type warranty \
  --expires 2027-03-15
mihomes document add "HOA Rules 2026.pdf" --to property:beach-house --type regulation
mihomes document list --property beach-house
mihomes document list --expiring 90         # documents expiring in 90 days

# Work Orders (Phase 3)
mihomes workorder create --from issue:42 --vendor abc-plumbing --estimate 850 \
  --due 2026-04-15
mihomes workorder list --property beach-house --open
mihomes workorder approve <wo-id>           # approve if above cost threshold
mihomes workorder complete <wo-id> --actual-cost 920 --notes "Additional fitting needed"

# Templates
mihomes template add "Summer Opening" --steps "Turn on water,Test faucets,Pool startup"
mihomes template list
mihomes template run "Summer Opening" --property beach-house --due 2026-05-01

# Tags
mihomes tag create "pre-sale"
mihomes tag apply "pre-sale" --to issue:42 task:87 task:88
mihomes task list --tag "pre-sale"

# Search
mihomes search "plumbing"                  # global search across all entities
mihomes search "leak" --type issues        # scoped search
mihomes search --tag "urgent"              # tag-based search

# Contracts
mihomes contract add --vendor abc-landscaping --property beach-house \
  --start 2026-04-01 --end 2027-03-31 --annual 18000 --auto-renew
mihomes contract list --expiring 60        # contracts expiring in 60 days

# Config
mihomes config set ai.provider claude
mihomes config set currency.default USD
mihomes config list

# Backup & Maintenance
mihomes backup                             # snapshot DB + media
mihomes restore backup-2026-03-27.tar.gz
mihomes doctor                             # integrity checks

# Audit
mihomes audit issues 42                    # full history of issue #42
mihomes audit --recent --days 7            # all changes in last 7 days

# WhatsApp Intelligence (Phase 3)
mihomes whatsapp setup                     # start bridge, display QR code for pairing
mihomes whatsapp link-group "House Staff" --property lakeside-estate
mihomes whatsapp status                    # gateway connection status, linked groups, message stats
mihomes whatsapp review                    # review AI-extracted issues/tasks from group chats
mihomes whatsapp review --property lakeside-estate
mihomes whatsapp review --accept 1,2,7,8   # bulk-confirm suggested items

# Insurance
mihomes insurance add --type homeowners --carrier "State Farm" --property beach-house \
  --coverage 2000000 --deductible 5000 --premium 12000 --renewal 2027-01-15
mihomes insurance list --expiring 90
mihomes insurance gaps                     # AI coverage adequacy review

# Recurring Expenses
mihomes recurring add "Pool Service" --amount 350 --frequency monthly \
  --vendor pool-pros --property beach-house --category maintenance
mihomes recurring list
mihomes recurring generate                 # create pending transactions for current period

# Alerts
mihomes alerts                             # all pending alerts by urgency
mihomes alerts --format brief              # one-line output for cron
mihomes alerts --format json               # machine-readable
mihomes alerts snooze <alert-id> --days 7  # defer non-urgent items

# Archive
mihomes archive stats                      # data volume per table
mihomes archive run                        # archive old audit/whatsapp/ai data

# Reports
mihomes report estate --period Q1-2026     # full estate quarterly summary
mihomes report property beach-house        # single property health report
mihomes report vendor abc-plumbing         # vendor performance summary
mihomes report spending --by-category --period 2026
mihomes report upcoming --days 30          # everything due in 30 days across all properties

# AI
mihomes ai ask "What should I prioritize this week?"
mihomes ai review                          # proactive AI review
mihomes ai plan --property beach-house --scenario "opening for summer"
mihomes ai budget-review --property beach-house
mihomes ai assess-issue <issue-id>
mihomes ai search "what did we do about the roof last year?"

# Dashboard
mihomes dashboard                          # full estate overview
mihomes dashboard --property beach-house   # single property focus
```

### 9.3 Dashboard Output (Rich Terminal)

```
╭─ MiHomes Estate Dashboard ─────────────────────────────────────╮
│                                                                  │
│  Properties (3)          Tasks Due This Week (7)                │
│  ┌────────────────────┐  ┌──────────────────────────────────┐   │
│  │ 🏠 Beach House     │  │ ⚠ HVAC filter change (Beach)     │   │
│  │   Status: Open     │  │ ⚠ Pool chemical test (Beach)     │   │
│  │   Issues: 2 open   │  │ ○ Lawn service (Mountain)        │   │
│  │                    │  │ ○ Gutter cleaning (City Apt)     │   │
│  │ 🏔 Mountain Lodge  │  │ ○ Deep clean kitchen (Beach)     │   │
│  │   Status: Closed   │  │ ...                              │   │
│  │   Issues: 0        │  │                                  │   │
│  │                    │  └──────────────────────────────────┘   │
│  │ 🏙 City Apartment  │                                        │
│  │   Status: Open     │  Budget Status (Q1 2026)               │
│  │   Issues: 1 open   │  ┌──────────────────────────────────┐   │
│  └────────────────────┘  │ Beach:    ██████████░░ 78% used   │   │
│                          │ Mountain: ████░░░░░░░░ 32% used   │   │
│  Open Issues (3)         │ City:     ███████░░░░░ 61% used   │   │
│  🔴 Roof leak (Beach)   │ Total:    $42,300 / $65,000       │   │
│  🟡 Squeaky door (City) │ └──────────────────────────────────┘   │
│  🟡 Fence repair (Beach)│                                        │
│                          │  AI Recommendations                   │
│                          │  → Schedule Mountain Lodge opening     │
│                          │    (you typically visit by April 15)   │
│                          │  → Beach house roof leak may worsen    │
│                          │    with forecast rain Thursday         │
│                          │  → HVAC warranty at City Apt expires   │
│                          │    in 30 days — schedule inspection    │
╰──────────────────────────────────────────────────────────────────╯
```

---

## 10. Implementation Phases

### Phase 1a: Core (MVP)
**Goal:** Get the essential data model and daily-use commands working

- `mihomes init` setup wizard with optional `--demo` sample data
- `mihomes version`, `mihomes help`, `--help` on all commands, tab completion via Typer
- SQLite database with schema migrations (alembic)
- Property CRUD with types (primary/vacation/seasonal/investment/rental/estate), status, and occupancy (manual entry via `occupy`/`vacate`)
- Task CRUD with recurrence engine (weekly/monthly/quarterly/seasonal/annual) and priority levels
- Issue tracking (create, list, update, resolve) with severity
- Staff and vendor directories (staff includes `whatsapp_phone` field for Phase 3 readiness)
- Basic expense logging and budget tracking (with `currency` column from day one)
- Dashboard command showing overview across properties
- Slug-based entity identification (auto-generated from name, user-overridable)
- Audit log — immutable record of all changes from day one
- CLI with Typer + Rich for formatted output

### Phase 1b: Supporting Features
**Goal:** Round out the MVP with operational depth

- Vendor contract tracking with renewal alerts
- Recurring expenses
- Insurance policy tracking with renewal alerts
- Templates and checklists — create, manage, instantiate into tasks
- Tags — create, apply to any entity, filter by tag
- Global search across all entity types
- Alerts system — centralized view of overdue tasks, expiring items, budget variances
- Unified reporting (`mihomes report`)
- Configuration system (`mihomes config`)
- Backup/restore commands and `mihomes doctor` integrity checks
- Data retention and archival commands
- CSV import/export with template files

### Phase 2: Intelligence
**Goal:** AI advisory layer with multi-provider support

- `AIProvider` abstraction with `ClaudeProvider` and `OpenAIProvider` implementations
- `ai ask` command with context-aware responses (routes to appropriate AI expert roles)
- `ai review` for proactive recommendations
- `ai search` for natural language querying across entities and history
- AI-assisted issue severity assessment
- AI-assisted task prioritization using SPACE framework
- AI-assisted import — paste unstructured text, AI parses into records for confirmation
- AI-suggested tags and priority when creating entities
- Conversation history storage for context continuity
- Cron-friendly output mode (`mihomes alerts --format brief`) with `mihomes cron setup` helper

### Phase 3: Depth + Communication
**Goal:** Full operational capability and staff communication

- Asset and inventory management (appliances, vehicles, valuables)
- Work order workflow (estimate → approve → assign → complete → verify)
- Vendor performance tracking and comparison
- Event and guest management with guest preference profiles
- Document storage and expiration tracking
- Seasonal checklists and templates (built-in starter templates)
- Advanced financial reports and forecasting
- **WhatsApp gateway via Baileys** — staff receive assignments, report completions, submit issues with photos
- WhatsApp group chats per property for staff coordination
- **Passive issue detection** — AI monitors group conversations and extracts issues/tasks into a review queue
- Google Calendar and MS365 Outlook integration (bidirectional sync via `CalendarProvider` abstraction)
- iCal file import
- Ollama/local model support for offline AI advisory

### Phase 4: Automation
**Goal:** Proactive and automated operations

- Scheduled AI reviews (daily/weekly digest via cron)
- Auto-generated task reminders and escalations (CLI + WhatsApp delivery)
- Weather-aware task scheduling (weather API integration)
- Smart reorder alerts for consumable inventory
- Cross-property optimization recommendations
- Occupancy-aware automation (calendar-triggered task workflows)
- Automated backup scheduling

### Phase 5: Scale (Future)
**Goal:** Multi-user, web UI, and integrations

- Web UI (for non-technical family members and staff)
- Full multi-user with role-based permissions and authentication
- Email and SMS notification gateways
- Multi-currency with live exchange rates
- Smart home sensor integration (temperature, humidity, water, security)
- API for third-party integrations
- Migration path to PostgreSQL if needed

---

## 11. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Startup time | < 500ms for any CLI command |
| Database size | Support up to 100 properties, 100K tasks, 10K assets without degradation |
| Offline operation | Full functionality without internet (except AI features) |
| Data portability | SQLite file is the entire database — easy backup/restore |
| AI response time | < 10s for advisory queries |
| Data privacy | All data stored locally, AI queries send only relevant context |
| Backup | Simple file copy; future: automated backup scheduling |

## 12. Testing Strategy

### Unit Tests
- All data access layer functions (CRUD operations, queries, recurrence calculations)
- Business logic: task scheduling, budget calculations, overdue detection, SPACE prioritization scoring
- Template instantiation (template → tasks with correct dependencies and assignments)
- Tag filtering and search logic
- CSV import parsing and validation

### Integration Tests
- Full CLI command execution via Typer's test runner (CliRunner)
- Database migrations — verify forward/backward migration integrity
- AI provider abstraction — mock provider tests ensuring all providers conform to the protocol
- WhatsApp gateway message parsing (inbound) and formatting (outbound)
- Calendar provider sync (mock API responses for Google/Outlook)

### End-to-End Tests
- Scenario-based: "Add a property, create recurring tasks, complete some, run dashboard, verify output"
- AI integration: verify context assembly (correct data gathered before sending to AI)
- Backup/restore round-trip: backup, modify data, restore, verify original state
- `mihomes doctor` detects intentionally introduced integrity problems

### Test Infrastructure
- pytest as test framework
- Factory Boy or fixtures for test data generation
- In-memory SQLite for fast unit tests, file-based SQLite for integration tests
- CI via GitHub Actions on every push

## 13. Error Handling & Data Integrity

### Database Integrity
- SQLite WAL mode for crash resilience
- Foreign key constraints enforced at the database level
- All multi-step operations wrapped in transactions (e.g., template instantiation creates multiple tasks atomically)
- `mihomes doctor` checks: orphaned media files, broken foreign key references, stale calendar events, audit log consistency

### AI Failure Handling
- All AI features are non-blocking — if the AI provider is unavailable, commands complete without AI enrichment
- Timeout: AI calls abort after 30 seconds with a user-visible warning, not an error
- Rate limiting: queue and retry with backoff, surface status to user
- Malformed AI responses: validate structured output before applying; fall back to raw text display if parsing fails
- No AI response is auto-committed — all AI-generated records (issues, tasks, import data) require user confirmation

### WhatsApp Gateway Resilience
- Inbound message queue: messages are stored locally and processed even if MiHomes was offline when received
- Outbound retry: failed message delivery retried 3 times with exponential backoff, then queued for next cycle
- Message validation: AI parses inbound staff messages but flags low-confidence interpretations for admin review rather than auto-creating records
- Gateway down: staff can still be managed via CLI; WhatsApp is an enhancement, not a dependency

### General Principles
- Never silently discard data — if an operation partially fails, show what succeeded and what didn't
- All destructive operations (delete, restore, bulk import) require explicit confirmation
- Corrupt or unreadable backup files are detected before restore begins, not mid-restore

## 14. Key Design Principles

1. **AI-first, not AI-only** — Every feature works without AI. AI enhances but never gates functionality.
2. **Local-first** — Your data stays on your machine. AI queries send minimal context.
3. **Convention over configuration** — Sensible defaults, override when needed.
4. **Progressive disclosure** — Simple commands for common tasks, full power available when needed.
5. **Single source of truth** — One database, one CLI, no data duplication.
6. **Proactive > Reactive** — The system should surface issues before the user asks.
7. **Graceful degradation** — AI down? WhatsApp down? Calendar API down? Core functionality continues uninterrupted.
8. **Confirm before commit** — AI-generated records, destructive operations, and bulk imports always require explicit user confirmation.

## 15. Success Metrics

- All recurring tasks tracked and none missed
- Issue resolution time decreasing over time
- Budget variance below 10% per property per quarter
- AI recommendations acted on > 50% of the time
- Zero data loss incidents
- User spends less time managing logistics, more time enjoying properties

## 16. Open Questions (Resolved)

### 16.1 Calendar Integration

How should we track property occupancy and family schedules?

| Option | Pros | Cons |
|---|---|---|
| **Google Calendar API** | Real-time sync, widely used, shared calendars | OAuth setup, Google dependency, privacy concern |
| **MS365 Outlook API** | Real-time sync, common in professional/family office contexts | OAuth + Microsoft Graph API complexity, Azure AD setup |
| **iCal file import** | Standard format, works with any calendar app, no API dependency | One-way sync (manual re-import), no real-time updates, stale data risk |
| **Manual entry via CLI** | Zero dependencies, full privacy, works offline | Tedious to maintain, easy to forget updates, no sync with existing calendars |
| **Multi-provider (Google + Outlook + iCal)** | Covers all major calendar ecosystems, user picks what they use | More code to maintain, must handle auth for both Google and Microsoft |

**Decision:** **Multi-provider calendar integration** behind a `CalendarProvider` abstraction.
- **Phase 1:** Manual entry via CLI (`mihomes property occupy/vacate`) + iCal file import for bulk loading
- **Phase 3:** Google Calendar API and MS365 Outlook API as selectable providers, bidirectional sync
- Architecture: a `CalendarProvider` protocol that each provider implements (`GoogleCalendarProvider`, `OutlookCalendarProvider`, `ICalFileProvider`), configured via `mihomes config set calendar.provider google|outlook|ical|manual`

### 16.2 Photo Storage

How should we handle photos for issues, inspections, assets, and properties?

| Option | Pros | Cons |
|---|---|---|
| **Filesystem with DB references** | No DB bloat, easy to view/manage photos with standard tools, fast CLI operations, works with any file size | Files can get orphaned if moved/deleted outside MiHomes, backup requires copying both DB + photo directory |
| **Embed in SQLite (BLOB)** | Single-file backup, no orphaned files, atomic with DB transactions | DB bloat (photos are large), slower queries, harder to view photos outside the app, SQLite not optimized for large blobs |
| **Filesystem with content-hash naming** | Same as filesystem + immutable/deduped, no collisions | Same orphan risk, hash filenames are not human-readable |

**Recommendation:** **Filesystem with DB references**. Store photos in a `~/.mihomes/media/` directory organized by entity type and ID (e.g., `issues/42/photo1.jpg`). The DB stores the relative path. This keeps the SQLite file small and fast, photos are viewable with any tool, and backup is just copying the `~/.mihomes/` directory. Add a `mihomes doctor` command that detects orphaned or missing files.

### 16.3 Multi-Currency

Do we need to support multiple currencies for international properties?

| Option | Pros | Cons |
|---|---|---|
| **Single currency (USD)** | Simple, no conversion logic, no exchange rate maintenance | Breaks for international properties, forces manual conversion |
| **Multi-currency with manual rates** | Supports international properties, user controls rates, no API dependency | User must update rates manually, reporting across currencies requires a base currency |
| **Multi-currency with live rates (API)** | Accurate conversions, automatic | API dependency, rate fluctuations complicate historical reporting, overkill for most users |

**Recommendation:** **Single currency default, with a currency field per property** in the schema from day one. Phase 1 stores amounts in each property's local currency and displays them as-is. Phase 3 adds a base currency setting and manual exchange rates for consolidated reporting. Live rates are a Phase 5 nice-to-have. The key is getting the schema right now so we don't need a migration later — just store a `currency` column on `properties` and `transactions` from the start.

### 16.4 Notification Delivery

How do we alert the user about overdue tasks, expiring warranties, and urgent issues?

| Option | Pros | Cons |
|---|---|---|
| **CLI-only (check when you run it)** | Zero setup, no external dependencies, full privacy | Easy to miss time-sensitive items if you don't run the CLI regularly |
| **Desktop notifications (system)** | Visible without opening CLI, works locally | Platform-specific implementation, requires a background daemon |
| **Email** | Reaches user anywhere, asynchronous, good for digests | Requires email config (SMTP), deliverability issues, privacy concern |
| **SMS (Twilio/similar)** | Immediate for urgent items, highest attention | Cost, API dependency, phone number storage, overkill for most alerts |
| **Cron + CLI summary** | Simple, leverages existing OS tools, no daemon needed | Unix-only, user must set up cron, limited to scheduled intervals |

**Decision:** **Cron + CLI as the core, WhatsApp as the staff communication gateway.**
- **Phase 1:** CLI-only — dashboard highlights urgent items, `mihomes alerts` shows pending notifications
- **Phase 2:** Cron-friendly output mode (`mihomes alerts --format brief`) pipeable to any notification tool via cron job. Add `mihomes cron setup` helper to configure common schedules (daily digest, hourly urgent-only)
- **Phase 3:** **WhatsApp gateway via Baileys** for staff and staff group communication. Staff can:
  - Receive task assignments and reminders via WhatsApp
  - Report task completion by replying to messages
  - Submit new issues with photos directly from WhatsApp
  - Participate in property-specific staff group chats managed by MiHomes
  - Architecture: a `NotificationGateway` protocol with `WhatsAppGateway` (Baileys Node.js bridge), `CLIGateway`, and future `EmailGateway` implementations
- **Phase 5:** Email/SMS as additional gateway options

### 16.5 AI Model Flexibility

Should we support multiple LLM providers or build exclusively on Claude?

| Option | Pros | Cons |
|---|---|---|
| **Claude-only** | Simpler code, can use Claude-specific features (long context, tool use), consistent behavior, easier to tune prompts | Vendor lock-in, user must have Anthropic API key, no fallback if API is down |
| **Multi-provider (OpenAI, Claude, local models)** | User choice, cost flexibility, can use local models for privacy, fallback options | Abstraction layer adds complexity, lowest-common-denominator prompts, inconsistent quality across models, harder to test |
| **Claude-primary with provider abstraction** | Best of both — optimized for Claude but swappable, can add local model support later | Some abstraction overhead, still need to test with each provider |
| **NVIDIA NIM (e.g. Qwen3.5-122B-A10B)** | OpenAI-compatible API, free tier credits on signup, access to top open-weight models (Qwen, Llama, Mistral), no vendor lock-in on model choice | Cloud dependency (not offline), free tier has usage limits, model availability subject to NVIDIA catalog |

**Decision:** **Multi-provider from the start** behind an `AIProvider` abstraction.
- **Phase 2:** Ship with Claude as the default and recommended provider. Build an `AIProvider` protocol (`complete()`, `tool_call()`, `structured_output()`) with `ClaudeProvider` and `OpenAIProvider` implementations. Prompts optimized for Claude but functional on OpenAI models.
- **Phase 3:** Add local model support via Ollama for fully offline AI advisory (useful for privacy-sensitive operations and environments without internet)
- **Phase 3:** Add NVIDIA NIM provider (`NIMProvider`) using the OpenAI-compatible API at `https://integrate.api.nvidia.com/v1`. Recommended model: `qwen/qwen3.5-122b-a10b`. Configured via `NVIDIA_API_KEY` env var or `mihomes ai setup`. Reuses the OpenAI SDK with a custom `base_url` — no additional dependencies required.
- Configuration: `mihomes config set ai.provider claude|openai|ollama|nim` with per-provider API key management
- Provider-specific features (Claude's long context, OpenAI's function calling) used opportunistically via capability flags on the provider interface

### 16.6 Collaboration / Multi-User

When does MiHomes need to support multiple users?

| Option | Pros | Cons |
|---|---|---|
| **Single-user forever** | Maximum simplicity, no auth/permissions, no sync conflicts | Only one person can manage, doesn't scale to estate teams |
| **Multi-user in Phase 3** | Enables staff to log task completion, vendors to update work orders | Significant complexity (auth, permissions, conflict resolution), distracts from core features |
| **Multi-user in Phase 5 (with web UI)** | Natural pairing — web UI makes multi-user accessible, core features are mature first | Delays collaboration, single-user may feel limiting for estate teams |
| **Shared SQLite via file sync (Dropbox/Syncthing)** | Simple "poor man's multi-user", no auth code needed | Write conflicts, no permissions, no audit trail, data corruption risk |

**Decision:** **Single admin user via CLI, staff participate via WhatsApp gateway.**
- **Phase 1-2:** Single admin user (estate manager/owner) operates via CLI. `audit_log` table from day one tracks all changes with timestamps.
- **Phase 3:** Staff become lightweight participants via the WhatsApp gateway (see 16.4) — they can receive assignments, report completions, and submit issues, but all management decisions remain with the admin via CLI. This gives multi-user *participation* without multi-user *complexity* (no auth system, no role-based permissions, no conflict resolution).
- **Phase 5:** Full multi-user with web UI and role-based permissions for when the estate operation needs multiple administrators or family members want direct access.

### 16.7 Import/Export

How should users bootstrap the system with existing data?

| Option | Pros | Cons |
|---|---|---|
| **No import, manual entry only** | Clean data from the start, no parser edge cases | Painful onboarding if user has dozens of properties/assets/vendors |
| **CSV import** | Universal format, user can export from spreadsheets, batch loading | Column mapping complexity, error handling for bad data, each entity type needs its own import format |
| **JSON import** | Structured, less ambiguous than CSV, good for programmatic use | Less familiar to non-technical users, harder to hand-edit |
| **AI-assisted import** | User describes data in natural language or pastes unstructured text, AI parses it | Depends on AI accuracy, non-deterministic, could create bad data |
| **CSV + AI-assisted** | CSV for structured bulk data, AI for messy/unstructured sources | Two paths to maintain, but covers the most ground |

**Decision:** **AI-assisted import as the primary onboarding experience**, with CSV as a structured fallback.
- **Phase 1:** CSV import/export with template files (`mihomes export --template properties > properties.csv`). Strict validation with clear error messages.
- **Phase 2:** **AI-assisted import** as the preferred onboarding path:
  - `mihomes import --ai` opens an interactive session where users paste unstructured text (vendor lists from emails, maintenance schedules from PDFs, property details from listing sites, spreadsheet dumps)
  - AI parses into structured records and presents for confirmation: "I found 12 vendors in that text. Here they are — confirm, edit, or reject each"
  - Supports iterative refinement: "That vendor is actually two separate companies" → AI re-parses
  - Photo/document import: `mihomes import --ai --file contractor_list.pdf` for PDF parsing
- **Export:** Always available in CSV and JSON formats. Users should never feel locked in.

---

## Appendix A: Competitive Landscape Summary

| Category | Examples | Strength | Gap for MiHomes |
|---|---|---|---|
| Property Management | Buildium, AppFolio, Rent Manager | Tenant/lease management, accounting | Designed for landlords, not owner-occupied estates |
| STR Operations | Breezeway, Properly, Turno | Booking-triggered cleaning, photo verification | Guest turnover focus, not lifestyle management |
| Personal Home | HomeZada, Centriq | Maintenance reminders, product intelligence | Single-home, no staff/vendor/AI depth |
| Multi-Property | Guesty, Hostaway | Channel distribution, revenue optimization | Revenue-focused, not estate operations |
| Luxury Estate | Fragmented (Monday + QuickBooks + Sheets) | None unified | **This is the gap MiHomes fills** |

## Appendix B: AI Role Quick Reference

See Section 8.2 for the full table. The 13 AI specialist roles cover: estate management, maintenance, finance, vendors, hospitality, housekeeping, grounds, fleet, security, energy, assets, compliance, and lifestyle.

## Appendix C: SPACE Framework Quick Reference

**S**afety → **P**resence → **A**sset Protection → **C**ompliance → **E**conomy

Used by AI to explain and justify all prioritization decisions. Every recommendation includes its SPACE classification.
