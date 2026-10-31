# Contract: Clinic CRM, phases 1 and 2 (Contacts, Guest Card, Conversations, Tasks, Home)

Binding for both builders. Read `docs/PLAN_CLINIC_CRM.md` sections 1, 4, 5 and 8 first, then `docs/CONTRACT.md` (rules, auth, events, SSE) and `docs/API.md` (what exists). This contract wins on detail. No em dashes anywhere. The repo is public.

## Ground rules

- **Invented data only.** Every contact, pet, phone, email and message is made up. Phones only in the fictional range `604-555-0100` to `604-555-0199` (and `778-555-01xx`); emails only `@example.com` or `@example.org`. Seed rows carry `is_demo = 1`. "Reset demo data" deletes only `is_demo = 1` rows, so real rows added later are never touched.
- **Nothing is ever really sent.** Messages get `delivery_state = "dry_run"` and an `outbox` row with status `would_send`. The composer shows "Practice mode: nothing is really sent".
- **No TELUS or real-number matching in this phase.** That waits for the privacy gate.
- **Every look at a contact is logged** in `access_log` (view, export, erase, send).
- Server-side permissions on every route via `require()`, so the permission-walker test covers them.
- Keep all existing screens and tests working.

## Permissions (add to `backend/app/security/permissions.py`)

| perm | desk | nurse | owner |
|---|---|---|---|
| `contacts.view`, `contacts.edit`, `messages.view`, `messages.send`, `notes.write`, `tasks.view`, `tasks.work`, `calendar.view`, `home.view` | yes | yes | yes |
| `contacts.export` | yes | no | yes |
| `calendar.edit` | yes | no | yes |
Admin-only (in `ADMIN_PERMS`): `admin.contacts_erase`, `admin.access_log`.

## Tables (migration `0003_crm.py`, all with `is_demo` boolean default 0)

`contacts` (id, kind `inquiry|patient`, display_name, email, email_consent `full|transactional|none|unsubscribed`, phone (E.164), sms_consent (same values), consent_source, consent_at, source `phone|walk_in|website|referral|after_hours|import|other`, owner_user_id, ai_enabled, preference_notes, created_at, last_active_at, erased_at), `contact_note_history` (contact_id, old_text, new_text, edited_by, edited_at), `pets` (id, contact_id, name, species, breed, sex, birth_date, weight_kg, notes, status `active|deceased|moved`), `contact_links` (id, contact_id, related_contact_id, relationship `friend|spouse|partner|parent|child|neighbour|family|caregiver|other`, note), `visits` (id, contact_id, pet_id, visited_at, kind `checkup|vaccine|surgery|dental|sick|emergency|other`, summary, source `import|app`), `messages` (id, contact_id, channel `sms|email|call`, direction `in|out`, sender_kind `client|staff|ai`, sender_user_id, subject, body, delivery_state `received|dry_run|scheduled_dry_run`, scheduled_for, created_at), `notes` (id, contact_id, author_id, body, created_at), `note_mentions` (note_id, user_id), `reminders` (id, contact_id, owner_id, due_at, text, done_at), `attachments` (id, contact_id, message_id, note_id, filename, mime, bytes, sha256, stored_name, uploaded_by, created_at), `tasks` (id, kind `unknown_knowledge|escalation|callback|refill|booking|complaint|emergency_followup|results|other`, classification `inquiry|patient`, title, reason, contact_id, assigned_to, priority `high|normal|low`, status `open|in_progress|done`, due_at, raised_by `ai|human|rule`, created_at, done_at, done_by), `calendar_events` (id, kind `checkup|vaccine|surgery|dental|consult|inquiry_call|staff|reminder|other`, classification `inquiry|patient|none`, title, contact_id, pet_id, staff_user_id, starts_at, ends_at, status `booked|requested|done|cancelled`, source `import|app`), `notifications` (id, user_id, kind `mention|task_assigned|reminder`, text, link, created_at, read_at), `saved_views` (id, user_id, screen, name, filters_json, columns_json), `access_log` (id, user_id, contact_id, action `view|export|erase|send`, at, ip). Indexes on contact foreign keys, `contacts.last_active_at`, `tasks.status`, `calendar_events.starts_at`.

## API (JSON, snake_case, CSRF on writes, errors `{"detail": "plain sentence"}`)

