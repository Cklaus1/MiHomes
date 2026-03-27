# MiHomes — Product Requirements Document

**Version:** 1.0
**Date:** 2026-03-27
**Status:** Draft

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
| AI Integration | Claude API (Anthropic) | Best reasoning for advisory/planning tasks |
| Architecture | Local-first, single-user | Privacy, simplicity, no infrastructure cost |

### Data Model (Core Entities)

```
Properties        — homes, addresses, features, status
Spaces            — rooms/areas within properties
Staff             — household employees, roles, certifications, assignments
Vendors           — contractors, service providers, ratings, contracts
Tasks             — recurring and one-off work items
TaskSchedules     — recurrence rules (weekly/monthly/quarterly/seasonal/annual)
Issues            — problems discovered, linked to property/space
WorkOrders        — vendor/staff assignments for tasks or issues
Assets            — inventory items, appliances, vehicles, valuables
Budgets           — per-property and per-category budget targets
Transactions      — income and expenses, linked to properties/vendors/categories
Events            — planned gatherings, guest visits
Guests            — guest profiles, preferences, dietary info
Documents         — files, warranties, manuals, contracts, policies
Notes             — free-form notes linked to any entity
AIConversations   — history of AI advisory sessions
```

## 5. Feature Specification

### 5.1 Property Management (Core)

**Properties & Spaces**
- Register properties with address, type, features, square footage, seasonal status (open/closed/caretaker-mode)
- Define spaces (rooms, garages, yards, pools, outbuildings) per property
- Track occupancy status and family calendar integration
- Seasonal opening/closing checklists per property

**Task Management**
- Create tasks with recurrence: weekly, biweekly, monthly, quarterly, seasonal, annual, or custom cron
- Assign tasks to staff or vendors
- Track completion with timestamps and notes
- Overdue task alerts and escalation
- Task templates for common operations (pre-arrival prep, seasonal closing, post-storm inspection)
- Dependency chains (task B cannot start until task A completes)

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

### 5.2 Staff Management

- Staff profiles: name, role, properties assigned, certifications, contact info
- Role types: housekeeper, groundskeeper, property manager, driver, chef, security, personal assistant, other
- Task assignment and workload visibility
- Availability and scheduling (which staff at which property, when)
- Performance notes (not ratings — qualitative observations)
- Certification and license expiration tracking

### 5.3 Vendor Management

- Vendor profiles: company, contacts, service categories, service areas, insurance info
- Link vendors to properties and service categories
- Track performance: response time, quality notes, reliability, pricing history
- Contract management: terms, renewal dates, auto-renewal alerts
- Preferred vendor designation per property per service category
- Compare vendor costs against budget benchmarks

### 5.4 Financial Management

- **Budgets:** Set annual/quarterly/monthly budgets per property and per category (maintenance, landscaping, cleaning, utilities, staff, events, vehicles, supplies)
- **Transactions:** Log expenses and income, categorize by property/vendor/category
- **Budget vs. Actual:** Real-time tracking with variance alerts
- **Forecasting:** Project future spend based on known scheduled tasks, contracts, and historical patterns
- **Reports:** Spending by property, by category, by vendor, by time period
- **Cost anomaly detection:** Flag expenses significantly above historical norms

### 5.5 Asset & Inventory Management

- **Appliances & Systems:** Model, serial number, install date, warranty expiration, maintenance schedule, location
- **Vehicles:** Make, model, year, VIN, location, registration/insurance dates, maintenance schedule, mileage
- **Valuables:** Art, collectibles, wine — with provenance, appraisal value, insurance coverage, storage conditions
- **Consumables:** Cleaning supplies, pantry basics, linens — with par levels and reorder alerts
- **Equipment:** Tools, outdoor gear, AV systems, smart home devices — with firmware/warranty tracking

### 5.6 Event & Hospitality Management

- Plan events with guest lists, vendor coordination, budgets, timelines
- Guest profiles with preferences (dietary, room, activities)
- Pre-arrival checklists triggered by guest visits
- Post-event task generation (cleanup, inventory restock, vendor review)
- Event history and cost tracking for future reference

### 5.7 Seasonal & Weather Awareness

- Property climate zone tracking
- Seasonal task templates (winterization, spring opening, hurricane prep, wildfire prep)
- Weather alert integration (future: API-based; initial: manual input)
- Automated seasonal checklist activation based on calendar

### 5.8 Documents & Knowledge Base

- Store documents linked to any entity (warranties, contracts, manuals, insurance policies, permits)
- Expiration date tracking with renewal alerts
- SOP library for staff procedures
- Property-specific house manuals

---

## 6. AI System Design

### 6.1 Philosophy

MiHomes treats AI as a **team of specialist advisors**, not a single chatbot. Each AI role has a defined scope, data context, and decision framework. The user interacts through a unified interface, but the system routes queries to the appropriate specialist context.

### 6.2 AI Expert Roles

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

### 6.3 AI Interaction Model

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

### 6.4 SPACE Prioritization Framework

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

## 7. CLI Interface Design

### 7.1 Command Structure

```bash
mihomes <entity> <action> [options]
```

### 7.2 Core Commands

