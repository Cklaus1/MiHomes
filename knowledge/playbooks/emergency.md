# Emergency Playbook

**Type**: emergency  
**Owner**: All staff — know this cold  
**Properties**: all  
**Review**: quarterly + after any incident

Do not improvise in an emergency. Follow these steps in order. Call for help first, contain second, document third.

---

## Emergency Contacts (fill in per property)

| Role | Name | Phone |
|------|------|-------|
| Owner (Chris) | Chris Klaus | ___________ |
| EA / Backup | ___________ | ___________ |
| Property Manager | ___________ | ___________ |
| Police / Fire / EMS | 911 | 911 |
| Plumber (emergency) | ___________ | ___________ |
| Electrician (emergency) | ___________ | ___________ |
| HVAC (emergency) | ___________ | ___________ |
| Security company | ___________ | ___________ |
| Insurance (claim line) | ___________ | ___________ |

> Update this per property in `knowledge/properties/<slug>.md`

---

## 🔥 Fire

**Immediate:**
1. Get everyone out — do not stop for belongings
2. Call 911
3. Do NOT re-enter the building
4. Text Chris and EA: "Fire at [property]. 911 called. Everyone safe."

**After evacuation:**
- Meet at designated assembly point (see property file)
- Account for all staff and residents
- Do not let anyone re-enter until fire department clears it

**Never:**
- Use elevator
- Open a door that's hot to the touch
- Try to fight a fire that's beyond a small wastebasket size

---

## 💧 Water Leak / Flooding

**Immediate:**
1. Locate and shut off the main water supply (see property file for location)
2. Turn off electricity to affected area if water is near outlets or panels
3. Call emergency plumber
4. Text Chris: "Water leak at [property / location]. Water shut off. Plumber called."
5. Begin containing: towels, buckets, move valuables off floor

**Main water shutoff locations:**
- [Miami] — ___________
- [Aspen] — ___________
- [Property 3] — ___________
- [Property 4] — ___________

**Document:**
- Take photos before cleanup begins
- Log in MiHomes: `mihomes issue add --severity critical --title "Water leak — [location]"`

**Follow-up:**
- Do not use affected areas until cleared
- Confirm with Chris before scheduling any repairs over $500

---

## ⚡ Power Outage

**First:**
1. Check if it's the whole neighborhood (look outside) or just the house
2. Check circuit breaker panel (see property file for location)
3. If tripped breaker: reset once. If it trips again — do not reset, call electrician.
4. If whole neighborhood: wait, check utility company outage map
5. Text Chris: "Power out at [property]. [Neighborhood-wide / isolated]. Status: [action taken]."

**Breaker panel locations:**
- [Miami] — ___________
- [Aspen] — ___________

**Extended outage (2+ hours):**
- Check fridge / freezer — keep closed to preserve temp (safe up to 4 hours)
- Disable HVAC and water heater to prevent surge damage on restore
- If overnight: secure property and inform Chris

---

## 🔓 Security Breach / Intrusion

**If someone is in the property:**
1. Do not confront — leave immediately
2. Call 911 from outside or a safe location
3. Text Chris: "Security issue at [property]. 911 called."
4. Do not re-enter until police clear it

**If you discover signs of forced entry (no one present):**
1. Do not enter or touch anything
2. Call 911 to file report
3. Call Chris
4. Document with photos from outside

**Alarm triggers:**
- If alarm sounds unexpectedly: treat as real until confirmed otherwise
- Know the alarm code and the security company's verification procedure
- Alarm company number: ___________

---

## 🏥 Medical Emergency (staff or resident)

1. Call 911 immediately
2. Stay with the person — follow 911 operator instructions
3. Unlock front door for emergency access
4. Text Chris: "Medical emergency at [property]. 911 called."
5. Do NOT move the person unless they are in immediate danger

**First aid kit location:**
- [Miami] — ___________
- [Aspen] — ___________

---

## 🌡️ HVAC Failure (Extreme Heat or Cold)

1. Check thermostat settings — confirm it's not a programming issue
2. Check air filters (clogged filter = shutdown)
3. If unit isn't responding: call HVAC vendor
4. Text Chris: "HVAC down at [property]. [Heat/cooling] out. Vendor called."

**Extreme cold (below 40°F outside):**
- Open cabinet doors under sinks
- Let faucets drip to prevent pipe freeze
- If pipes freeze: call plumber immediately — do NOT use open flame to thaw

---

## After Any Emergency

1. **Document**: photos, timeline, who was involved
2. **Log in MiHomes**: `mihomes issue add --severity critical`
3. **Written summary to Chris within 24 hours**: what happened, what was done, current status, next steps
4. **Insurance**: notify within 48 hours for any property damage (Chris or EA handles)
5. **Debrief**: what could have been caught earlier, update this playbook if needed

---

## Key System Locations (template — fill in per property)

| System | Location |
|--------|----------|
| Main water shutoff | |
| Electrical panel | |
| Gas shutoff | |
| HVAC unit | |
| Security panel | |
| First aid kit | |
| Fire extinguisher | |
| Evacuation assembly point | |

> Full details in each property file: `knowledge/properties/<slug>.md`
