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

---

## Features

### Property Management
```bash
mihomes property add "Beach House" --address "123 Ocean Dr" --type vacation --climate-zone northeast
mihomes property list
mihomes property show beach-house
mihomes property occupy beach-house --from 2026-06-01 --to 2026-08-31
mihomes space add "Master Bedroom" --property beach-house --type bedroom
mihomes space list --property beach-house
```

### Task Management
```bash
mihomes task add "Clean gutters" --property beach-house --recurrence quarterly --due 2026-04-01
mihomes task list                                  # all open tasks
mihomes task list --overdue                        # overdue only
mihomes task list --property beach-house           # scoped to property
mihomes task upcoming --days 14                    # due in next 14 days
mihomes task by-frequency                          # grouped by daily/weekly/monthly/etc.
mihomes task by-frequency -f quarterly             # just quarterly tasks
mihomes task by-frequency -p beach-house           # scoped to property
mihomes task by-frequency -a diego                 # just one staff member's tasks
mihomes task complete <id> --notes "All clear"     # auto-creates next recurrence
mihomes task edit <id> --due 2026-05-01 --assignee diego-regalado
mihomes task show <id>
```

Recurrence options: `daily`, `weekly`, `biweekly`, `monthly`, `quarterly`, `seasonal` (climate-zone-aware), `annual`, `once`.

### Issue Tracking
```bash
mihomes issue add "Roof leak" --property beach-house --severity high
mihomes issue list --open
mihomes issue list --severity critical
mihomes issue resolve <id> --notes "Replaced flashing"
mihomes issue show <id>
```

### Staff Management
```bash
mihomes staff add "Maria Santos" --role housekeeper --property beach-house --phone 770-555-0100 --whatsapp +17705550100
mihomes staff list
mihomes staff show maria-santos
mihomes staff edit maria-santos --phone 770-555-0199
mihomes staff assign maria-santos --property second-home
mihomes staff schedule                             # all staff tasks grouped by person
mihomes staff schedule maria-santos                # one staff member
mihomes staff schedule --property beach-house      # all staff at one property
mihomes staff workload                             # task counts per staff member

# PTO
mihomes staff pto-requests                         # all pending/approved/denied requests
mihomes staff pto-requests --status pending        # filter by status
mihomes staff pto diego-regalado                   # PTO history + days used for one person
mihomes staff pto-approve <id>                     # approve from CLI
mihomes staff pto-deny <id> --reason "short-staffed that week"
```

Staff PTO requests submitted via WhatsApp ("can I have Friday off") are auto-detected, logged as pending, and the configured approver is notified via direct WhatsApp message. Approver replies `APPROVE <id>` or `DENY <id>` to action them.

Configure PTO approver:
```bash
mihomes config set staff.pto_approver_phone +1xxxxxxxxxx
```

### Vendor Management
```bash
mihomes vendor add "ABC Plumbing" --category plumbing --area "South Shore" --phone 770-555-0200
mihomes vendor list                                # includes which properties each vendor serves
mihomes vendor list --category hvac
mihomes vendor show abc-plumbing
mihomes vendor edit abc-plumbing --phone 770-555-0299
mihomes vendor rate <id> --quality 4 --reliability 5 --cost 4 --communication 5 --notes "Fast response"
mihomes vendor ratings <id>                        # full rating history and averages
```

### Financial Management
```bash
mihomes budget set --property beach-house --category maintenance --amount 25000
mihomes budget report --property beach-house
mihomes expense add 450 --property beach-house --category maintenance --vendor abc-plumbing
mihomes recurring add "Pool Service" --amount 350 --frequency monthly --property beach-house --category maintenance
mihomes report spending --property beach-house --by vendor
mihomes report spending --by category
mihomes report property beach-house               # full single-property health report
mihomes report estate                             # full estate summary
mihomes report upcoming --days 30                 # tasks, events, renewals due soon
mihomes report vendor abc-plumbing                # vendor performance + spend
mihomes report compare                            # spending across all properties
mihomes report forecast                           # projected future spend
```

