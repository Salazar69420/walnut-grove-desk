# What the 3,637 recorded calls say about the CRM (aggregate only)

Source: the owner's transcript export (3,637 recordings, July 29 to October 26, 2026). This page holds **counts and shares only**: no transcript text, names, phone numbers or call references. The analysis ran outside the repo and outside the private store; it is a keyword pass, so every share below is rough (a call can match several topics, a keyword is not a diagnosis). Treat it as direction for what to build first, not a measurement.

## Shape of the data

| Fact | Value | What it means for the CRM |
|---|---|---|
| Recordings | 3,637, 96.1 hours, 21 dollars of transcription | |
| Median length | 52 seconds; 90th percentile 3 min 24 s | Most calls are short: booking, hours, a message |
| Length buckets | under 15 s: 353, 15 to 60 s: 1,635, 1 to 3 min: 1,202, 3 to 10 min: 399, over 10 min: 48 | Long calls are billing, insurance, complaints and end of life (see below) |
| Busiest hours | 9 am (468), then a flat 10 am to 4 pm; 6 pm and later is thin | Matches the Brief chart; staff the 9 am peak |
| Weekdays | Mon 479, Tue 587, Wed 669, Thu 639, Fri 657, Sat 405, Sun 201 | Saturday is busy enough to need real hours and cover |
| After hours or weekend | about 19.5 percent | The after-hours assistant has real volume to take |
| Speakers per recording | 1: 163, 2: 2,166, 3 or more: 1,308 | Many recordings are **in-clinic conversations**, not phone calls. The call log needs a call versus conversation classifier, and private content must be separated |
| Language | English 3,597; Hindi 32; Punjabi 4; French 2 | Mostly English. A small but real Hindi and Punjabi share to plan for |

## Data problems that affect the design

1. **The `number` column cannot identify callers.** 2,241 rows are blank and 1,385 carry one repeated number (almost certainly the clinic's own line). Only about 11 rows have anything else. So **new versus repeat caller, and matching a call to a Guest Card, cannot come from this file.** It must come from the TELUS call log (real from/to numbers), which the read-only connector already pulls.
2. **The `direction` column is unreliable** (3,627 of 3,637 say "Outgoing"). Direction must also come from the TELUS call log.
3. **Names live only in the audio.** Contact matching from a recording would need a model to extract the caller's name, which is a privacy decision (see `docs/SECURITY.md`), not something to switch on quietly.

## What callers talk about (share of recordings that mention the topic)

| Topic | Calls | Share | Average length | CRM feature it drives |
|---|---|---|---|---|
| Clinic greeting heard (client-facing call) | 2,741 | 75% | 1 min 19 s | Marks real inbound calls versus conversations |
| Booking, availability, appointments | 1,363 | 37.5% | 1 min 55 s | Calendar, booking Task, after-hours booking flow |
| Asking for the doctor | 802 | 22% | 2 min 22 s | Escalation Task type |
| Hours, location, parking | 746 | 20.5% | 3 min 11 s | Knowledge: Clinic info (first thing to approve) |
| Results, bloodwork, imaging | 626 | 17% | 2 min 47 s | Lab results workflow; assistant never reads results out |
| Records, transfers, microchip, travel | 597 | 16% | 1 min 56 s | Knowledge: patient communication, records Task |
| Prescriptions and refills | 577 | 16% | 2 min 42 s | Refill Task, doctor approval |
| Vaccines, flea, tick, heartworm | 545 | 15% | 2 min 18 s | Recall coverage report, vaccine reminder campaign |
| Pickup and drop-off | 445 | 12% | 2 min 25 s | Surgery pickup knowledge, outbound call task |
| Food and diet | 434 | 12% | 3 min 6 s | Knowledge: patient knowledge |
| Price and cost | 419 | 11.5% | 4 min 9 s | Fees (approval required; prices heard differ) |
| Surgery, spay, neuter, anesthesia | 395 | 11% | 2 min 38 s | Surgery event class, fasting knowledge |
| A callback was promised | 377 | 10.4% | 2 min 11 s | **Auto-raise a Task whenever someone says "I will call you back"** |
| Voicemail | 374 | 10.3% | 48 s | Voicemail intake, matched to a contact by number |
| Stomach or eating problems | 268 | 7.4% | 3 min 10 s | Triage wording and emergency rules |
| Emergency words (keyword only) | 257 | 7.1% | 2 min 25 s | Keyword hit is not an emergency; needs the model check |
| Skin, ear, eye, limping, lumps | 229 | 6.3% | 3 min 11 s | Triage wording |
| Reschedule or cancel | 164 | 4.5% | 2 min 23 s | Calendar status changes |
| Dental | 158 | 4.3% | 2 min 29 s | Dental campaign and event class |
| Payment, invoice, refund | 139 | 3.8% | 5 min 22 s | Escalate to a person, never the assistant |
| New client or new pet | 124 | 3.4% | 3 min 52 s | Inquiry intake, new-client knowledge |
| Boarding, grooming | 84 | 2.3% | 2 min 46 s | Services list |
| Insurance | 77 | 2.1% | 4 min 43 s | Escalate; insurer decides, clinic fills forms |
| Upset or complaint language | 61 | 1.7% | **7 min 51 s** | Complaint Task, human only |
| End of life | 44 | 1.2% | 5 min 9 s | Human only, gentle wording, never a campaign target |
| Dogs 20.6%, cats 13.7%, other pets 0.4% | | | | Species filter on Contacts and Campaigns |

## What this changes in the plan

- **Build order inside Knowledge & Fees:** hours and location, booking and what to bring, records, refills, results policy, fees. Together these cover most of what callers ask.
- **Tasks:** one in ten calls promises a callback. That alone justifies the callback Task type and automatic Task creation from call notes.
- **Never automate:** complaints, payments, insurance, end of life and results. These are the longest calls and the ones where a wrong answer costs most. The assistant escalates them.
- **Calls versus conversations:** a quarter of recordings have no clinic greeting. Before any real call goes into a Guest Card, classify it, and keep the private-content flag from `docs/DATA_AUDIT.md`.
- **Caller identity must come from TELUS metadata**, not from the transcript file.
- **Re-run after approval:** repeat this on the private store with a model labeler once the owner approves the privacy decision. The keyword method here is a first look and over-counts (for example, "open" and "close" appear in surgery talk).
