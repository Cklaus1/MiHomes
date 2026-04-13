# Communication Playbook

**Type**: operations  
**Owner**: All staff  
**Properties**: all

Clear communication is the single biggest lever for running a multi-property household smoothly. This playbook defines what goes where, when, and how.

---

## Communication Channels

### WhatsApp — Real-Time & Urgent
**Use for:**
- Anything that needs a response today
- Emergencies (always)
- Quick confirmations ("done with kitchen, moving to bedrooms")
- Urgent supply requests
- Unexpected issues that affect the day

**Do not use for:**
- Task assignments (use MiHomes)
- Formal issue reports (use MiHomes)
- End-of-day reports (use the report format below)
- Anything that needs to be tracked or searched later

**Response expectation:** Within 30 minutes during working hours.

**Groups:**
- `[Property] Operations` — all staff at that property + manager
- `All Properties` — cross-property announcements from manager only
- Direct messages for 1:1 issues

---

### MiHomes — Tasks & Issues
**Use for:**
- Any assigned task (created by manager or EA)
- Issue logging (broken items, damage, supply shortages that need tracking)
- Recurring maintenance reminders

**Rules:**
- When you complete a task, mark it done in the system — don't just tell someone
- When you log an issue, add a photo if at all possible
- Don't create tasks for things that should be in the daily checklist — those live in the playbook

---

### Daily Report — Written EOD Summary
**Use for:**
- End-of-day summary to manager / EA
- The record of what was done that day
- Flagging anything that needs follow-up

**Format:**

```
📍 [Property Name]  
📅 [Date]  
👤 [Your name]

✅ Done today:
- [List what you completed from the daily checklist]
- [Any extra tasks done]

🔧 Issues / flags:
- [Anything broken, low, unusual, or needing attention]
- [If nothing: "None"]

📦 Supplies needed:
- [What's low or out, with approximate quantity needed]
- [If nothing: "None"]

🕐 Tomorrow:
- [Anything scheduled or that you plan to follow up on]
```

**Send to:** Property manager / EA  
**Deadline:** By 8:00 PM each working day  
**Channel:** WhatsApp DM to manager (not group)

---

## Issue Reporting Standards

### When to report (immediately — same hour):
- Water leak, flooding, or sewage
- Any electrical issue
- Security concern
- Broken appliance affecting daily operation
- Anything that could get worse if left until EOD

### When to report (in daily report):
- Non-urgent damage or wear
- Low supplies
- Items out of place that need owner decision

### How to report in MiHomes:
```
mihomes issue add \
  --property <slug> \
  --title "Brief description" \
  --severity [critical|high|medium|low]
```

**Severity guide:**
| Level | Meaning | Example |
|-------|---------|---------|
| Critical | Immediate risk to people or property | Water leak, fire, security breach |
| High | Significant impact on operations, needs attention today | Appliance failure, pest sighting |
| Medium | Notable problem, needs scheduling | Damaged furniture, broken fixture |
| Low | Cosmetic or minor, no urgency | Scuff on wall, loose handle |

---

## Escalation Hierarchy

When something needs a decision or response:

1. **First contact:** Property manager (text or call)
2. **If no response in 1 hour:** EA
3. **If no response in 2 hours (urgent) / same day (non-urgent):** Chris directly
4. **Emergency (immediate):** Call 911 first, then Chris, then EA

Do not sit on a problem because you're unsure who to tell. Escalate up the chain. A problem communicated late is always worse than one communicated early.

---

## Response Expectations

| Message type | Expected response time |
|-------------|----------------------|
| Emergency (WhatsApp, urgent label) | Immediate |
| WhatsApp operational message | Within 30 min (working hours) |
| Daily report | Reviewed by 10 AM next morning |
| MiHomes task update | Same business day |
| Non-urgent question | Within 24 hours |

---

## Language & Tone

**Be direct.** Say what happened. Not "I think maybe the thing under the sink might be a little wet" — say "Water under kitchen sink, looks like a slow drip from the drain. Took a photo. Logging now."

**Be specific.** Name the room, the item, the location. Not "bathroom issue" — "master bathroom, shower drain backing up."

**No need for formality.** Short, clear messages are better than long polished ones. The goal is speed and clarity, not presentation.

**Ask rather than assume.** If you're not sure whether to do something, ask. Especially for:
- Moving or throwing away any personal item
- Letting a vendor into the property alone
- Making any purchase over $50
- Anything involving guests or family

---

## What Not to Communicate (anywhere)

- Property addresses or entry codes — never share outside of staff group
- What residents are doing, where they are, when they're away
- Financial information (costs, budgets, salaries)
- Anything about other staff members' performance or personal situations
- Anything about the family to friends, family, or social media

Violations of this are grounds for immediate termination. This is not a formality — it protects both the household and you.