```bash
# Properties
mihomes property add "Beach House" --address "123 Ocean Dr" --type vacation
mihomes property list
mihomes property show beach-house
mihomes property status                    # overview of all properties

# Tasks
mihomes task add "Clean gutters" --property beach-house --recurrence quarterly
mihomes task list --property beach-house --overdue
mihomes task complete <task-id> --notes "All clear, no blockages"
mihomes task upcoming --days 14            # what's due in the next 2 weeks

# Issues
mihomes issue add "Leak under kitchen sink" --property beach-house --severity high
mihomes issue list --open --sort severity
mihomes issue resolve <issue-id> --notes "Replaced P-trap, tested for 24hrs"

# Staff & Vendors
mihomes staff add "Maria Santos" --role housekeeper --property beach-house
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

# AI
mihomes ai ask "What should I prioritize this week?"
mihomes ai review                          # proactive AI review
mihomes ai plan --property beach-house --scenario "opening for summer"
mihomes ai budget-review --property beach-house
mihomes ai assess-issue <issue-id>

# Dashboard
mihomes dashboard                          # full estate overview
mihomes dashboard --property beach-house   # single property focus
```

### 7.3 Dashboard Output (Rich Terminal)

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

## 8. Implementation Phases

### Phase 1: Foundation (MVP)
**Goal:** Core data management and task tracking via CLI

- SQLite database with schema migrations (alembic)
- Property CRUD
- Task CRUD with recurrence engine (weekly/monthly/quarterly/seasonal/annual)
- Issue tracking (create, list, update, resolve)
- Staff and vendor directories
- Basic expense logging and budget tracking
- Dashboard command showing overview across properties
- CLI with Typer + Rich for formatted output

### Phase 2: Intelligence
**Goal:** AI advisory layer

- Claude API integration for natural language interaction
- `ai ask` command with context-aware responses
- `ai review` for proactive recommendations
- AI-assisted issue severity assessment
- AI-assisted task prioritization using SPACE framework
- Conversation history storage for context continuity

### Phase 3: Depth
**Goal:** Full operational capability

- Asset and inventory management (appliances, vehicles, valuables)
- Work order workflow (estimate → approve → assign → complete → verify)
- Vendor performance tracking and comparison
- Event and guest management
- Document storage and expiration tracking
- Seasonal checklists and templates
- Advanced financial reports and forecasting

### Phase 4: Automation
**Goal:** Proactive and automated operations

- Scheduled AI reviews (daily/weekly digest)
- Auto-generated task reminders and escalations
- Weather-aware task scheduling (API integration)
- Smart reorder alerts for consumable inventory
- Cross-property optimization recommendations
- Calendar integration for occupancy-aware automation

### Phase 5: Scale (Future)
**Goal:** Multi-user and beyond CLI

- Web UI (optional, for non-technical family members)
- Multi-user access with role-based permissions
- Mobile notifications
- Smart home sensor integration
- API for third-party integrations
- Migration path to PostgreSQL if needed

---

## 9. Non-Functional Requirements

| Requirement | Target |
|---|---|
| Startup time | < 500ms for any CLI command |
| Database size | Support up to 100 properties, 100K tasks, 10K assets without degradation |
| Offline operation | Full functionality without internet (except AI features) |
| Data portability | SQLite file is the entire database — easy backup/restore |
| AI response time | < 10s for advisory queries |
| Data privacy | All data stored locally, AI queries send only relevant context |
| Backup | Simple file copy; future: automated backup scheduling |

## 10. Key Design Principles

1. **AI-first, not AI-only** — Every feature works without AI. AI enhances but never gates functionality.
2. **Local-first** — Your data stays on your machine. AI queries send minimal context.
3. **Convention over configuration** — Sensible defaults, override when needed.
4. **Progressive disclosure** — Simple commands for common tasks, full power available when needed.
5. **Single source of truth** — One database, one CLI, no data duplication.
6. **Proactive > Reactive** — The system should surface issues before the user asks.

## 11. Success Metrics

- All recurring tasks tracked and none missed
- Issue resolution time decreasing over time
- Budget variance below 10% per property per quarter
- AI recommendations acted on > 50% of the time
- Zero data loss incidents
- User spends less time managing logistics, more time enjoying properties

## 12. Open Questions

### 12.1 Calendar Integration

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

### 12.2 Photo Storage

How should we handle photos for issues, inspections, assets, and properties?

| Option | Pros | Cons |
|---|---|---|
| **Filesystem with DB references** | No DB bloat, easy to view/manage photos with standard tools, fast CLI operations, works with any file size | Files can get orphaned if moved/deleted outside MiHomes, backup requires copying both DB + photo directory |
| **Embed in SQLite (BLOB)** | Single-file backup, no orphaned files, atomic with DB transactions | DB bloat (photos are large), slower queries, harder to view photos outside the app, SQLite not optimized for large blobs |
| **Filesystem with content-hash naming** | Same as filesystem + immutable/deduped, no collisions | Same orphan risk, hash filenames are not human-readable |

