# MiHomes

**AI-first multi-home estate management system.**

MiHomes unifies property operations, staff coordination, vendor management, financial tracking, and lifestyle services into a single CLI-powered platform with AI advisory capabilities.

## Quick Start

```bash
# Install
pip install -e .

# Initialize with sample data
mihomes init --demo

# See the dashboard
mihomes dashboard

# Explore
mihomes property list
mihomes task list --overdue
mihomes issue list --open
mihomes alerts
```

## Features

### Property Management
```bash
mihomes property add "Beach House" --address "123 Ocean Dr" --type vacation --climate-zone northeast
mihomes property occupy beach-house --from 2026-06-01 --to 2026-08-31
mihomes space add "Master Bedroom" --property beach-house --type bedroom
```

### Task Management with Recurrence
```bash
mihomes task add "Clean gutters" --property beach-house --recurrence quarterly --due 2026-04-01
mihomes task complete <id> --notes "All clear"     # auto-creates next quarterly occurrence
mihomes task upcoming --days 14
```

Supports: weekly, biweekly, monthly, quarterly, seasonal (climate-zone-aware), annual.

### Issue Tracking
```bash
mihomes issue add "Roof leak" --property beach-house --severity high
mihomes issue resolve <id> --notes "Replaced flashing"
```

### Staff & Vendors
```bash
mihomes staff add "Maria Santos" --role housekeeper --property beach-house
mihomes vendor add "ABC Plumbing" --category plumbing --area "South Shore"
mihomes vendor rate <id> --quality 4 --reliability 5
```

### Financial Management
```bash
mihomes budget set --property beach-house --category maintenance --amount 25000
mihomes expense add 450 --property beach-house --category maintenance --vendor abc-plumbing
mihomes budget report --property beach-house
mihomes recurring add "Pool Service" --amount 350 --frequency monthly --property beach-house --category maintenance
mihomes report spending --property beach-house --by vendor
```

### Asset & Inventory
```bash
mihomes asset add "Sub-Zero Refrigerator" --type appliance --property beach-house --warranty 2027-03-15
mihomes asset list --property beach-house --warranty-expiring 90
```

### Work Orders
```bash
mihomes workorder create "Fix roof leak" --property beach-house --from issue:1 --vendor abc-plumbing --estimate 850
mihomes workorder approve <id>
mihomes workorder complete <id> --actual-cost 920 --notes "Extra fitting needed"
```

### Templates & Seasonal Checklists
```bash
mihomes template seed                    # load 8 built-in seasonal templates
mihomes template run spring-opening --property beach-house --due 2026-05-01
mihomes template add "Custom Checklist" --steps "Step 1,Step 2,Step 3"
```

### Events & Guests
```bash
mihomes event add "July 4th Party" --property beach-house --date 2026-07-04 --guests 40
mihomes guest add "John Smith" --dietary vegan
mihomes guest invite john-smith --to july-4th-party
```

### Tags, Search, Notes
```bash
mihomes tag create "urgent-repair"
mihomes tag apply urgent-repair --to issue:1 --to task:5
mihomes search "plumbing"
mihomes note add "Neighbor has spare key" --to property:beach-house
```

### AI Advisory (requires API key)
```bash
export ANTHROPIC_API_KEY=sk-...
mihomes ai ask "What should I prioritize this week?"
mihomes ai review                        # proactive estate recommendations
mihomes ai search "what did we do about the roof last year?"
mihomes ai assess-issue <id>             # AI severity evaluation
mihomes ai prioritize                    # SPACE-ranked task ordering
mihomes ai import vendor                 # paste text, AI parses into records
mihomes ai setup                         # configure provider and key
```

Supports Claude (default), OpenAI, and Ollama (local).

### Automation
```bash
mihomes auto run-all                     # escalation + expiration alerts + digest
mihomes auto digest --format brief       # cron-ready daily summary
mihomes auto escalate --days 7           # bump priority on overdue tasks
mihomes cron setup                       # recommended crontab entries
```

### WhatsApp Staff Gateway
```bash
cd bridge && npm install && npm start    # start Baileys bridge
mihomes whatsapp setup                   # pairing instructions
mihomes whatsapp link-group "Staff Chat" --property beach-house
mihomes whatsapp review                  # AI extracts issues/tasks from conversations
mihomes whatsapp send +1234567890 "HVAC filter is due"
```

### Backup & Maintenance
```bash
mihomes backup                           # snapshot DB + media
mihomes backup list
mihomes doctor                           # integrity checks
mihomes config list                      # view all settings
```

### CSV Import/Export
```bash
mihomes export csv property              # export all properties
mihomes export csv property --template   # empty template with headers
mihomes import-csv csv vendor vendors.csv
```

## Architecture

```
src/mihomes/
├── cli/          # Typer CLI commands (thin layer)
├── models/       # SQLAlchemy ORM models
├── services/     # Business logic
│   ├── ai/       # AI provider abstraction + roles + context
│   └── gateways/ # WhatsApp bridge client, Calendar, iCal
├── db.py         # SQLite engine + session management
└── config.py     # Paths and configuration

bridge/           # Node.js WhatsApp bridge (Baileys)
```

**Key patterns:** CLI → Service → Model. AI uses SPACE prioritization (Safety > Presence > Asset Protection > Compliance > Economy). All changes logged to immutable audit trail.

## AI Expert Roles

MiHomes routes queries to specialized AI advisors:

| Role | Handles |
|------|---------|
| Estate Manager | Cross-property orchestration, prioritization |
| Maintenance Advisor | Repairs, preventive scheduling, failure prediction |
| Financial Analyst | Budget variance, cost optimization, forecasting |
| Vendor Strategist | Performance analysis, contract negotiation |
| Compliance Monitor | Insurance, permits, contract renewals |

## Tech Stack

- **Python 3.11+** with Typer + Rich
- **SQLite** with WAL mode and Alembic migrations
- **Claude / OpenAI / Ollama** for AI advisory
- **Baileys** (Node.js) for WhatsApp integration
- Local-first, single-user, zero cloud infrastructure required

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — Creative Commons Attribution-NonCommercial 4.0 International
