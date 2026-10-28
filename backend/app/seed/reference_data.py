"""Demo seed data for Walnut Grove Desk.

All data in this module is invented demo content copied from the prototype
(reference/mock-reference.html). It is not real clinic, client, pet, staff,
or hospital information. No phone numbers or addresses are included.
"""

HOURS_LABELS = ["8a", "9a", "10a", "11a", "12p", "1p", "2p", "3p", "4p", "5p"]
EXAMPLE_CALLS = [14, 21, 18, 15, 9, 12, 16, 17, 13, 6]
EXAMPLE_COVER = [1, 1, 2, 2, 1, 2, 2, 2, 2, 1]
LOAD_LIMIT = 12

CALLBACKS = [
    {"id": "c1", "who": "Dhillon", "pet": "Simba (cat)", "what": "Asking about blood test results", "kind": "Results", "at": "9:31 am", "via": "Voicemail", "prio": "high", "flag": False, "owner": None, "status": "new"},
    {"id": "c5", "who": "Nguyen", "pet": "Mochi (rabbit)", "what": "Not eating since last night, asks if she should come in", "kind": "Triage", "at": "9:20 am", "via": "Voicemail", "prio": "high", "flag": True, "owner": None, "status": "new"},
    {"id": "c3", "who": "Walker", "pet": "Pepper (dog)", "what": "Missed call, no message. Food order arrived.", "kind": "Pickup", "at": "9:38 am", "via": "Missed call", "prio": "normal", "flag": False, "owner": None, "status": "new"},
    {"id": "c2", "who": "Chen", "pet": "Biscuit (Labrador)", "what": "Needs a carprofen refill before the weekend", "kind": "Refill", "at": "8:12 am", "via": "Voicemail", "prio": "normal", "flag": False, "owner": None, "status": "new"},
    {"id": "c4", "who": "Langley Referral Clinic", "pet": "Maple (spaniel)", "what": "Asking for records to be sent", "kind": "Records", "at": "8:55 am", "via": "Voicemail", "prio": "normal", "flag": False, "owner": "Priya", "status": "claimed"},
    {"id": "c7", "who": "Fraser", "pet": "Luna (Husky)", "what": "Needs a refill. Taken overnight by the assistant.", "kind": "Refill", "at": "6:55 am", "via": "Call", "prio": "normal", "flag": False, "owner": None, "status": "new"},
    {"id": "c6", "who": "Lab courier", "pet": "", "what": "Confirm tomorrow pickup time", "kind": "Courier", "at": "8:02 am", "via": "Call", "prio": "normal", "flag": False, "owner": "Marcus", "status": "done"},
]

RESULTS = [
    {"id": "r1", "pet": "Maple (spaniel)", "owner": "Singh", "test": "Blood panel", "age": 3, "stage": 0},
    {"id": "r2", "pet": "Simba (cat)", "owner": "Dhillon", "test": "Thyroid panel", "age": 2, "stage": 1},
    {"id": "r4", "pet": "Luna (Husky)", "owner": "Fraser", "test": "Cytology", "age": 2, "stage": 0},
    {"id": "r3", "pet": "Rocky (Boxer)", "owner": "Ali", "test": "Urinalysis", "age": 1, "stage": 1},
    {"id": "r5", "pet": "Oscar (beagle)", "owner": "Pereira", "test": "Fecal test", "age": 0, "stage": 2},
]

DRAFTS = [
    {"id": "d1", "kind": "Annual exam recall", "to": "Ms. Chen, for Biscuit", "body": "Hi Ms. Chen, it is Walnut Grove Animal Hospital. Biscuit is due for her annual exam. Would you like us to book a time this week? Reply YES and we will call you.", "status": "pending"},
    {"id": "d2", "kind": "Ready for pickup", "to": "Mr. Walker, for Pepper", "body": "Hi Mr. Walker, Pepper's food order is in and ready for pickup at the front desk. We are open until 6 pm today.", "status": "pending"},
    {"id": "d3", "kind": "Post-surgery check-in", "to": "Mr. Ali, for Rocky", "body": "Hi Mr. Ali, how is Rocky doing after his dental yesterday? If he is not eating, or the gums look red or swollen, please call us today.", "status": "pending"},
]