**Staff and search**
- `GET /api/staff` (contacts.view): `{"items":[{"id","display_name","initials","role"}]}` active users only (no e2e accounts).
- `GET /api/search?q=` (contacts.view, q 2 to 80 chars): `{"contacts":[{id,display_name,kind,match}], "pets":[{id,name,species,contact:{id,display_name}}], "tasks":[{id,title,status}], "messages":[{id,contact_id,contact_name,preview,at}]}` max 8 each.

**Contacts**
- `GET /api/contacts` (contacts.view). Query: `q`, `kind`, `owner` (`me|none|<id>`), `email_consent`, `sms_consent`, `species`, `visits_min`, `visits_max`, `last_visit_from`, `last_visit_to` (YYYY-MM-DD), `has_open_task` (bool), `ai` (`on|off`), `source`, `sort` (`name|last_active|created|last_visit|visits`), `dir` (`asc|desc`), `page` (1+), `per_page` (10 to 100, default 50). Returns `{"items":[Row],"total","page","per_page"}` where Row = `{id, display_name, initials, avatar_hue (0 to 359 from a hash of id), kind, email, phone, phone_display ("(604) 555-0123"), email_consent, sms_consent, owner:{id,display_name}|null, ai_enabled, source, visit_count, last_visit_at, pet_names:[], open_tasks, unread, last_message:{channel,direction,preview,at}|null, last_active_at, created_at}`. Previews are at most 80 characters.
- `POST /api/contacts` (contacts.edit): `{display_name (2 to 120), kind, email?, phone?, email_consent, sms_consent, consent_source?, source, owner_id?, preference_notes?, pets?:[{name,species,breed?}]}`. Phone normalised to E.164 (Canadian default), email lowercased and validated. 409 `{"detail":"A contact with this phone already exists","existing_id":n}` on a duplicate phone or email. Event tool "Contacts": "Priya added a contact".
- `GET /api/contacts/{id}` (contacts.view, logs `view`): `{"contact":{...Row, preference_notes, consent_source, consent_at}, "pets":[...], "links":[{id, relationship, note, contact:{id,display_name,kind,initials,avatar_hue}}], "visits":[{id,pet:{id,name}|null,visited_at,kind,summary,source}], "upcoming_events":[Event], "open_tasks":[Task], "attachments":[{id,filename,mime,bytes,created_at}], "counts":{"messages","notes","visits"}}`.
- `PATCH /api/contacts/{id}` (contacts.edit): any of `display_name, kind, email, phone, email_consent, sms_consent, consent_source, owner_id, ai_enabled, preference_notes, source`. Changing `preference_notes` writes `contact_note_history`. Changing consent sets `consent_at`. Events for kind, owner and consent changes.
- `GET /api/contacts/{id}/note-history` (contacts.view).
- `POST /api/contacts/{id}/pets`, `PATCH /api/pets/{id}` (contacts.edit).
- `POST /api/contacts/{id}/links` (contacts.edit): `{relationship, note?, related_contact_id}` or `{relationship, note?, new_contact:{display_name, phone?, email?, kind}}` (creates the contact). Links are shown from both sides. `DELETE /api/contact-links/{id}` (contacts.edit).
- `GET /api/export/contacts.csv` (contacts.export, same filters as the list, formula-safe, logs `export` per contact... one access_log row with contact_id null and a count is fine).
- `POST /api/admin/contacts/{id}/erase` (admin.contacts_erase): deletes the contact and every child row, sets nothing recoverable, `PRAGMA secure_delete` on, event without content.
- `GET /api/admin/access-log` (admin.access_log): paginated, filter by user and contact.

