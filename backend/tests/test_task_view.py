"""Tasks at a glance: generated titles and notes become a label, who, number, when and one clean sentence."""
from app.services.task_view import glance


def test_missed_call_from_unknown_number():
    g = glance("Call back (604) 555-0100", "(604) 555-0100 called and nobody answered at Thu Oct 30, 4:10 PM. Number: (604) 555-0100.\n"
               "(604) 555-0100 left a voicemail at Thu Oct 30, 5:02 PM. Number: (604) 555-0100.", "callback", "Call back", None)
    assert g["label"] == "Voicemail" and g["who"] == "Unknown caller" and g["phone"] == "(604) 555-0100" and g["channel"] == "missed"
    assert g["when"] == "Thu Oct 30, 5:02 PM" and g["attempts"] == 2 and g["summary"] == "Left a voicemail (2 tries)." and g["tone"] == "warn"


def test_transcript_task_splits_summary_and_next_step():
    g = glance("Urgent, check-in on a pet: Caller (604) 555-0101",
               "Call Sat Oct 31, 9:12 AM: Caller asked about a limping dog. Next step: Call back before noon.\nNumber: (604) 555-0101.", "callback", "Call back", "Caller (604) 555-0101")
    assert g["label"] == "Check-in on a pet" and g["urgent"] and g["tone"] == "crit" and g["who"] == "Unknown caller" and g["phone"] == "(604) 555-0101"
    assert g["summary"] == "Caller asked about a limping dog." and g["next_step"] == "Call back before noon." and g["when"] == "Sat Oct 31, 9:12 AM"


def test_named_contact_wins_and_topics_map_to_tones():
    g = glance("Pickup: Ryan", "Call Thu Oct 30, 2:00 PM: Ryan will pick up the medication.\nNumber: (604) 555-0102.", "callback", "Call back", "Ryan Lee")
    assert g["label"] == "Pickup" and g["who"] == "Ryan Lee" and g["tone"] == "ok"
    v = glance("Call back (604) 555-0103: vendor or business", "Call Thu Oct 30, 1:00 PM: A supplier called.", "callback", "Call back", None)
    assert v["label"] == "Vendor or business" and v["tone"] == "mute"


def test_bookings_keep_the_appointment_time():
    g = glance("Confirm phone booking: Oscar, Fri Nov 6, 9:10 am",
               "A time was agreed on a call (Oscar booked a check-up for Jimmy). It is in the Calendar waiting for approval: check it.", "booking", "Booking", "Oscar")
    assert g["label"] == "Confirm booking" and g["appointment"] == "Fri Nov 6, 9:10 am" and g["summary"] == "Oscar booked a check-up for Jimmy" and g["tone"] == "info"
    t = glance("Confirm text booking: Krish, Thu Nov 5, 10 am", "Check-up asked for by text at Thu Nov 5, 10 am. Read the conversation, then approve it.", "booking", "Booking", "Krish")
    assert t["channel"] == "text" and t["summary"] == "Check-up asked for by text at Thu Nov 5, 10 am."


def test_free_form_titles_fall_back_to_the_kind():
    g = glance("Ask Dr. Garg about the new fee", "Someone asked about dental pricing.", "unknown_knowledge", "Needs an answer", None)
    assert g["label"] == "Needs an answer" and g["summary"] == "Someone asked about dental pricing." and g["who"] == ""


def test_requests_list_and_task_phone(env, garg, anita):
    r = garg.get("/api/calendar/requests").json()
    assert r["can_decide"] is True and all(e["status"] == "requested" for e in r["items"])
    t = garg.get("/api/tasks?status=active").json()["items"]
    assert all("phone" in x for x in t)


def test_requests_list_and_task_phone(env, garg):
    r = garg.get("/api/calendar/requests").json()
    assert r["can_decide"] is True and all(e["status"] == "requested" for e in r["items"])
    assert all("phone" in x for x in garg.get("/api/tasks?status=active").json()["items"])