SUGGESTIONS = [
    {"id": "r1", "rule_key": "desk_shift", "title": "Add an open desk shift, 8:00 to 10:00, Monday to Friday", "why": "Calls peak at 9 am (about 21 an hour in this example) with one person on the phones. Add a second person and each person's load drops by half.", "status": "open"},
    {"id": "r2", "rule_key": "missed_texts", "title": "Turn on automatic texts to missed callers", "why": "41 calls went unanswered last week and 13 of those callers never rang again. A text with a booking link reaches them.", "status": "open"},
    {"id": "r3", "rule_key": "recall_batch", "title": "Start the annual exam recall batch (120 clients overdue)", "why": "Drafts go to the front desk for approval, 10 at a time, so no one is overloaded.", "status": "open"},
    {"id": "r4", "rule_key": "old_results", "title": "Review lab results older than two days", "why": "Three results are waiting for a doctor review or a client call. Nurses can call once you mark them reviewed.", "status": "open"},
]

RECALL_EXTRA_DRAFTS = [
    {"id": "d4", "kind": "Annual exam recall", "to": "Ms. Okafor, for Teddy", "body": "Hi Ms. Okafor, it is Walnut Grove Animal Hospital. Teddy is due for his annual exam. Would you like us to book a time this week? Reply YES and we will call you.", "status": "pending"},
    {"id": "d5", "kind": "Annual exam recall", "to": "Mr. Tran, for Willow", "body": "Hi Mr. Tran, it is Walnut Grove Animal Hospital. Willow is due for her annual exam and vaccines. Reply YES and we will call you to book.", "status": "pending"},
]

# Expanded from buildRoster(). weekday: 0=Mon .. 6=Sun. Times are minutes after midnight.
ROSTER = [
    # Mon
    {"weekday": 0, "who": "Marcus", "role": "desk", "start_min": 480, "end_min": 990},
    {"weekday": 0, "who": "Priya", "role": "desk", "start_min": 600, "end_min": 1080},
    {"weekday": 0, "who": "Anita", "role": "nurse", "start_min": 510, "end_min": 1020},
    {"weekday": 0, "who": "Dr. Garg", "role": "doc", "start_min": 510, "end_min": 1050},
    # Tue
    {"weekday": 1, "who": "Marcus", "role": "desk", "start_min": 480, "end_min": 990},
    {"weekday": 1, "who": "Priya", "role": "desk", "start_min": 600, "end_min": 1080},
    {"weekday": 1, "who": "Anita", "role": "nurse", "start_min": 510, "end_min": 1020},
    {"weekday": 1, "who": "Dr. Garg", "role": "doc", "start_min": 510, "end_min": 1050},
    # Wed
    {"weekday": 2, "who": "Priya", "role": "desk", "start_min": 480, "end_min": 990},
    {"weekday": 2, "who": "Marcus", "role": "desk", "start_min": 600, "end_min": 1080},
    {"weekday": 2, "who": "Jordan", "role": "nurse", "start_min": 510, "end_min": 1020},
    {"weekday": 2, "who": "Dr. Garg", "role": "doc", "start_min": 510, "end_min": 1050},
    # Thu
    {"weekday": 3, "who": "Marcus", "role": "desk", "start_min": 480, "end_min": 990},
    {"weekday": 3, "who": "Priya", "role": "desk", "start_min": 600, "end_min": 1080},
    {"weekday": 3, "who": "Anita", "role": "nurse", "start_min": 510, "end_min": 1020},
    {"weekday": 3, "who": "Dr. Garg", "role": "doc", "start_min": 510, "end_min": 1050},
    # Fri
    {"weekday": 4, "who": "Priya", "role": "desk", "start_min": 480, "end_min": 990},
    {"weekday": 4, "who": "Marcus", "role": "desk", "start_min": 600, "end_min": 1080},
    {"weekday": 4, "who": "Anita", "role": "nurse", "start_min": 510, "end_min": 1020},
    {"weekday": 4, "who": "Jordan", "role": "nurse", "start_min": 510, "end_min": 960},
    {"weekday": 4, "who": "Dr. Garg", "role": "doc", "start_min": 510, "end_min": 1050},
    # Sat
    {"weekday": 5, "who": "Marcus", "role": "desk", "start_min": 510, "end_min": 960},
    {"weekday": 5, "who": "Jordan", "role": "nurse", "start_min": 510, "end_min": 960},
    {"weekday": 5, "who": "Dr. Garg", "role": "doc", "start_min": 540, "end_min": 840},
    # Sun: no shifts (empty in the prototype)
]