**Timeline and messages**
- `GET /api/contacts/{id}/timeline?channel=all|email|sms|call|note&before=<iso>&limit=1..100` (messages.view): `{"items":[Item],"next_before"}` newest first. Item = `{id, type:"message"|"note"|"visit"|"task", channel, direction, sender:{kind,name,initials}, subject, body, at, delivery_state, scheduled_for, attachments:[{id,filename,mime,bytes}], mentions:[{id,display_name}]}`.
- `POST /api/contacts/{id}/messages` (messages.send): `{channel:"sms"|"email", subject? (email only, max 150), body (1 to 2000), scheduled_for?, attachment_ids?:[]}`. Refuse with 409 and plain text when consent is `none` or `unsubscribed` for that channel, or the contact has no phone or email. Creates the message with `dry_run` or `scheduled_dry_run`, an outbox row `would_send`, logs `send`, updates `last_active_at`, event tool "Conversations".
- `POST /api/contacts/{id}/notes` (notes.write): `{body (1 to 2000), mention_ids:[]}`. Each mention creates a `notifications` row (kind `mention`, text "Priya mentioned you on Chen", link `/contacts/{id}`).
- `POST /api/contacts/{id}/reminders` (notes.write): `{due_at, text (1 to 200), owner_id?}`; `GET /api/reminders?mine=1&done=0`; `POST /api/reminders/{id}/done`.
- `POST /api/contacts/{id}/attachments` (messages.send) multipart, one file, at most 5 MB, only JPEG, PNG, WEBP, PDF, checked by magic bytes not by name; stored under `WALNUT_ATTACH_DIR` (default `/var/lib/walnut-desk/attachments`) with a random name, mode 600; returns `{"id","filename","mime","bytes"}`. `GET /api/attachments/{id}` (contacts.view): streams with `Content-Disposition: attachment`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: sandbox`. Filenames are sanitised for display only.

**Notifications**
- `GET /api/notifications` (any signed-in user): `{"items":[...],"unread"}` latest 50. `POST /api/notifications/{id}/read`, `POST /api/notifications/read-all`.

**Tasks**
- `GET /api/tasks?status=open|in_progress|done|active|all&classification=&kind=&assigned=me|none|all|<id>&contact_id=&q=` (tasks.view): `{"items":[Task],"counts":{"open","in_progress","mine","unassigned"}}`. Task = `{id, kind, kind_label, classification, title, reason, priority, status, due_at, raised_by, created_at, contact:{id,display_name,kind,initials,avatar_hue}|null, assigned:{id,display_name,initials}|null}`. Default sort: high priority first, then oldest.
- `POST /api/tasks` (tasks.work): `{kind, classification, title, reason?, contact_id?, assigned_to?, priority?, due_at?}` (raised_by human).
- `PATCH /api/tasks/{id}` (tasks.work): `status`, `assigned_to`, `priority`, `due_at`. Assigning to someone else creates a `task_assigned` notification. 409 on a stale update (send `expected_status`).
- `POST /api/tasks/{id}/take` (tasks.work).
- The after-hours engine's outcomes (`aftercare/service.py persist_outcome`) also create a task with `raised_by = "ai"`: booking -> `booking`, callback or refill -> `callback` or `refill`, emergency -> `emergency_followup` (priority high). Do not change the engine itself.

**Calendar (phase 2 minimum, full Calendar screen is phase 3)**
- `GET /api/events?from=&to=&contact_id=&kind=` (calendar.view): `{"items":[Event]}`. Event = `{id, kind, kind_label, classification, title, starts_at, ends_at, status, source, contact:{id,display_name}|null, pet:{id,name}|null, staff:{id,display_name}|null}`.

**Home**
- `GET /api/home?mine=0|1` (home.view): `{"people":[Row] (20 most recently active, unread first), "events":[Event] (today plus 6 days), "tasks":[Task] (open and in progress, max 20), "counts":{"unread_people","events_today","open_tasks","my_tasks"}}`. `mine=1` filters people by owner, events by staff, tasks by assignee.

**Saved views**
- `GET /api/views?screen=contacts|tasks`, `POST /api/views {screen,name (1 to 60),filters,columns}`, `DELETE /api/views/{id}` (own views only).

## Seed (extend `python -m app.seed --reset-demo`)

Make it feel like a real, busy small clinic, all invented. About 30 contacts (18 patients, 12 inquiries). Reuse the existing demo families so screens agree: Chen (Biscuit, Labrador), Walker (Pepper, dog), Dhillon (Simba, cat), Nguyen (Mochi, rabbit), Tran (Willow, dog), Okafor (Teddy, dog), Fraser (Luna, Husky), Ali (Rocky, Boxer), Pereira (Oscar, beagle), Singh (Maple, spaniel), plus about 20 more invented households with varied species. Consent mix: most full, some transactional only, a few none and one unsubscribed. Owners spread across Priya and Marcus, some unassigned; AI switched off on two contacts. Visits over the last two years with realistic kinds. Four associated-contact links, including a friend with no pet who first called about someone else's dog, and a spouse. About 150 timeline items: emails and texts both ways (AI assistant and staff as senders), call entries, internal notes with @mentions. About 15 tasks across every kind with reasons in plain words ("Asked whether we board cats over Christmas. No knowledge entry yet."), several raised by AI, some unassigned, two high priority. About 25 calendar events over the next 14 days (check-ups, vaccines, two surgeries, dental, inquiry call-backs). Three reminders. Two unread notifications for Priya. All text invented, friendly, no medical advice, Canadian spelling.

## UI (the bar is: better than the reference product)

Design language: extend `frontend/src/styles.css` (same tokens, light and dark), 8 px spacing grid, type scale 12, 13, 14, 16, 20, 24, consistent 10 px radius, subtle elevation for panels, visible focus rings, 44 px minimum click targets, WCAG AA contrast in both themes. Plain short words. Relative times ("3 min ago") with the exact time on hover. Skeleton loaders, never a blank flash. Clear empty states with the next action. Toasts for every action, with Undo where it is cheap (status changes). Optimistic updates that roll back on error.

**Navigation.** Left menu groups: *Workspace* (Home, Tasks with count, Contacts), *Clinic* (Brief for owner, Overnight, Callbacks, Lab results, Schedule), *Setup* (After-hours setup, How it fits, Admin). Only built screens are listed. Top bar gains a search box (Ctrl or Cmd K opens a command palette: people, pets, tasks, messages, and "Go to" screens) and a notifications bell with unread count and a dropdown. On Workspace screens the assistant panel is hidden by default and opens from an "Assistant" button as a right drawer. Desk users land on Home.

**Home.** Three columns: *Inquiries and patients* (avatar, name, kind badge, last message preview, unread dot, time), *Calendar* (grouped by day, colored by kind), *Tasks* (priority, kind, person, assignee). "Assigned to me only" switch at the top, plus counts. Each item opens the right place. Phone width: the three columns become tabs.

**Contacts.** Sticky-header table with the Row fields, row height 52 px, avatar with initials, kind badge, consent dots (green full, amber transactional, grey none, red unsubscribed) with labels on hover. Filter bar: search, then chips for kind, owner, consent, species, visits range, last visit range, open task, AI, source; "Clear all"; Columns chooser; saved Views dropdown (save, rename, delete). Sorting by clicking headers. Pagination with total. "Add contact" opens a side sheet with consent capture (required choice, with a plain explanation) and duplicate detection showing the existing contact. "Export CSV" for allowed roles. Keyboard: up and down to move, Enter to open. Phone width: cards instead of a table.

**Contact page (conversation plus Guest Card).** Header: back, avatar, name, kind badge, owner, a one-line summary ("Looking for a vaccine visit for Willow"), and a disabled "Call" button with the tooltip "Calling comes in a later phase". Centre: timeline with day separators, bubbles (client left; staff and AI right, AI marked with a small "AI assistant" label), notes in a distinct yellow-tinted card visible only to staff, visits and tasks as compact system lines, channel filter (All, Text, Email, Calls, Notes), load older on scroll. Composer: Text or Email switch (Email shows Subject), body with a live counter (texts show segments of 160), Add internal note (tab inside the composer, @ autocomplete from `/api/staff`), Add reminder (date and time picker), Schedule send, Attach (drag and drop, preview chips), Send with Ctrl or Cmd Enter, the practice-mode line, consent warning that disables Send when consent is missing for that channel. Right: Guest Card panel (collapsible with a chevron, 380 px) with tabs Contact, Pets, Calendar, Services:
- Contact: email and phone each with a consent pill and edit; preference notes with autosave and "Edit history"; owner picker; AI assistant switch; associated contacts with relationship and "Add" (search existing or create new inline); source; created and last active; documents (attachments) list; "Erase contact" for admins behind a typed confirmation.
- Pets: one card per pet (species icon via text emoji is fine, breed, age from birth date, weight, status) with visit history and "Add pet".
- Calendar: upcoming and past events for this person.
- Services: the clinic's visit types and lengths from the existing settings (fees come in phase 5), read-only.
Phone width: the Guest Card becomes a tab next to the conversation.

**Tasks.** Left: filters (status tabs Open, In progress, Done; classification Inquiry or Patient; kind; assignee with "Me", "Unassigned", "Everyone"; search), list with priority stripe, kind label, person, time, assignee avatar. Middle: the task detail with reason, raised by (AI shows a small badge "Raised by AI"), actions (Take it, Assign, Start, Done with Undo, Change priority, Due date), and the last few timeline items of the person with a link to the conversation. Right: the same Guest Card component. Empty state: "You are all caught up".

**Notifications.** Bell dropdown with mention and assignment items, mark read on open, "Mark all read".

**Quality bar for the builder.** Run the app locally, take screenshots of every new screen in light and dark at 1440 and 390 px wide, look at them, fix anything cramped, misaligned or unclear, and compare with the reference product photos in `/root/.claude/uploads/3f04d535-e357-5656-81b9-005e04389df3/1450e406-Elise_ui_-compressed.pdf` (render pages with `pdftoppm`; never copy any name, email or number from them).
