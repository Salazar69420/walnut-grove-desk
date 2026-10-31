# Plan: the Clinic CRM and AI front desk (EliseAI-style, for a veterinary clinic)

Status (2026-10-31): phases 1 to 5, 7 and 8 are built on invented data (see HANDOFF.md section 11); phase 6 (AI assistant) and phase 9 (real sending, calling, voice) are not. Original plan text follows. Source: the owner's walkthrough and 17 photographed screens of the EliseAI product (Home, Tasks, Contacts, Calendar, Knowledge & Fees, Phone call log, Reports, conversation view). The photos contained a real person's details; none of that is copied here.

Short verdict: **it can be pulled off, in phases.** The screens and workflows are all buildable on the stack we already have. What decides the pace is not code. It is three gates: privacy approval for real client data, consent rules for sending messages, and safety rules for an AI that talks to clients. The plan builds the whole interface first on invented contacts, then connects real data and real sending one gate at a time.

---

## 1. What the owner described, screen by screen

| EliseAI screen | What it does there | Clinic version |
|---|---|---|
| **Home** | Three columns: Prospects, Calendar, Tasks. Filter "Assigned to me only", search box, a Call button, "Edit workstreams" | Home: **Inquiries and Patients**, **Calendar**, **Tasks**, each filterable to "mine" |
| **Tasks** | Left list of tasks with filters (type, owner), middle detail, **Guest Card** on the right. Tasks are raised by the AI when it cannot handle something or lacks knowledge | Same. Tasks are typed and classified **Inquiry** or **Patient** (instead of Prospect or Resident), assigned to a person |
| **Guest Card** (right panel, 4 tabs: Contact, Knowledge, Calendar, Units) | Name, status badge, email and phone with opt-in state, preference notes with edit history, owner, **AI on/off switch**, associated contacts, preferences, add tour, documents | Contact (name, status Inquiry or Patient, email and phone with consent state, previous visits and dates, pets, owner, AI switch, associated contacts, notes, documents), **Knowledge**, **Calendar**, **Services** |
| **Conversation view** | One thread per person across SMS, email, calls. Filter All, Email only, SMS only. Call button. Composer: choose Email or Text, internal note with @mentions, reminder, scheduled send, attach media | Same, per client |
| **Contacts > Prospect** | Table: name, email, phone, community, status, last message, mediums, last active, created. Search, Filters, Columns, saved Views, **Add Contact**, **Create Campaign** | Contacts: Inquiries and Patients with multi-filter, columns, views, add contact (walk-ins), campaigns |
| **Calendar** | Week view, Create, color by property, agent or event type, legend, property filter | Calendar with filters: patients, inquiries, check-ups, surgeries, other classes |
| **Operations > Knowledge & Fees** | Knowledge entries by level. **Pending Knowledge**: entries the AI learned that are not used until a human approves. Diff of previous and new. Approve all or Decline all. Tabs: Community Info, Media, Specials. "Suggest knowledge" | **Knowledge & Fees** with classes (patient knowledge, patient communication, inquiry communication, laws and regulations, fees) and the same pending-approval queue |
| **Operations > Phone** | Call Log: direction, source, contact, call type, time, **agent (Voice AI or Office)**, score, duration, transcript, filters, export. Tabs: Call Log, Call Center, Voice Hub, Scoring Rubrics | Phone: every call, **handled by human or AI**, when, short note, transcript (permission-gated), export |
| **Reports** | Catalog: Agent Reporting, Closing Report, Pending Knowledge Insights, compliance grid, financial | Reports: agent (human vs AI) performance, pending-knowledge insights, inquiry-to-visit conversion, recall coverage |
| **Units, floorplans, lease, move-in, budget, pets** | Rental preferences | Pets and visit preferences: species, breed, age, weight, reason for visit, preferred vet and days, insurance, vaccination status |