OVERNIGHT = [
    {
        "id": "o3", "who": "Pereira", "pet": "Oscar (beagle)", "channel": "Text", "at": "11:40 pm",
        "kind": "emergency", "status": "new", "pref": None,
        "summary": "Says Oscar ate a whole bar of dark chocolate. Pointed to Boundary Bay Veterinary Specialty Hospital.",
        "conv": [
            ["client", "Oscar just ate a whole bar of dark chocolate"],
            ["bot", "That sounds like it needs a vet tonight. Boundary Bay Veterinary Specialty Hospital in Surrey is on the clinic emergency list as open 24 hours. I am texting you their address and phone number now. The clinic will check in with you this morning."],
        ],
    },
    {
        "id": "o5", "who": "Ali", "pet": "Rocky (Boxer)", "channel": "Text", "at": "5:30 am",
        "kind": "emergency", "status": "new", "pref": None,
        "summary": "Says Rocky has not eaten since his dental and is very sleepy. Pointed to the emergency list.",
        "conv": [
            ["client", "Rocky has not eaten since his dental yesterday and he is very sleepy"],
            ["bot", "I cannot tell you what is wrong, but a pet that is not eating and is very sleepy after surgery should be seen by a vet. Boundary Bay Veterinary Specialty Hospital in Surrey is on the clinic emergency list as open 24 hours. The clinic will check in with you this morning."],
        ],
    },
    {
        "id": "o1", "who": "Tran", "pet": "Willow (dog)", "channel": "Text", "at": "9:12 pm",
        "kind": "booking", "status": "new", "pref": "Thu Oct 29, 9:30 am",
        "summary": "Wants vaccines. Asked for Thursday morning.",
        "conv": [
            ["client", "Hi, can I book Willow in for her vaccines?"],
            ["bot", "Of course. Dr. Garg has Thu Oct 29 at 9:30 am or Fri Oct 30 at 2:00 pm. Which works?"],
            ["client", "Thursday morning please"],
            ["bot", "Done. I have asked for Thu Oct 29, 9:30 am. The front desk will confirm by text after 8 am."],
        ],
    },
    {
        "id": "o2", "who": "Okafor", "pet": "Teddy (dog)", "channel": "Call", "at": "10:47 pm",
        "kind": "booking", "status": "new", "pref": "Mon Nov 2, 3:30 pm",
        "summary": "Wants a yearly check-up. Free after 3 pm next week.",
        "conv": [
            ["bot", "Hi, this is the automated assistant for Walnut Grove Animal Hospital. The clinic is closed until 8 am. How can I help?"],
            ["client", "I want to book Teddy for a check-up, sometime after three next week"],
            ["bot", "Dr. Garg has Mon Nov 2 at 3:30 pm. Shall I ask for that?"],
            ["client", "Yes please"],
            ["bot", "Done. The front desk will text you to confirm after 8 am."],
        ],
    },
    {
        "id": "o4", "who": "Fraser", "pet": "Luna (Husky)", "channel": "Call", "at": "6:55 am",
        "kind": "callback", "status": "new", "pref": None,
        "summary": "Needs a refill. Added to callbacks.",
        "conv": [
            ["bot", "Hi, this is the automated assistant for Walnut Grove Animal Hospital. The clinic opens at 8 am. How can I help?"],
            ["client", "I need to refill Luna's medicine"],
            ["bot", "Thanks. I have passed that to the front desk and they will call you after 8 am. A vet has to approve any medicine refill."],
        ],
    },
]

VISIT_TYPES = [
    {"name": "Yearly check-up", "minutes": 30, "bookable": True},
    {"name": "Vaccines", "minutes": 20, "bookable": True},
    {"name": "Sick visit", "minutes": 30, "bookable": True},
    {"name": "Dental consult", "minutes": 30, "bookable": False},
    {"name": "Surgery drop-off", "minutes": 15, "bookable": False},
]

HOSPITALS = [
    {
        "name": "Boundary Bay Veterinary Specialty Hospital",
        "place": "Surrey",
        "hours_claim": "Says it is open 24 hours a day, 7 days a week",
        "confirmed": False,
        "is_placeholder": False,
    },
    {
        "name": "Backup hospital",
        "place": "",
        "hours_claim": "",
        "confirmed": False,
        "is_placeholder": True,
    },
]

EVENT_LOG = [
    {"t": "9:38 am", "tool": "Call logger", "text": "Missed call from Walker, no voicemail"},
    {"t": "9:31 am", "tool": "Call logger", "text": "Voicemail from Dhillon tagged: results question, high priority"},
    {"t": "9:12 am", "tool": "Recall sender", "text": "Draft ready for Biscuit annual exam recall"},
    {"t": "8:47 am", "tool": "Results tracker", "text": "Lab result received for Maple (blood panel)"},
    {"t": "8:02 am", "tool": "Call logger", "text": "Courier call, pickup confirmed"},
    {"t": "8:00 am", "tool": "After-hours assistant", "text": "Sorted 5 overnight conversations: 2 appointment requests, 1 callback, 2 sent to an emergency hospital"},
    {"t": "6:00 am", "tool": "ImproMed sync", "text": "Imported 64 appointments for today (read only)"},
]