### Asset & Inventory
```bash
mihomes asset add "Sub-Zero Refrigerator" --type appliance --property beach-house --warranty 2027-03-15
mihomes asset list --property beach-house
mihomes asset list --warranty-expiring 90

mihomes inventory list                            # all consumable stock levels
mihomes inventory add "Pool Chemicals" --property beach-house --unit bottle --par-level 6
mihomes inventory update pool-chemicals --stock 2 --to-order 4
mihomes inventory reorder                         # everything that needs ordering
mihomes inventory mark-ordered pool-chemicals
mihomes inventory mark-restocked pool-chemicals
```

### Work Orders
```bash
mihomes workorder create "Fix roof leak" --property beach-house --from issue:1 --vendor abc-plumbing --estimate 850
mihomes workorder list
mihomes workorder approve <id>
mihomes workorder complete <id> --actual-cost 920 --notes "Extra fitting needed"
```

### Templates & Seasonal Checklists
```bash
mihomes template seed                              # load 8 built-in seasonal templates
mihomes template list
mihomes template run spring-opening --property beach-house --due 2026-05-01
mihomes template add "Custom Checklist" --steps "Step 1,Step 2,Step 3"
```

### Events & Guests
```bash
mihomes event add "July 4th Party" --property beach-house --date 2026-07-04 --guests 40
mihomes event list
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

### AI Advisory
```bash
# Setup (choose one provider)
mihomes ai setup --provider nim --key nvapi-...       # NVIDIA NIM (default)
mihomes ai setup --provider claude --key sk-ant-...   # Anthropic Claude
mihomes ai setup --provider openai --key sk-...       # OpenAI
# Or set via environment variable: NVIDIA_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY

mihomes ai ask "What should I prioritize this week?"
mihomes ai ask "Is the HVAC contract due for renewal?" --property beach-house
mihomes ai review                                    # proactive estate recommendations
mihomes ai review --property beach-house
mihomes ai search "what did we do about the roof last year?"
mihomes ai assess-issue <id>                         # AI severity evaluation
mihomes ai prioritize                                # SPACE-ranked task ordering
mihomes ai prioritize --property beach-house
mihomes ai budget-review                             # financial variance + anomalies
mihomes ai budget-review --property beach-house
mihomes ai plan "opening for summer" --property beach-house
mihomes ai import vendor                             # paste text, AI parses into records
mihomes ai import task --property beach-house
```

Supported providers: **NVIDIA NIM** (default, free tier), **Claude**, **OpenAI**, **Ollama** (local).

### Google Calendar Integration
```bash
mihomes calendar auth                              # one-time OAuth2 browser login
mihomes calendar sync                              # bidirectional: push tasks + pull events
mihomes calendar push                              # push upcoming MiHomes tasks to Google Calendar
mihomes calendar pull                              # pull Google Calendar events into occupancy
mihomes calendar list                              # show occupancy periods
mihomes calendar import events.ics                 # import from iCal file
```

Auto-sync runs every 15 minutes via the watchdog. `[MiHomes]` prefixed events are pushed from MiHomes; all other events are pulled in. Approved PTO requests sync automatically.

### WhatsApp Staff Gateway

The WhatsApp bot monitors group chats, auto-responds to questions, logs issues and tasks, handles PTO requests, and notifies the approver — all without staff learning any commands.

```bash
# Bridge (Node.js sidecar)
mihomes whatsapp bridge                            # start bridge as background process
mihomes whatsapp watchdog                          # start watchdog (keeps bridge + monitor alive)
mihomes whatsapp autostart true                    # register as Windows login startup task
mihomes whatsapp autostart false                   # remove startup task
mihomes whatsapp status                            # check connection status