Vocabulary: Prospect becomes **Inquiry** (new, never been seen). Resident becomes **Patient** (has visited). Open question for the owner: in a vet clinic the patient is the pet and the person is the client. The plan models a **person (Contact)** who owns **pets**, and the badge "Patient" means "this contact has an existing relationship". Confirm this wording.

---

## 2. Hard truths and gates (read before the roadmap)

1. **Privacy gate (BC PIPA).** The current app holds no real client data on purpose. This CRM exists to hold names, emails, phones, visit history and conversations. Nothing real is loaded until Dr. Garg has approved hosting location, retention, access rules and the breach process (`docs/SECURITY.md`). Until then everything runs on **invented contacts**.
2. **Consent gate (CASL, Canada's anti-spam law).** Texts and emails to clients, and especially mass campaigns, need recorded consent, sender identification and an unsubscribe. The screens show opt-in state per channel for a reason. The CRM must store consent, its source and date, and the send path must refuse anyone without it. Have a lawyer confirm the rules for transactional messages (appointment reminders) versus promotional ones before any real blast.
3. **Sending gate.** Today the app can only log "would send". Real SMS, email and calls need new send-capable integrations. Our TELUS connection is deliberately read-only; sending needs a separate app with write permissions, a separate credential, and an on/off switch. It is built last.
4. **AI safety gate.** Our assistant speaks only from fixed templates, which is why it cannot give medical advice. An EliseAI-style assistant writes free replies. The plan keeps the guarantees by: emergency detection first, answers only from human-approved knowledge, refusal of medical or dose questions, escalation to a human task when unsure, and a staged rollout (suggest drafts, then supervised send, then autonomous after hours). Each stage needs a passed evaluation. The current emergency detection is not good enough yet (7 of 12 sealed test emergencies). **No autonomous client messaging until that is fixed and re-tested.**
5. **ImproMed has no API.** Patient lists, visit history and appointments can only come from exported files (the read-only `PimsAdapter`). So the Guest Card's "previous visits" and the calendar's existing appointments are imports, not live. The app can show them and add its own events, but cannot write into ImproMed. Staff keep entering bookings there.
6. **Phones.** Reading calls and messages from TELUS is done (read-only). Calling from the chat and after-hours AI voice need extra integrations (section 7).

---

## 3. Architecture: what we reuse and what is new

Reused as is: sign-in, roles, audit events and live updates (SSE); the callbacks, overnight conversations, drafts and suggestions tables; the after-hours engine and emergency rules; call analytics; the TELUS read-only worker and its bridge folder; `PimsAdapter` imports; the Retell hook (off); the LLM adapter (OpenRouter and DeepSeek); the Admin and Brief screens.

New modules (all inside the existing FastAPI and React app, SQLite stays; one clinic does not need a bigger database):

```
Contacts and pets  ->  Conversations (messages, notes, reminders)  ->  Tasks
        |                       |                                        |
   Calendar events         Phone call log (TELUS + AI)            Knowledge and fees (pending queue)
        |                       |                                        |
   Campaigns (segments, consent, approvals)      AI assistant (draft, supervised, autonomous)   Reports
```

Data flow for one inbound text: TELUS message store (read, polled) -> matched to a contact by phone -> appended to the conversation -> AI assistant (if on for this contact) checks emergency rules, looks up approved knowledge, drafts a reply -> in shadow mode a human sees the draft; in autonomous mode it is sent -> anything unsure becomes a Task with the reason -> any new fact the AI picked up goes to Pending Knowledge.

Background work uses the pattern already proven: systemd timers and a request-file worker, so the web app never holds TELUS credentials.

---

## 4. Data model (new tables; names indicative)

- `contacts`: id, kind (inquiry, patient), display_name, email, email_consent (full, transactional only, none, unsubscribed), phone (E.164), phone_hash (lookup index), sms_consent, call_consent, consent_source, consent_at, source (phone call, walk-in, website, referral, import), owner_user_id, ai_enabled, status, created_at, last_active_at, notes_pinned. Phone and email stored encrypted with a hash for lookup.
- `contact_links`: contact_id, related_contact_id, relationship (friend, spouse, parent, neighbour, other), note. This is the "associated contacts" feature: a friend who has no pet can be the first point of contact for someone else's pet.
- `pets`: id, contact_id, name, species, breed, age or birth date, weight, sex, neutered, allergies note, insurance note, vaccination due dates, status.
- `contact_preferences`: key and value pairs (preferred vet, preferred days, reason for visit) with edit history.
- `visits`: id, contact_id, pet_id, date, type (check-up, surgery, vaccine, emergency, other), source (import, app), summary. Imported read-only from ImproMed exports.
- `conversations` (extend the existing table): contact_id, channel (sms, email, call, internal), status, last_message_at.
- `messages`: conversation_id, direction (in, out), channel, sender (client, human user, ai), body, attachments, delivery state (dry run, queued, sent, failed), ai_draft_of (if a draft), consent_checked, created_at.
- `notes`: contact_id or conversation_id, author, body (max 2000), mentions.
- `reminders`: owner, due_at, target contact, text, done.
- `tasks`: id, kind (unknown knowledge, escalation, callback, refill, booking, complaint, emergency follow-up, results, other), classification (inquiry, patient), contact_id, conversation_id, assigned_to, status (open, in progress, done), reason (fixed code plus short text), priority, due_at, raised_by (ai, human, rule).
- `events` (calendar): id, kind (check-up, surgery, vaccine, dental, consult, inquiry call, tour-style visit, staff, reminder, other), classification, contact_id, pet_id, vet_id, start, end, status, source (import, app), room or notes.
- `calls` (call log): id, started_at, direction, handled_by (human, ai), agent_label, contact_id (nullable), duration, result, short_note, transcript_ref, source (telus, retell).
- `knowledge_entries`: id, category (clinic info, fees, patient knowledge, patient communication, inquiry communication, laws and regulations, specials), title, body, version, status (pending, approved, declined, retired), proposed_by (human, ai, import), source_ref (conversation or call id), previous_body (for the diff), approved_by, approved_at.
- `fees`: service, price, unit, note, effective dates, approved status.
- `campaigns`: name, channel, segment filter (saved), template, merge fields, schedule, status (draft, awaiting approval, approved, sending, done, cancelled), approved_by, counts. `campaign_recipients`: campaign_id, contact_id, state, skipped reason (no consent, no number, duplicate).
- `saved_views`: user, screen, columns, filters.
- `access_log`: user, contact_id, action (viewed card, viewed transcript, exported, sent), time. Every look at a client record is logged.

---

## 5. Screen specifications

All screens follow the existing design system (same CSS, large click targets, plain words, light and dark), work on a phone, update live, and obey role permissions. The left menu becomes: Home, Tasks, Contacts, Calendar, Operations (Knowledge and Fees, Phone, Services), Reports, Brief, Overnight, Callbacks, Lab results, Schedule, After-hours setup, Admin. Existing screens stay.

### Home
Three columns: **Inquiries and Patients** (recent, with unread counts), **Calendar** (today and next days), **Tasks** (open). Toggle "Assigned to me only". Search box for patients, contacts and conversations. Call button. "Edit workstreams" to choose columns. Clicking a person opens their conversation with the Guest Card.

### Tasks
Left list with filters: task type, classification (Inquiry, Patient), assignee, status. Middle: the task, its reason, the related conversation. Right: Guest Card. Actions: assign, take it, add note, mark done, snooze, create reminder. Counts feed the left menu. New tasks appear live.

### Contacts
Table with columns: name, email, phone, status (Inquiry or Patient), last message, channels used, last active, created, owner, visit count, last visit. Search by name, email or phone. Filters: status, visit count (range), number of inquiries, last visit date range, owner, pet species, consent state, source, has open task, AI on or off. Column chooser and saved Views. **Add Contact** (walk-ins and phone-ins, with consent capture). **Create Campaign** from the current filtered list. Export CSV (logged and permissioned).

### Guest Card (panel and full page)
Header: avatar initials, name, status badge (switch Inquiry to Patient), edit. **Contact** tab: email and phone each with consent state, preference notes with edit history, owner picker, **AI assistant on or off**, associated contacts with relationship and add, pets (cards with visit history), previous visits and dates, add appointment, documents (upload, type and size limits). **Knowledge** tab: filtered clinic knowledge relevant to this person. **Calendar** tab: this contact's events. **Services** tab: service and fee lookup. "Full journey" shows the whole timeline: calls, messages, tasks, visits.

### Conversation view
Thread of all communication, with filter All, Email only, SMS only, Calls. Each message labelled with who sent it (client, a named staff member, AI assistant) and how. Composer: choose Email or Text message, rich text, templates, attach media, **Add internal note** (team-only, @ to notify a colleague, 2000 characters), **Add reminder**, schedule send. **Call** button starts a call (phase 9). Right panel is the Guest Card.

### Calendar
Day, week and month. Color by event class, staff member or room. Legend with toggles. Filters: patients, inquiries, check-ups, surgeries, vaccines, dental, staff, reminders, and a search. Create event (appointment, inquiry call-back, surgery slot) writes an app event and shows "enter in ImproMed" until sync exists. Imported ImproMed appointments appear read-only and are labelled with their source.

### Operations > Knowledge & Fees
Tabs by category. Search and filter. **Pending Knowledge**: a queue of entries the AI proposed (with the source conversation or call, a short quote, a confidence and category). Each shows previous versus new text. Approve, edit then approve, or decline; select several; approve all or decline all. "Conflicting edits only" toggle (new text contradicts an approved entry). **Suggest knowledge** lets staff add entries. Nothing in Pending is used by the AI until approved. Every decision is logged. Fees table with effective dates.

### Operations > Phone
Call log: time, direction, contact (if matched), **handled by (human name or AI)**, call type, duration, result, short note, transcript link (permission and audit), recording link (owner only). Filters: date range, direction, handled by, result, contact, search. Tabs: Call Log, Call Center (live and queue stats), Voice Hub (AI voice settings), Scoring (optional quality rubric). Export.

### Reports
Catalog with starred favourites: Agent reporting (human vs AI: volume, handling time, escalations), Pending knowledge insights (what the AI keeps not knowing), Inquiry to visit conversion, Missed and callback performance (already live), Recall coverage grid (vaccines and exams overdue), Campaign results. Each with date range, filters and export.

### Campaigns
Segment builder using the same filters as Contacts, template with merge fields (owner name, pet name, due date), preview per recipient, consent and quiet-hours checks with counts of who is skipped and why, owner approval, schedule, throttle, unsubscribe link and keyword, delivery log. Dry run until sending is enabled.

---

## 6. The AI system

**Roles of AI**, each independently switchable, all off by default:
1. **Knowledge extraction.** After each conversation or call, a model proposes facts it learned that are not in approved knowledge (a new fee, a policy detail, a service). They land in Pending Knowledge with their source. Humans approve.
2. **Reply drafting.** For an inbound message, the model drafts a reply using only approved knowledge, the contact's context and fixed style rules.
3. **Escalation.** When the model is unsure, lacks knowledge, detects a complaint, a medical question, a refill request or anything emergency-like, it creates a Task and tells the client a human will follow up.
4. **Call notes.** A short summary of each call for the call log.
5. **After-hours voice** (later): answers inbound calls outside opening hours only, using the same engine and rules.

**Guardrails (non-negotiable):**
- Emergency detection runs first on every client message, with the existing rules plus the model check; any hit routes to the emergency flow with only clinic-confirmed hospitals named.
- No diagnosis, no medicine or dose advice, no "your pet is fine". Medical questions always escalate.
- Replies may state only approved knowledge; no approved fact means escalate, never guess.
- Never reveal another client's information; the model only receives the current contact's data.
- Always disclose that it is an automated assistant; calls say they are recorded.
- Per-contact AI switch and a global kill switch. Quiet hours.
- Every AI message is stored with its inputs (which knowledge entries it used) for review.

**Rollout stages**, each gated by an evaluation on fresh sealed scenarios (emergency recall must be 100 percent, zero advice, escalation precision) and an owner sign-off:
- Stage A, **Suggest**: drafts appear only to staff; nothing sent by AI.
- Stage B, **Supervised**: AI sends low-risk replies (hours, address, booking requests) automatically, everything else drafts; staff review a daily sample.
- Stage C, **After-hours autonomous**: AI handles inbound after hours within the same rules, human tasks in the morning.

Model plumbing: OpenRouter with the models already tested (`deepseek/deepseek-v4.1-flash-1017`; `openai/whisper-large-v3-turbo` for speech). Provider and data-retention settings are pinned, and client text is minimised and stripped of unneeded identifiers before it leaves the server. This needs the owner's data-location approval.

---

## 7. Integrations

| Need | Approach | Status |
|---|---|---|
| Call history, voicemail, recordings | TELUS (RingCentral) read-only worker, bridge folder | **Done** (log and recordings button) |
| Inbound SMS | Poll the TELUS message store (read permission already granted), match to contact by phone | Phase 2 |
| Send SMS | Separate TELUS (RingCentral) app with SMS permission, own credential, on/off switch, consent check on every send | Phase 9 |
| Send email | A transactional email provider with the clinic's domain (SPF, DKIM), bounce and unsubscribe handling | Phase 9, owner chooses provider |
| Click-to-call | RingCentral call-out or a browser softphone; needs a write permission | Phase 9 |
| After-hours AI voice | Retell (hook exists, off) with the clinic number forwarded after hours | Phase 9 |
| Patients, clients, appointments | ImproMed CSV exports through `PimsAdapter` (read only) | Phase 1 and 3; needs real export files |
| Speech to text, language model | OpenRouter | Stage A onward |

---

## 8. Security and compliance requirements

- **Before real data:** two-factor sign-in; encrypted off-site backups; encryption at rest for contact fields; written retention and deletion rules; breach process; data location decided; consent records.
- **Access:** role matrix extended (owner: everything; desk: contacts, conversations, tasks, calendar, campaigns draft; nurse: contacts, conversations, tasks, calendar, no campaigns, no transcripts by default). Transcripts and recordings are owner or named roles only.
- **Audit:** every record view, export, send and AI decision is logged in `access_log` and `events`.
- **Consent enforcement in code:** one send function that checks consent, quiet hours and the kill switch. Nothing else may send.
- **Data minimisation:** no free text from clients in logs; model prompts carry only what the task needs.
- **Deletion:** a person can be erased on request (contact, messages, notes), with a tested procedure.

---

## 9. Roadmap, with acceptance checks

One **wave** below means one parallel build round like the last ones (contract, builders, my review, deploy, test through the public URL). Wave counts are effort, not calendar time.

**Phase 0: Gates (decisions, no code).** Owner approvals: hosting location and data residency; retention periods; who may see transcripts; AI provider data terms; consent wording; clinic hours; vocabulary (client and patient). Real ImproMed export files. *Acceptance: a signed-off checklist in `docs/DECISIONS.md`. Runs in parallel with Phase 1.*

**Phase 1: Contacts and Guest Card (about 3 waves).** Tables for contacts, pets, links, preferences, consent, visits, notes, access log; seed with invented clients; Contacts table with search, filters, columns, saved views, add contact, CSV export; Guest Card panel and page. Import of ImproMed client, patient and appointment CSVs (read only), switched on only after Phase 0. *Acceptance: desk adds a walk-in, finds them by phone, edits consent, links an associated contact; permission tests; nothing real loaded.*

**Phase 2: Conversations, Tasks and Home (about 3 waves).** Messages and conversation view with channel filter, internal notes with @mentions, reminders, attachments; the existing overnight conversations and callbacks migrate into the new model; Tasks page with typing, classification, assignment; Home with three workstreams; TELUS inbound SMS polling and call matching by phone; outbound is dry run. *Acceptance: two people on two devices see a new task and a note live; a text from a known number lands in the right thread.*

**Phase 3: Calendar (about 2 waves).** Events, filters, day and week and month, create, per-contact calendar tab, ImproMed appointment import, staff shifts overlay. *Acceptance: filter to surgeries this week; imported and app events distinguishable.*

**Phase 4: Phone (about 2 waves).** Call log page from TELUS with handled-by, contact matching, short notes (AI summary only after approval), transcript view with audit, export, Call Center stats. Connects to the recordings and transcription pipeline. *Acceptance: yesterday's calls listed with correct direction and result; transcript opens only for permitted roles and is logged.*

**Phase 5: Knowledge & Fees (about 2 waves).** Categories, versions, pending queue with diff and bulk actions, suggest knowledge, fees table, seed from `docs/ANSWER_SHEET_DRAFT.md` (marked unconfirmed). AI extraction from conversations and calls (gated by AI settings). *Acceptance: an extracted fact appears in Pending, is approved, and becomes available to the assistant; declined facts never are.*

**Phase 6: AI assistant (about 3 waves plus evaluation).** Stage A suggestions first, with the fix to emergency detection and a new sealed evaluation. Then Stage B and C only after each passes its evaluation and the owner signs off. *Acceptance: documented evaluation results; emergency recall 100 percent on fresh scenarios; zero advice; kill switch tested.*

**Phase 7: Campaigns (about 2 waves).** Segments, templates, merge fields, consent and quiet-hours filtering, approval, schedule, throttle, unsubscribe, delivery log, all dry run. *Acceptance: a campaign preview shows exactly who is excluded and why; no real send possible.*

**Phase 8: Reports (about 1 wave).** The catalog above, filters, favourites, export. *Acceptance: each report matches a hand count on the invented data.*

**Phase 9: Real sending, calling and voice (as providers are chosen).** One integration at a time, each behind its own switch, consent check and pilot with staff as recipients. *Acceptance: a supervised pilot with a staff phone number before any client.*

Rough total: 18 to 22 waves of building and reviewing. A working, testable CRM on invented data (Phases 1 to 3) is about 8 waves.

---

## 10. Testing

Per phase: backend unit and API tests, the permission walker for every new route, frontend build with zero type errors, a browser flow on the public URL for each role, and a check that no personal data appears in logs. Assistant work adds sealed evaluation sets written without access to the code (as done for the independent test). Campaign and send paths get tests that prove a contact without consent is never sent to.

## 11. Decisions I need from the owner

1. Approve the privacy gate order: invented data first, real data only after sign-off.
2. Confirm vocabulary: Contact (person) with Pets; badge Inquiry or Patient.
3. Which email provider and whether to buy a clinic domain for sending.
4. Whether to allow the AI to send anything before Stage A passes (recommendation: no).
5. Real ImproMed export files, and who can provide them.
6. Who approves knowledge entries and fees (name or role).
7. Opening hours and after-hours rules.
8. Priority order if different from the roadmap.

## 12. Risks

- The emergency detector is the weakest part and the most important; the AI stages depend on fixing it.
- Real client data raises legal duties (PIPA, CASL, veterinary record rules); the gates are there to protect the clinic.
- ImproMed's lack of an API means calendar and visit history are only as fresh as the last export.
- The model may invent facts; the pending-approval design and escalation rules are the defence, and must not be weakened for convenience.
- Scope is large; the order above keeps something usable at each step.

## 13. Proposed next step

Build Phases 1 and 2 on invented contacts (Contacts, Guest Card, conversation view, Tasks, Home), so you can see and use the interface in a few waves, while the owner works through Phase 0. No real data and no real sending until the gates are passed.