TOOLS = [
    {"n": "Call logger", "who": "Front desk, Dr. Garg", "w": "Call, time, type, outcome"},
    {"n": "Callback list", "who": "Front desk, nurses", "w": "Callback, owner, status"},
    {"n": "Results tracker", "who": "Nurses, Dr. Garg", "w": "Result, stage, days waiting"},
    {"n": "Recall sender", "who": "Front desk", "w": "Message, approved by, sent"},
    {"n": "After-hours assistant", "who": "Clients at night, front desk in the morning", "w": "Conversation, request, outcome"},
    {"n": "Staff roster", "who": "Everyone", "w": "Shift, role, phone cover"},
    {"n": "ImproMed sync", "who": "Read only, runs by itself", "w": "Appointments, clients"},
]

CHIPS = {
    "owner": [
        ["night", "What came in overnight?"],
        ["first", "What should I look at first today?"],
        ["missed", "How many calls did we miss?"],
        ["recall", "Who is overdue for a recall?"],
        ["saturday", "Is the desk covered on Saturday?"],
        ["week", "How does this week compare to last?"],
    ],
    "desk": [
        ["night", "What came in overnight?"],
        ["urgent", "What is most urgent in my queue?"],
        ["records", "How do I handle a records request?"],
        ["hours", "What are our Saturday hours?"],
        ["pickup", "Draft a pickup message for Pepper"],
    ],
    "nurse": [
        ["emerg", "Any emergencies overnight?"],
        ["waiting", "Which results are waiting longest?"],
        ["script", "Draft a results call for Simba"],
        ["flags", "Any callbacks that sound urgent?"],
        ["records", "How do I handle a records request?"],
    ],
}

GREETINGS = {
    "owner": "Morning, {name}. I have read everything the desk and nurses logged since yesterday. Ask me something, or tap a question below.",
    "desk": "Morning, {name}. I can show what is most urgent in your queue, draft messages for you to approve, and answer quick clinic questions.",
    "nurse": "Morning, {name}. I can show which results are waiting, draft call scripts and point out callbacks that sound urgent.",
}

EMERGENCY_WORDS = [
    "trouble breathing",
    "bleeding that will not stop",
    "seizure",
    "collapse or very sleepy",
    "hit by a car",
    "ate chocolate, xylitol or rat poison",
    "not eating after surgery",
    "cannot pass urine",
    "swollen, hard belly",
]

WORDING = {
    "opening_line": "Hi, this is the automated assistant for Walnut Grove Animal Hospital. The clinic is closed until 8 am. I can take an appointment request or pass on a message.",
    "urgent_line": "That sounds like it needs a vet tonight. [Hospital name] is open now. I can text you their address and phone number.",
    "never_line": "It never diagnoses, never says a pet is fine, and always says it is an automated assistant.",
}

PEOPLE = [
    {"username": "garg", "display_name": "Dr. Garg", "title": "Veterinarian and owner", "initials": "G", "role": "owner", "is_admin": True},
    {"username": "priya", "display_name": "Priya", "title": "Front desk", "initials": "P", "role": "desk", "is_admin": False},
    {"username": "marcus", "display_name": "Marcus", "title": "Front desk", "initials": "M", "role": "desk", "is_admin": False},
    {"username": "anita", "display_name": "Anita", "title": "Registered vet tech", "initials": "A", "role": "nurse", "is_admin": False},
    {"username": "jordan", "display_name": "Jordan", "title": "Registered vet tech", "initials": "J", "role": "nurse", "is_admin": False},
    {"username": "krish", "display_name": "Krish", "title": "Admin", "initials": "K", "role": "owner", "is_admin": True},
]


# ---- wave 2 additions (example content, invented) ----
RULES_DEFAULT = {"load_limit": 12, "min_weekdays": 3, "missed_min": 20, "recall_min": 50, "recall_batch": 10, "results_age_days": 2}

HOURS_NOTE = "Example hours: closed after 6 pm on weekdays, and after 2 pm on Saturdays."

ANSWER_SHEET = {
    "example": True,
    "records": {
        "intro": "For a records request from another clinic:",
        "steps": [
            "Confirm the pet and the owner name with the requesting clinic.",
            "Check that the owner has agreed to the transfer.",
            "Send from the practice system and log it in the callback.",
        ],
        "footer": "This sheet is written by the clinic, and I only repeat it.",
    },
    "hours": {
        "paragraphs": [
            "Saturday: 9 am to 2 pm, with Dr. Garg and one receptionist. Phones go to voicemail after 2 pm.",
            "This is example content. In the real version the clinic writes its own answer sheet.",
        ],
    },
}