# Pairing
mihomes whatsapp setup                             # pairing instructions + QR code

# Groups
mihomes whatsapp groups                            # list all WhatsApp groups
mihomes whatsapp link-group "Staff Chat" --property beach-house
mihomes whatsapp unlink-group "Staff Chat"

# Messaging
mihomes whatsapp send +1234567890 "HVAC filter is due"
mihomes whatsapp send-group "Staff Chat" "Pool chemicals delivered"
mihomes whatsapp monitor                           # live monitor with AI auto-responses
mihomes whatsapp monitor --property beach-house    # scoped to one property

# Review queue
mihomes whatsapp review                            # AI-extracted issues/tasks from all groups
mihomes whatsapp review --group "Testing mihomes"  # filter to one group
mihomes whatsapp review --property beach-house     # filter to one property
mihomes whatsapp review --accept 1,3,5             # create specific items
mihomes whatsapp review --auto                     # create all actionable items
mihomes whatsapp clear                             # clear message buffer
```

**What the bot handles automatically:**
- Issues reported in chat → logged with AI severity assessment + expert reply
- Task requests → logged and assigned to named staff member if mentioned
- PTO requests → logged as pending, approver notified via direct message
- Questions about the estate → answered by AI estate manager
- Supply needs → inventory updated silently
- Vendor activity → task created + pushed to Google Calendar

### Automation
```bash
mihomes auto run-all                               # escalation + expiration alerts + digest
mihomes auto digest --format brief                 # cron-ready daily summary
mihomes auto escalate --days 7                     # bump priority on overdue tasks
mihomes cron setup                                 # recommended crontab entries
```

### Backup, Archival & Maintenance
```bash
mihomes backup                                     # snapshot DB
mihomes backup list
mihomes doctor                                     # integrity checks
mihomes archive stats                              # show data volume + what would be archived
mihomes archive run                                # archive old records per retention policy
mihomes config list                                # view all settings
mihomes config set <key> <value>
mihomes stats                                      # quick counts of everything
mihomes audit list                                 # change history
```

### CSV Import/Export
```bash
mihomes export csv property                        # export all properties
mihomes export csv task --template                 # empty template with headers
mihomes import-csv csv vendor vendors.csv
```

---

## Architecture

```
src/mihomes/
├── cli/          # Typer CLI commands (thin layer)
├── models/       # SQLAlchemy ORM models
├── services/     # Business logic
│   ├── ai/       # AI provider abstraction + roles + context
│   └── gateways/ # WhatsApp bridge client, Google Calendar, iCal
├── db.py         # SQLite engine + session management
└── config.py     # Paths and configuration

bridge/           # Node.js WhatsApp bridge (Baileys)
scripts/          # watchdog.py, Start-MiHomes.ps1, Stop-MiHomes.ps1
alembic/          # Database migrations
```

**Key patterns:** CLI → Service → Model. AI uses SPACE prioritization (Safety > Presence > Asset Protection > Compliance > Economy). All changes logged to immutable audit trail.

## AI Expert Roles

MiHomes routes queries to specialized AI advisors:

| Role | Handles |
|------|---------|
| Estate Manager | Cross-property orchestration, prioritization, scheduling |
| Maintenance Advisor | Repairs, preventive scheduling, failure prediction |
| Financial Analyst | Budget variance, cost optimization, forecasting |
| Vendor Strategist | Performance analysis, contract negotiation |
| Compliance Monitor | Insurance, permits, contract renewals |

## Tech Stack

- **Python 3.11+** with Typer + Rich
- **SQLite** with WAL mode and Alembic migrations
- **NVIDIA NIM / Claude / OpenAI / Ollama** for AI advisory
- **Baileys** (Node.js) for WhatsApp integration
- **Google Calendar API** for bidirectional calendar sync
- Local-first, single-user, zero cloud infrastructure required

## License

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) — Creative Commons Attribution-NonCommercial 4.0 International