**Recommendation:** **Filesystem with DB references**. Store photos in a `~/.mihomes/media/` directory organized by entity type and ID (e.g., `issues/42/photo1.jpg`). The DB stores the relative path. This keeps the SQLite file small and fast, photos are viewable with any tool, and backup is just copying the `~/.mihomes/` directory. Add a `mihomes doctor` command that detects orphaned or missing files.

### 12.3 Multi-Currency

Do we need to support multiple currencies for international properties?

| Option | Pros | Cons |
|---|---|---|
| **Single currency (USD)** | Simple, no conversion logic, no exchange rate maintenance | Breaks for international properties, forces manual conversion |
| **Multi-currency with manual rates** | Supports international properties, user controls rates, no API dependency | User must update rates manually, reporting across currencies requires a base currency |
| **Multi-currency with live rates (API)** | Accurate conversions, automatic | API dependency, rate fluctuations complicate historical reporting, overkill for most users |

**Recommendation:** **Single currency default, with a currency field per property** in the schema from day one. Phase 1 stores amounts in each property's local currency and displays them as-is. Phase 3 adds a base currency setting and manual exchange rates for consolidated reporting. Live rates are a Phase 5 nice-to-have. The key is getting the schema right now so we don't need a migration later — just store a `currency` column on `properties` and `transactions` from the start.

### 12.4 Notification Delivery

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
- **Phase 3:** **WhatsApp Business API gateway** for staff and staff group communication. Staff can:
  - Receive task assignments and reminders via WhatsApp
  - Report task completion by replying to messages
  - Submit new issues with photos directly from WhatsApp
  - Participate in property-specific staff group chats managed by MiHomes
  - Architecture: a `NotificationGateway` protocol with `WhatsAppGateway` (via WhatsApp Business API / Cloud API), `CLIGateway`, and future `EmailGateway` implementations
- **Phase 5:** Email/SMS as additional gateway options

### 12.5 AI Model Flexibility

Should we support multiple LLM providers or build exclusively on Claude?

| Option | Pros | Cons |
|---|---|---|
| **Claude-only** | Simpler code, can use Claude-specific features (long context, tool use), consistent behavior, easier to tune prompts | Vendor lock-in, user must have Anthropic API key, no fallback if API is down |
| **Multi-provider (OpenAI, Claude, local models)** | User choice, cost flexibility, can use local models for privacy, fallback options | Abstraction layer adds complexity, lowest-common-denominator prompts, inconsistent quality across models, harder to test |
| **Claude-primary with provider abstraction** | Best of both — optimized for Claude but swappable, can add local model support later | Some abstraction overhead, still need to test with each provider |

**Decision:** **Multi-provider from the start** behind an `AIProvider` abstraction.
- **Phase 2:** Ship with Claude as the default and recommended provider. Build an `AIProvider` protocol (`complete()`, `tool_call()`, `structured_output()`) with `ClaudeProvider` and `OpenAIProvider` implementations. Prompts optimized for Claude but functional on OpenAI models.
- **Phase 3:** Add local model support via Ollama for fully offline AI advisory (useful for privacy-sensitive operations and environments without internet)
- Configuration: `mihomes config set ai.provider claude|openai|ollama` with per-provider API key management
- Provider-specific features (Claude's long context, OpenAI's function calling) used opportunistically via capability flags on the provider interface

### 12.6 Collaboration / Multi-User

When does MiHomes need to support multiple users?

| Option | Pros | Cons |
|---|---|---|
| **Single-user forever** | Maximum simplicity, no auth/permissions, no sync conflicts | Only one person can manage, doesn't scale to estate teams |
| **Multi-user in Phase 3** | Enables staff to log task completion, vendors to update work orders | Significant complexity (auth, permissions, conflict resolution), distracts from core features |
| **Multi-user in Phase 5 (with web UI)** | Natural pairing — web UI makes multi-user accessible, core features are mature first | Delays collaboration, single-user may feel limiting for estate teams |
| **Shared SQLite via file sync (Dropbox/Syncthing)** | Simple "poor man's multi-user", no auth code needed | Write conflicts, no permissions, no audit trail, data corruption risk |

**Decision:** **Single admin user via CLI, staff participate via WhatsApp gateway.**
- **Phase 1-2:** Single admin user (estate manager/owner) operates via CLI. `audit_log` table from day one tracks all changes with timestamps.
- **Phase 3:** Staff become lightweight participants via the WhatsApp gateway (see 12.4) — they can receive assignments, report completions, and submit issues, but all management decisions remain with the admin via CLI. This gives multi-user *participation* without multi-user *complexity* (no auth system, no role-based permissions, no conflict resolution).
- **Phase 5:** Full multi-user with web UI and role-based permissions for when the estate operation needs multiple administrators or family members want direct access.

### 12.7 Import/Export

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

See Section 6.2 for the full table. The 13 AI specialist roles cover: estate management, maintenance, finance, vendors, hospitality, housekeeping, grounds, fleet, security, energy, assets, compliance, and lifestyle.

## Appendix C: SPACE Framework Quick Reference

**S**afety → **P**resence → **A**sset Protection → **C**ompliance → **E**conomy

Used by AI to explain and justify all prioritization decisions. Every recommendation includes its SPACE classification.
