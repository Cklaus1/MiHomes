---
title: Privacy Policy
version: draft-2026-11-09
effective_date:
---
**DRAFT for counsel review. Not in effect.** Written from how MiHomes works as of 9 November 2026. Items in [BRACKETS] must be filled in or decided before publishing.

# Who we are

MiHomes is operated by [COMPANY LEGAL NAME], [REGISTERED ADDRESS] ("MiHomes", "we", "us"). Questions about this policy or your data: [PRIVACY CONTACT EMAIL].

MiHomes helps households and estates run their homes: properties, rooms and assets, tasks, issues, vendors, staff, documents, a calendar, in-app chat, a Telegram bot and an AI advisor.

# Two kinds of data, two roles

- **Your account data** (your name, email, sign-in details, billing). We decide how this is used, so we are its controller.
- **Your household's content** (everything you and your household put into MiHomes, including details about other people such as staff, vendors and guests). Your household decides what goes in and why; we store and process it to provide the service on your household's behalf. [COUNSEL: confirm the controller/processor split and whether a data processing agreement is offered to estate customers.]

If you add information about other people (for example a housekeeper's phone number or a guest's dietary needs), you are responsible for having the right to do so and for telling them where appropriate.

# What we collect

**When you sign up and sign in**
- Name, email address and, if you use Google sign-in, your Google account identifier and profile picture address.
- A password, stored only as a one-way hash, if you set one.
- Your role in each household (owner, admin or staff) and who invited you.
- A sign-in session: a random identifier (we store only a hash of it), when it was created and last used. We do not store your IP address or browser details with your session.

**What your household adds**
- Properties and their addresses and map coordinates; rooms, assets, inventory, tasks, issues, work orders, budgets, contracts and documents.
- People in your directory: staff, residents, family members and associates (names, phone numbers, emails, WhatsApp numbers, certifications, time off).
- Vendors: contact names, phone numbers, emails, licence and insurance details, notes.
- Guests and events: names, contact details, room preferences and dietary preferences. Dietary information can reveal health or religious information; only add it with the guest's agreement.
- Files you upload: documents and photos (for issues, assets, chat).
- In-app chat messages, reactions and mentions.
- An audit trail of changes (what changed, when, and by whom).

**The Telegram bot**
- If you link your Telegram account: your Telegram user identifier.
- If a Telegram group is linked to your household: the group identifier, and for each group message the sender's name, the text, whether media was attached, and the time. Private messages to the bot and one-time link codes are not logged.

**The AI advisor**
- What you ask, the AI's answers, a summary of the household context used, which AI provider and model answered, and how many tokens were used. Images and text files you attach to a question are sent to the AI provider with it.

**Calendar**
- If you connect Google Calendar: your Google email, an access token (stored encrypted) and which events we created in it.

**Billing**
- Your Stripe customer and subscription identifiers, plan, status and billing dates. Card details are entered on Stripe's own pages; we never see or store your card number.

**Email and notifications**
- Emails we have queued or sent to you, delivery results, and an unsubscribe list (kept so we never email an unsubscribed address again).
- If you turn on browser notifications: the push address your browser gives us and its user agent.

**The waiting list** (our public website)
- Email, name, number of homes, whether you have staff, how you found us (campaign tags, referrer), and the IP address and browser details at sign-up. [DECISION: keep IP and browser details, or stop collecting them.]

We use IP addresses briefly, in memory, to limit repeated sign-in attempts and form submissions. They are not stored for that purpose.

# How we use it

- To provide MiHomes: show your household its data, send the reminders, alerts, emails and notifications you've set up, sync your calendar, and run the Telegram bot.
- To secure accounts: sign-in, rate limits against password guessing, and the audit trail.
- To bill you, through Stripe.
- To answer AI advisor questions, by sending the question and the relevant household context to an AI provider (see below).
- To contact you about your account, and with onboarding or product emails you can unsubscribe from.

We do not sell personal data, and we do not use your household's content to advertise to you. [COUNSEL: confirm the lawful bases (contract, legitimate interests, consent) per purpose.]

# Who we share it with

Only these service providers, and only what each needs:

- **Stripe**: payments and subscriptions.
- **Google**: sign-in, if you choose it; Google Calendar, if you connect it. Calendar events we create include the title, description and the **property's address**.
- **Telegram**: messages to and from the bot, if you use it.
- **AI providers**: your question, attachments and relevant household context. MiHomes can use NVIDIA (NIM), Anthropic (Claude) or OpenAI; the provider currently configured is [CURRENT AI PROVIDER]. [COUNSEL: check each provider's data-use and retention terms, and state whether they may train on submitted data.]
- **Resend**: sending email.
- **Browser push services** (Google, Mozilla, Apple, Microsoft): delivering notifications you turned on.
- **OpenStreetMap (Nominatim) and Open-Meteo**: weather for your property. The **property address** is sent to OpenStreetMap to find its location, and the coordinates to Open-Meteo.
- **jsDelivr**: some pages load open-source scripts from this content network, which sees your IP address when they load.
- **Hosting**: [HOSTING PROVIDER(S) AND REGION(S)]. Our public website runs on Fly.io in the United States.

We may also disclose data if the law requires it, or to protect people's safety or our rights.

[COUNSEL: international transfers. Several providers are in the United States; state the transfer safeguards that apply.]

# Cookies and browser storage

We use only the cookies MiHomes needs to work:

- **mihomes_session**: keeps you signed in. 14 days.
- **mihomes_csrf**: protects forms against cross-site attacks. 14 days.
- **Google sign-in cookies** (mihomes_oauth_state, mihomes_oauth_verifier, mihomes_oauth_intent, oauth_state): used only during a Google sign-in, for up to 10 minutes.

Your browser also remembers a few display choices on your own device (for example which task view or vendor tab you last used, and your current AI conversation). We use no advertising or analytics cookies.

# How long we keep it

- **While your household uses MiHomes**, we keep its content. [DECISION: audit-trail and AI-conversation history are currently kept until the household is deleted; settings for 2 years and 1 year exist but are not yet enforced.]
- **Deleting a household:** the owner can delete it in Settings. It can be undone for 30 days; then its data and files are permanently erased. A record that the deletion happened is kept, and so is the unsubscribe list.
- **Deactivating a household:** if the owner deactivates it and does not reactivate it within 90 days, it is permanently erased the same way.
- **Leaving or deleting your profile:** staff and admins can leave a household, and anyone who owns no household can delete their profile. That erases their name, email, password and Google link and signs them out everywhere. Households keep the work they added.
- **Backups:** we back up the database every night. Erasing data removes it from MiHomes straight away, but copies made before the erasure stay in our backups until they are deleted on schedule: **up to 14 days on our server** and **up to 30 days in an offline copy** kept by [WHO HOLDS THE OFFLINE COPY]. Backups are only used to recover from a failure; if one were ever restored, erasures made since it was taken would be applied again.
- **Shorter-lived data:** sign-in sessions expire after 14 days, password-reset links after 1 hour, invitations after 7 days, Telegram link codes after 15 minutes, and the chat activity log behind live updates after 7 days. Server logs rotate and are overwritten.

# Your rights

In MiHomes you can:
- **See and correct** your profile, and your household's content.
- **Export** everything your household holds (owner, in Settings).
- **Delete** your household (owner), **leave** a household, or **delete your profile**.
- **Unsubscribe** from emails and turn off notifications at any time.

Depending on where you live you may also have the right to object to or restrict processing, to data portability, and to complain to a data protection authority. To exercise any right, or if you're a staff member, vendor or guest whose details a household added, contact [PRIVACY CONTACT EMAIL]. If your details were added by a household, we may refer you to that household, since it decides what is kept about you.

# Security

Passwords are stored as one-way hashes, sessions as hashes, and calendar tokens encrypted. Each household's data is separated from every other household's at the database level. Connections are encrypted. No system is perfectly secure; tell us at [SECURITY CONTACT EMAIL] if you find a problem.

# Children

MiHomes is not intended for anyone under [MINIMUM AGE]. Do not create an account for a child.

# Changes to this policy

We will post changes here with a new effective date, and tell account owners by email before significant changes take effect.
