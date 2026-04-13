# MiHomes — How-To Guide for Millena

Your quick reference for everything you'll do day-to-day in MiHomes.

---

## Getting Started

Open your terminal and type `mihomes` to see all available commands. If you ever get stuck on a specific command, add `--help`:

```bash
mihomes --help
mihomes task --help
mihomes report weekly --help
```

---

## Your Weekly Workflow

### Friday afternoon — send the weekly report to Chris

```bash
mihomes report weekly --format 15-5
```

Copy the output. Fill in:
- **🎯 My priorities** — what you're personally focused on next week
- **⏱ Time split** — rough % breakdown of how your time went

Send to Chris via Slack or email. He reads it Monday morning.

---

## Daily Tasks

### See what needs to be done

```bash
mihomes task list                        # all open tasks
mihomes task list --overdue              # overdue only
mihomes task list --property miami       # one property
```

### Mark a task done

```bash
mihomes task complete <id>
```

### Add a task

```bash
mihomes task add "Clean pool filters" --property miami --priority high --due 2026-04-18
```

### See alerts (overdue tasks, critical issues, expiring contracts)

```bash
mihomes alerts
```

---

## Logging Issues

When something is broken, damaged, or needs attention — log it immediately.

```bash
mihomes issue add "Kitchen faucet dripping" --property miami --severity medium
mihomes issue add "Water leak under sink" --property miami --severity critical
```

**Severity guide:**
- `critical` — immediate risk (water, electrical, security)
- `high` — affects operations today
- `medium` — needs scheduling, not urgent
- `low` — cosmetic or minor

### Resolve an issue

```bash
mihomes issue resolve <id>
```

### See all open issues

```bash
mihomes issue list
```

---

## Playbooks

Playbooks are the step-by-step guides for how everything gets done.

### See all playbooks

```bash
mihomes playbook list
```

### Read a playbook

```bash
mihomes playbook show housekeeper
mihomes playbook show emergency
mihomes playbook show hiring
```

### See just the checklist

```bash
mihomes playbook show daily-operations --checklist
```

### Run a playbook (creates tasks in the system)

Use this when you're starting a recurring process — like onboarding a new hire — and want all the steps to show up as tasks:

```bash
mihomes playbook run onboarding-new-hire --property miami --start 2026-04-21
mihomes playbook run daily-operations --property miami
```

Preview first without creating anything:

```bash
mihomes playbook run daily-operations --property miami --dry-run
```

### Search across all playbooks and guides

```bash
mihomes playbook search "water shutoff"
mihomes playbook search "termination"
mihomes playbook search "background check"
```

---

## Hiring

When you're hiring for a role:

1. Put all resumes (PDF or text files) into a folder — e.g. `~/resumes/housekeeper/`
2. Make sure there's a job description in `knowledge/staff/job-descriptions/<role>.md`
3. Run:

```bash
mihomes ai rank-resumes ~/resumes/housekeeper --role housekeeper
```

The AI reads every resume, scores each candidate on 5 dimensions, and gives you a ranked list with a recommended action (phone screen / hold / decline) for each person. Notes for each candidate are saved automatically to `knowledge/staff/candidates/`.

---

## Tracking Expenses

### Log an expense

```bash
mihomes expense add --property miami --amount 250 --category maintenance --description "Pool chemical restock"
```

### See spending this month

```bash
mihomes report spending --property miami
mihomes report compare              # all properties side by side
```

---

## Staff & Vendors

### See all staff

```bash
mihomes staff list
```

### See who has what tasks

```bash
mihomes staff workload
```

### See all vendors

```bash
mihomes vendor list
```

---

## The Dashboard (quick overview)

```bash
mihomes dashboard
```

Shows: all properties, open issues, tasks due this week, overdue count, budget status, open work orders.

---

## Tips

**Log issues the same hour they happen.** A photo description in the notes is better than a perfect entry tomorrow.

**Use playbook search before asking someone.** If you're unsure how to handle something — a resignation, a leak, a vendor dispute — search first:
```bash
mihomes playbook search "resignation"
mihomes playbook search "leak"
```

**The 15-5 report only takes 5 minutes** if you've been logging tasks and issues throughout the week. The DB does the work — you just fill in your priorities and time split.

**Severity matters on issues.** Critical issues trigger alerts automatically and show up in Chris's weekly report flags. When in doubt, go one level higher.

---

## Quick Reference Card

| What | Command |
|------|---------|
| Weekly report for Chris | `mihomes report weekly --format 15-5` |
| What's overdue | `mihomes task list --overdue` |
| Log an issue | `mihomes issue add "title" --property X --severity Y` |
| Mark task done | `mihomes task complete <id>` |
| Check alerts | `mihomes alerts` |
| Read a playbook | `mihomes playbook show <name>` |
| Run a playbook | `mihomes playbook run <name> --property X` |
| Rank resumes | `mihomes ai rank-resumes <folder> --role <role>` |
| Dashboard | `mihomes dashboard` |
| Search everything | `mihomes playbook search "term"` |
| Help on any command | `mihomes <command> --help` |
