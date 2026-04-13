# MiHomes — Millena's Guide

Everything you need to run the household system day-to-day. You don't need to be technical — just follow the steps.

---

## First: How to Open MiHomes

MiHomes runs in the **Command Prompt** on your PC.

1. Press the **Windows key** (⊞) on your keyboard
2. Type **cmd** and press Enter — a black window will open
3. Type `mihomes` and press Enter — you'll see the full list of commands

You'll use this same window for everything in this guide. After each command, press **Enter** to run it.

> **Tip**: Right-click the Command Prompt icon in the taskbar and select **Pin to taskbar** so you can open it quickly every day.

---

## Your Most Important Weekly Task

### Every Friday afternoon — send Chris his weekly report

Type this and press Enter:

```
mihomes report weekly --format 15-5
```

You'll see a report pre-filled with everything that happened this week — tasks completed, issues, upcoming work, budget. 

**Select all the text, copy it, and paste it into Slack or email to Chris.**

Before you send it, fill in the four blank sections:

| Section | What to write |
|---------|--------------|
| 🎯 My priorities | What YOU are personally focused on next week — your judgment, not just the calendar |
| 🚩 Flags / Blockers | Anything slowing you or the team down. System-generated flags show as `[auto]` — add yours below |
| ❓ Needs decision from Chris | Anything you're waiting on him for — a decision, approval, or answer — before you can move forward |
| ⏱ Time split | Rough guess at how your time was split (doesn't need to be exact) |

Chris reads it Monday morning. The Monday sync should only cover the Flags and Needs Decision sections — everything else is already in the report.

> **Tip**: The report is easy to fill out if you've been logging tasks and issues throughout the week. The system does the work — you just add your perspective.

### What a good 15-5 looks like

Here's an example of a completed report — this is what you're aiming for:

---

📍 Miami, Aspen  
📅 Week of Apr 14 – Apr 18, 2026  
👤 Millena

✅ Done this week:  
- Deep clean completed — master bedroom and both guest bathrooms [Miami]  
- Pool filter replacement scheduled and confirmed with AquaFix for Tuesday  
- Coordinated AC service — technician came Wednesday, filters replaced, unit running well  
- Onboarding started for Rosa (new housekeeper) — Day 1 and 2 complete  
- Issue resolved: kitchen faucet drip — plumber came Thursday, fixed same day  

🔨 In progress:  
- Rosa onboarding — Week 1 shadowing in progress, on track  
- Vendor quote for Aspen gutter cleaning — waiting on 2nd quote from Alpine Services  
- Appliance warranty check — started, not finished, will complete next week  

📅 Plan for next week:  
- Rosa: Week 2 independent work begins Monday  
- Gutter cleaning decision once 2nd quote comes in (expected Wed)  
- Aspen spring walkthrough — scheduling with property manager  
- HVAC filter check at Miami (overdue by 2 weeks)  

🎯 My priorities:  
- Get Rosa fully independent by end of week — she's doing well but needs one more round of supervision  
- Close the Aspen gutter situation — been open 3 weeks  
- Catch up on Miami maintenance backlog before end of month  

🚩 Flags / Blockers:  
- [auto] HVAC filter task is 2 weeks overdue — scheduling now  
- Aspen caretaker called in sick twice this week — coverage is thin, may need backup plan  
- Rosa still needs her alarm code set up — I don't have access to do this myself  

❓ Needs decision from Chris:  
- Gutter cleaning: Alpine quote is $400 more than first vendor but has better reviews — which do you want me to go with?  
- Rosa's alarm code — who do I contact to get this set up?  
- Aspen caretaker reliability — worth having a conversation or too early?  

💰 Budget MTD:  
- Miami: $3,200 / $5,000 USD (64%)  
- Aspen: $1,800 / $3,000 USD (60%)  

⏱ Time split (rough %):  
- Operations / staff oversight: 45%  
- Vendor coordination: 25%  
- Admin / reporting: 15%  
- Other: 15%  

---

**A few things to notice in this example:**

- **Done** is specific — names the vendor, the room, the outcome. Not just "cleaned house."
- **In progress** says where things stand, not just that they exist — "waiting on 2nd quote" tells Chris it's moving.
- **My priorities** reflects her judgment — she's decided Rosa and Aspen gutters are the focus, not just listing what's scheduled.
- **Flags** includes both auto-generated items and her own observation about the caretaker.
- **Needs decision** are actual questions with context — not "what should I do about Aspen?" but "quote A vs quote B, here's the difference, which one?"

The ❓ section is the most important one for your Monday sync. If it's empty, Chris has nothing to decide and the meeting can be short.

---

## Daily: Checking What Needs to Get Done

### See everything open across all properties

```
mihomes task list
```

### See only what's overdue

```
mihomes task list --overdue
```

### See tasks for one specific property

```
mihomes task list --property miami
```

Each task has an **ID number** on the left (like `12` or `47`). You'll use that number to mark things done.

### Mark a task as done

```
mihomes task complete 12
```

(Replace `12` with the actual task ID.)

### Add a new task

```
mihomes task add "Replace HVAC filters" --property miami --priority high --due 2026-04-20
```

Priority options: `urgent`, `high`, `medium`, `low`

### Check for alerts (overdue tasks, critical issues, expiring contracts)

```
mihomes alerts
```

If an alert isn't urgent right now, you can snooze it:

```
mihomes alerts snooze 5 --days 7
```

(Replace `5` with the alert ID, `7` with how many days to snooze.)

---

## Logging Issues

When something is broken, damaged, or needs attention — **log it the same hour it happens.** Don't wait until the end of the day.

### Log an issue

```
mihomes issue add "Kitchen faucet dripping" --property miami --severity medium
```

```
mihomes issue add "Water leak under sink" --property miami --severity critical
```

**Which severity to pick:**

| Severity | When to use | Examples |
|----------|-------------|---------|
| `critical` | Immediate risk to people or the property | Water leak, electrical issue, security breach |
| `high` | Affects operations today | Appliance broken, pest sighting |
| `medium` | Needs to be scheduled, not today | Damaged furniture, broken fixture |
| `low` | Cosmetic, no urgency | Scuff on wall, loose handle |

**When in doubt, go one level higher.** Critical issues automatically flag in the weekly report.

### Add a note or update to an issue

```
mihomes note add --entity issue --id 8 --text "Plumber coming Thursday 2pm, Marco from AquaFix"
```

(Replace `8` with the issue ID.)

### Mark an issue resolved

```
mihomes issue resolve 8
```

### See all open issues

```
mihomes issue list
```

---

## Playbooks

Playbooks are your step-by-step guides for every important situation. **Search here before asking someone** — the answer is usually already written.

### See all available playbooks

```
mihomes playbook list
```

You'll see:
- `daily-operations` — the daily house routine
- `housekeeper` — cleaning standards, laundry, deep clean
- `emergency` — fire, water leak, power outage, security
- `hiring` — posting, phone screening, trial day, offer
- `onboarding-new-hire` — first 2 weeks for a new staff member
- `communication` — what goes in WhatsApp vs MiHomes vs reports
- `separation-offboarding` — resignations and terminations

### Read a playbook

```
mihomes playbook show emergency
mihomes playbook show housekeeper
mihomes playbook show hiring
```

### See just the checklist (great for printing)

```
mihomes playbook show daily-operations --checklist
```

### Search across all playbooks

Not sure which playbook has what you need? Search:

```
mihomes playbook search "water shutoff"
mihomes playbook search "resignation"
mihomes playbook search "background check"
mihomes playbook search "phone screen"
```

### Run a playbook (turns it into tasks in the system)

When you're starting a process — like onboarding a new hire — you can turn the entire checklist into tasks automatically:

```
mihomes playbook run onboarding-new-hire --property miami --start 2026-04-21
```

Want to preview what tasks it would create before committing?

```
mihomes playbook run onboarding-new-hire --property miami --dry-run
```

---

## Hiring

When you're hiring for a role, use the AI to rank resumes automatically.

**Step 1**: Create a folder on your Desktop and put all the resumes in it (PDF files work best). For example, name the folder `resumes`.

**Step 2**: Make sure there's a job description file saved at:
`knowledge/staff/job-descriptions/<role>.md`

If there isn't one yet, create a plain text file there with the job description — copy it from Indeed or wherever you posted the role.

**Step 3**: Run the ranker (replace `YourName` with your Windows username):

```
mihomes ai rank-resumes C:\Users\YourName\Desktop\resumes --role housekeeper
```

The AI reads every resume, scores each person on experience, reliability, household fit, and more — then gives you a ranked list showing who to phone screen, hold, or decline. Notes for each candidate are saved automatically so you have a record.

Follow the rest of the process in:
```
mihomes playbook show hiring
```

---

## Logging Expenses

### Add an expense

```
mihomes expense add --property miami --amount 250 --category maintenance --description "Pool chemical restock"
```

### See spending this month for one property

```
mihomes report spending --property miami
```

### Compare spending across all properties

```
mihomes report compare
```

---

## Staff & Vendors

### See all staff

```
mihomes staff list
```

### See who has what tasks assigned to them

```
mihomes staff workload
```

### See all vendors

```
mihomes vendor list
```

---

## Quick Overview of Everything

```
mihomes dashboard
```

Shows all properties at a glance: open issues, tasks due this week, overdue tasks, budget status, open work orders.

---

## When You're Unsure What to Do

**1. Search the playbooks first:**
```
mihomes playbook search "your question"
```

**2. Check the alerts — something may already be flagged:**
```
mihomes alerts
```

**3. Add `--help` to any command to see how it works:**
```
mihomes issue --help
mihomes task --help
```

**4. If it's urgent** — follow the emergency playbook:
```
mihomes playbook show emergency
```

---

## Quick Reference Card

Cut this out and keep it handy.

| What | Command |
|------|---------|
| Weekly report for Chris | `mihomes report weekly --format 15-5` |
| See all open tasks | `mihomes task list` |
| See overdue tasks | `mihomes task list --overdue` |
| Mark a task done | `mihomes task complete <id>` |
| Add a task | `mihomes task add "title" --property X --priority Y --due YYYY-MM-DD` |
| Check alerts | `mihomes alerts` |
| Snooze an alert | `mihomes alerts snooze <id> --days 7` |
| Log an issue | `mihomes issue add "title" --property X --severity Y` |
| Add note to issue | `mihomes note add --entity issue --id X --text "..."` |
| Resolve an issue | `mihomes issue resolve <id>` |
| Read a playbook | `mihomes playbook show <name>` |
| Search playbooks | `mihomes playbook search "term"` |
| Run a playbook | `mihomes playbook run <name> --property X` |
| Rank resumes | `mihomes ai rank-resumes <folder> --role <role>` |
| Log an expense | `mihomes expense add --property X --amount Y --category Z --description "..."` |
| Dashboard | `mihomes dashboard` |
| Help on any command | `mihomes <command> --help` |
