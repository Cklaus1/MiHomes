# 15-5 Weekly Report Guide

**For**: Millena  
**Reviewed by**: Chris (Monday mornings)  
**Format**: 15 minutes to write · 5 minutes to read

---

## What Is a 15-5 Report?

A short weekly status update you write in 15 minutes, structured so the reader can absorb it in 5. It's not a diary or a design doc — it's a signal: where things stand, what's moving, what needs attention.

Write it Friday afternoon or Sunday night. Chris reads it Monday morning before your first sync.

---

## Template

```
📅 Week of [Mon Date] – [Fri Date]

## ✅ Done This Week
- [List 3–6 concrete things completed. Be specific: "Built slug generation service + tests pass" not "worked on backend".]

## 🔨 In Progress
- [What you're mid-stream on, with % or next milestone]
- [Include any blockers as a sub-bullet: "⚠ Blocked on X — need Y"]

## 📅 Plan for Next Week
- [3–5 priority items you intend to finish, in order]

## 🚩 Flags (optional)
- [Decisions needed, risks, things that surprised you, scope changes]
- [If nothing — omit this section entirely]

## ⏱ Time Split (rough %)
Dev: __% | Testing: __% | Review/docs: __% | Other: __%
```

---

## Writing Tips

**Done section**
- Name the artifact: file, feature, test suite, command
- Include a result if meaningful: "tests pass", "syncs 183 rows", "modal submits correctly"
- Skip "researched X" unless it produced a decision or doc

**In Progress**
- One bullet per active thread — don't combine three things into one
- If you're blocked, say so clearly and name what you need

**Next Week**
- List what you'll actually finish, not everything on the backlog
- Order by priority (top = most important)

**Flags**
- Use this for things Chris needs to respond to, not general FYI
- Good flags: "Need DB schema decision before I can build chunk 7", "Found mismatch in PRD re: WhatsApp auth — which approach do we take?"
- Bad flags: "lots going on", "things are complex"

**Time split**
- Rough is fine — this surfaces where time goes week over week
- "Other" = meetings, context-switching, unplanned support

---

## MiHomes Build Context

The current work is Phase 1a — 12 chunks, each a vertical slice. Report chunk progress using the chunk numbers from `tasks/todo.md`:

> "Finished Chunk 3 (Property + Space CLI). All tests pass, full round-trip works. Starting Chunk 4 (Staff + Vendor) Monday."

When a chunk is in flight, name what's done and what remains:

> "Chunk 5 (Task + Recurrence): recurrence engine done and tested. CLI task commands 60% done. No alembic migration yet."

When you hit a technical decision point, flag it — don't wait for the weekly sync to surface it.

---

## Example Report

```
📅 Week of Apr 14 – Apr 18

## ✅ Done This Week
- Chunk 1: project skeleton, db.py, config.py, alembic setup — `pip install -e .` and empty migration verified
- Chunk 2: slug service (generate, ensure_unique, resolve_identifier) + audit log — all tests pass
- PR reviewed and pushed

## 🔨 In Progress
- Chunk 3: Property + Space models done, services 80% done, CLI not started
  - ⚠ Unsure if Space should belong to Property via FK or slug — need decision before migration

## 📅 Plan for Next Week
1. Finish Chunk 3 CLI + full round-trip test
2. Start Chunk 4 (Staff + Vendor) — models + services
3. Get alembic migration for chunks 3–4 working

## 🚩 Flags
- Space FK vs slug decision needed before I write the migration (see PRD §3.2)

## ⏱ Time Split
Dev: 70% | Testing: 20% | Review/docs: 5% | Other: 5%
```

---

## Cadence

| When | What |
|------|------|
| Friday 4pm / Sunday night | Write and send (Slack / email / wherever) |
| Monday morning | Chris reads before sync |
| Monday sync | Only cover Flags + blockers — skip what's already in the report |

Keep it tight. If it's taking more than 20 minutes to write, you're putting in too much detail.
