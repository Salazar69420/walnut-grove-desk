"""Synthetic fixture generator. Everything here is invented; no real data is used or read.

make_fixture(path, seed) writes a CSV with the same columns/encoding (UTF-8 with BOM) as the real export
and returns a dict with ground truth per call id and the planted secrets used by the tests.
"""
from __future__ import annotations

import csv
import random
from datetime import datetime, timedelta

PRIVATE_MARKER = "privatemarkerxyzzy"   # unique token placed in one private-call transcript
FIRST_MONDAY = datetime(2026, 8, 3)       # a Monday
COLUMNS = ["file", "date", "time", "number", "direction", "audio_seconds", "cost_usd", "language",
           "transcript", "plain_text"]

PETS = [("dog", "Max", "he", "him", "his"), ("cat", "Luna", "she", "her", "her"),
        ("dog", "Bella", "she", "her", "her"), ("cat", "Milo", "he", "him", "his"),
        ("dog", "Rocky", "he", "him", "his"), ("dog", "Daisy", "she", "her", "her")]
STAFF = ["Amy", "Sarah", "Jen", "Priya"]
DAYS = ["Thursday", "Friday", "Monday", "Wednesday"]


def _pet(r):
    return r.choice(PETS)


def scenarios(r: random.Random):
    """Yield dicts: key, caller_type, direction, lang, secs, utts, slot=(day_offset, hour) or None, extra."""
    out = []

    def add(key, caller_type, direction, utts, secs=None, lang="eng", slot=None, **extra):
        out.append(dict(key=key, caller_type=caller_type, direction=direction, utts=utts,
                        secs=secs if secs is not None else r.randint(35, 180), lang=lang, slot=slot, **extra))

    # --- client bookings (6 land in the same weekday-hour cell: Tuesday 10:xx) ---------------------
    for i in range(8):
        sp, pet, he, him, his = _pet(r)
        greet = ("Thank you for calling Walnut Grove Animal Hospital, this is {s}, how can I help you?"
                 if i % 3 else "Thanks for calling wall nut grove animal hospital, how may I help you?")
        add("booking", "client", "inbound", [
            greet.format(s=r.choice(STAFF)),
            f"Hi, I'd like to book an appointment for my {sp} {pet}, {he} needs {his} yearly vaccines.",
            "Sure, do you have a day in mind?", f"{r.choice(DAYS)} morning would be great.",
            "We have ten thirty open.", "Perfect, see you then.", "Thank you so much, have a good day."],
            slot=[(1, 10), (1, 10), (1, 10), (8, 10), (8, 10), (8, 10), None, None][i], intent="booking")
    for i in range(2):
        sp, pet, he, him, his = _pet(r)
        add("reschedule", "client", "inbound", [
            "Walnut Grove Animal Hospital, how can I help you?",
            f"I need to cancel and reschedule my {sp}'s appointment, I can't make it on Friday.",
            "No problem, I can move it to next Tuesday.", "That works, thank you."], intent="reschedule_or_cancel")
    for i in range(2):
        sp, pet, he, him, his = _pet(r)
        add("refill", "client", "inbound", [
            "Thank you for calling Walnut Grove Animal Hospital, how can I help?",
            f"Hi, I need a refill on my {sp}'s prescription, {he} is running out of the pain medication.",
            "Okay, I'll have the doctor approve it and call you back this afternoon.", "Great, thank you."],
            intent="refill", outcome="callback_promised")
    for i in range(2):
        add("food", "client", "inbound", [
            "Walnut Grove Animal Hospital, how can I help you?",
            "I want to order more of the prescription diet kidney food for my cat. Do you have it in stock?",
            "Yes, we have two bags, I'll set them aside.", "Thanks."], intent="food_or_supply_order")
    sp, pet, he, him, his = PETS[2]
    add("pickup_answered", "client", "outbound", [
        f"Hi, this is Sarah calling from Walnut Grove Animal Hospital. {pet} is ready for pickup, you can come get {him} anytime before six.",
        "Great, I'll be there in twenty minutes.", "Wonderful, see you then."], intent="pickup_ready")
    add("pickup_vm", "client", "outbound", [
        "Hello, this is Sarah calling from Walnut Grove Animal Hospital. Bella is ready for pickup. Please call us back if you have any questions."],
        secs=25, intent="pickup_ready", outcome="voicemail_left")
    add("results_out", "client", "outbound", [
        "Hi, it's Dr Lee from Walnut Grove Animal Hospital calling about Rocky's blood work. The results came back and everything looks good.",
        "Oh that is great news, thank you so much."], intent="results_call_out")
    add("results_in", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "I'm calling to check on the lab results for my dog Rocky, he had blood work done on Monday.",
        "The doctor reviewed them and everything is normal.", "Oh good, thank you."], intent="results_question")
    for i in range(2):
        add("recall_vm", "client", "outbound", [
            "Hello, this is Amy calling from Walnut Grove Animal Hospital with a reminder that Milo is due for his annual vaccination. Please call us back to book an appointment. Thank you."],
            secs=30, intent="recall_or_reminder", outcome="voicemail_left")
    add("check_in", "client", "outbound", [
        "Hi, this is Jen calling from Walnut Grove Animal Hospital to check in on Daisy after the surgery, how is she doing?",
        "She's doing great, eating well and walking around.", "That's wonderful, call us if anything changes."],
        intent="check_in")
    add("records", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "I'm moving to Calgary and need a copy of my dog's vaccination records, can you email them?",
        "Sure, I'll send over the records today.", "Thank you."], intent="records_request")
    add("pricing", "client", "inbound", [
        "Thank you for calling Walnut Grove Animal Hospital, how can I help you?",
        "How much does it cost to get my cat spayed? And do you take walk-ins?",
        "A spay is around four hundred dollars, and we do not take walk-ins for surgery."],
        intent="pricing_or_policy_question")
    add("billing", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "I got an invoice with a charge I don't understand, and I want to know if my insurance claim went through for my dog's surgery.",
        "Let me look at your account."], intent="billing_or_insurance")
    add("complaint", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "I'm not happy, I waited over an hour with my dog and the service was terrible, I want to speak to the manager."],
        intent="complaint")
    add("triage_soon", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "My dog has been vomiting since last night and he's not eating, can I bring him in today?",
        "Yes, we can see him at two o'clock."], intent="triage_symptom", urgency="soon")

    # --- emergencies ---------------------------------------------------------------------------------------
    g = "Walnut Grove Animal Hospital, how can I help you? "
    add("emergency_chocolate", "client", "inbound", [
        g + "My dog just ate a whole box of chocolate about an hour ago, what should I do?",
        "Please bring him in right now, how much does he weigh?", "About twenty pounds, we're coming."],
        urgency="emergency_signs", flags=["toxin_chocolate"], intent="triage_symptom")
    add("emergency_breathing", "client", "inbound", [
        g + "My cat is having trouble breathing and her gums look pale, please help.",
        "Come in right away."], urgency="emergency_signs", flags=["trouble_breathing", "pale_gums"],
        intent="triage_symptom", slot=(5, 11))
    add("emergency_car", "client", "inbound", [
        g + "My dog got hit by a car, he's bleeding badly and can't stand up.",
        "Bring him in immediately, we'll be ready."], urgency="emergency_signs",
        flags=["hit_by_car", "bleeding", "collapse"], intent="triage_symptom", slot=(12, 19))
    add("emergency_xylitol", "client", "inbound", [
        g + "My puppy ate a pack of sugar free gum, I think it has zylitol in it.",
        "How long ago?", "Maybe twenty minutes."], urgency="emergency_signs", flags=["toxin_xylitol"],
        intent="triage_symptom")
    add("emergency_seizure", "client", "inbound", [
        g + "He's having a siezure right now on the kitchen floor, what do I do?",
        "Keep him away from stairs and come in now."], urgency="emergency_signs", flags=["seizure"],
        intent="triage_symptom", slot=(6, 11))

    # --- voicemails to the clinic ---------------------------------------------------------------------------
    add("vm_inbound_1", "client", "inbound", [
        "You have reached Walnut Grove Animal Hospital. We are unable to take your call right now. Please leave a message after the tone.",
        "Hi, this is Pat, my dog Buddy has been limping since yesterday. My number is 604 555 0188. Please call me back."],
        secs=40, outcome="voicemail_left", slot=(6, 15), phone_in_text="604 555 0188")
    add("vm_inbound_2", "client", "inbound", [
        "You have reached Walnut Grove Animal Hospital. Please leave a message after the tone.",
        "Hi it's Maria, I'd like to book an appointment for my cat Luna next week. Please call me back."],
        secs=35, outcome="voicemail_left", slot=(12, 9))

    # --- other clinics, labs, vendors, staff ----------------------------------------------------------------
    for i in range(3):
        add("other_clinic", "other_clinic", "inbound" if i < 2 else "unknown", [
            *(["Walnut Grove Animal Hospital, how can I help you?"] if i < 2 else []),
            "Hi, this is Karen from Maple Ridge Veterinary Clinic. We have a referral patient, can you send over the records for the dog Rex?",
            "Sure, I'll fax them over today."], intent="records_request")
    add("courier_antech", "lab_or_courier", "unknown", [
        "Hi, it's the courier from Antech Diagnostics, I'm here for the sample pickup at the back door and nobody is answering."],
        secs=45)
    add("courier_purolator", "lab_or_courier", "unknown", [
        "Purolator delivery for the clinic, I need someone to come and sign for the package at the front."], secs=40)
    add("courier_idexx", "lab_or_courier", "unknown", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "IDEXX driver here, are the samples ready for the pickup today?", "Yes, they are at the back door."],
        secs=50)
    add("vendor_sales_1", "vendor_or_sales", "unknown", [
        "Hi, I'm a sales representative with Pacific Vet Supply, we have a special offer on our products this month, could I speak to the practice manager?",
        "Not interested, thanks."], secs=60)
    add("vendor_sales_2", "vendor_or_sales", "unknown", [
        "Hello, I'm calling about your credit card processing, we can lower your merchant fees, who is the decision maker there?",
        "We are not interested."], secs=45)
    add("vendor_support", "vendor_or_sales", "unknown", [
        "Hi this is Tom from your practice management software company, I'm following up on your support ticket, your login should work now.",
        "Great, I'll try it."], secs=70)
    add("staff_1", "staff_internal", "internal", [
        "Hey, it's Jen from the back. Can you send the next client to room two?",
        "Sure, the doctor needs you at the front desk too."], secs=30, number=None)
    add("staff_2", "staff_internal", "internal", [
        "Can you cover my lunch break? I'll be in room three with the surgery list.",
        "Yes, no problem, go ahead."], secs=28, number=None)
    add("staff_3", "staff_internal", "internal", [
        "I'm running late, I'll be in by nine, can you cover the front desk until I get there?",
        "Sure, see you soon."], secs=25)

    # --- private ---------------------------------------------------------------------------------------------
    add("private_1", "private_personal", "unknown", [
        "Hey babe, it's me. Are you picking up the kids after school today? Love you.",
        f"Also what's for dinner tonight? Did you book the {PRIVATE_MARKER} cabin for the long weekend?",
        "I think so, I'll check tonight. Love you too."], secs=75, private=True, marker=PRIVATE_MARKER)
    add("private_2", "private_personal", "unknown", [
        "Hi mom, yeah I'm still at work. Happy birthday to dad, we'll do dinner on Saturday.",
        "The kids are so excited. Love you, bye."], secs=90, private=True)
    add("private_3", "private_personal", "unknown", [
        "Did you watch the hockey game last night? My husband wants to go to the game tonight.",
        "We also need to pay the rent and the mortgage this week, the bank called again.",
        "Ugh, okay, talk later."], secs=100, private=True)

    # --- dead air / automated --------------------------------------------------------------------------------
    add("dead_empty", "automated_or_dead_air", "unknown", [""], secs=4, number=None)
    add("dead_hello", "automated_or_dead_air", "unknown", ["Hello? Hello? Hello?"], secs=6)
    add("dead_thanks", "automated_or_dead_air", "unknown", ["Thank you."], secs=3)
    add("ivr", "automated_or_dead_air", "unknown", [
        "This is an automated message. Press 1 for sales, press 2 for support. Your call may be recorded."],
        secs=20, number=None)

    # --- outcomes ----------------------------------------------------------------------------------------------
    add("hold_dropped", "client", "inbound", [
        "Thank you for calling Walnut Grove Animal Hospital, how can I help you?",
        "Hi, I have a question about my dog's medication.", "Sure, can you hold for a moment please?"],
        secs=50, outcome="hold_then_dropped")
    add("transferred", "client", "inbound", [
        "Walnut Grove Animal Hospital, how can I help you?",
        "I have a question about my cat's blood work results.",
        "Let me transfer you to the technician, one moment."], secs=55, outcome="transferred")

    # --- languages ---------------------------------------------------------------------------------------------
    add("hindi_1", "client", "unknown", [
        "नमस्ते, मुझे अपने कुत्ते के लिए अपॉइंटमेंट चाहिए। डॉक्टर से बात करनी है। कल सुबह ठीक रहेगा। धन्यवाद।"],
        lang="hin", secs=60)
    add("hindi_2", "client", "unknown", [
        "Namaste, mera kutta bimar hai, doctor sahab se baat karni hai, appointment chahiye, kal subah aa sakte hain."],
        lang="hin", secs=55)
    add("punjabi", "client", "unknown", [
        "ਸਤ ਸ੍ਰੀ ਅਕਾਲ, ਮੇਰੇ ਕੁੱਤੇ ਲਈ ਅਪੌਇੰਟਮੈਂਟ ਚਾਹੀਦੀ ਹੈ, ਡਾਕਟਰ ਨਾਲ ਗੱਲ ਕਰਨੀ ਹੈ ਜੀ।"], lang="pan", secs=58)
    add("french", "client", "unknown", [
        "Bonjour, je voudrais prendre un rendez-vous pour mon chien, il a besoin de ses vaccins. Merci beaucoup."],
        lang="fra", secs=48)

    # --- unknown -------------------------------------------------------------------------------------------------
    add("wrong_number", "unknown", "unknown", [
        "Hello, is this the vet? Oh sorry, wrong number, I was looking for a different place. Sorry about that."],
        secs=12)
    add("vague", "unknown", "unknown", [
        "Hi, uh, I was calling about the thing from before, never mind, I'll try again later, thanks a lot."],
        secs=15)
    return out


def make_fixture(path: str, seed: int = 7) -> dict:
    r = random.Random(seed)
    items = scenarios(r)
    rows, truth = [], {}
    seq = 0
    number_pool = [f"604555{i:04d}" for i in range(100, 100 + len(items))]
    for idx, it in enumerate(items):
        slot = it["slot"]
        if slot is None:
            day = r.choice([0, 2, 3, 4, 7, 9, 10, 11])
            hour = r.choice([8, 9, 11, 12, 13, 14, 15, 16, 17])
        else:
            day, hour = slot
        dt = FIRST_MONDAY + timedelta(days=day, hours=hour, minutes=r.randint(0, 59), seconds=r.randint(0, 59))
        ext = r.choice(["101", "102", "103"])
        number = it.get("number", number_pool[idx]) if "number" in it else number_pool[idx]
        seq += 1
        base = f"{dt:%Y%m%d-%H%M%S}_{ext}_{number if number else 'null'}_{r.choice(['Outgoing', 'Incoming'])}_Auto_{8800000 + seq}"
        utts = it["utts"]
        plain = " ".join(u for u in utts if u)
        transcript = "\n".join(f"Speaker {1 + (i % 2)}: {u}" for i, u in enumerate(utts) if u)
        rows.append({"file": base + ".mp3", "date": f"{dt:%Y-%m-%d}", "time": f"{dt:%H:%M:%S}",
                     "number": number or "", "direction": r.choice(["Outgoing", "Incoming", "Auto"]),
                     "audio_seconds": it["secs"], "cost_usd": round(it["secs"] * 0.0001, 4),
                     "language": it["lang"], "transcript": transcript, "plain_text": plain})
        truth[base] = {k: v for k, v in it.items() if k not in ("utts",)}
        truth[base].update(number=number, plain=plain, dt=dt.isoformat(), ext=ext)
    dup = dict(rows[3])
    rows.append(dup)  # exact duplicate row to exercise de-duplication
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return {"truth": truth, "rows": len(rows), "unique": len(rows) - 1, "duplicates": 1,
            "marker": PRIVATE_MARKER}


if __name__ == "__main__":
    import sys
    info = make_fixture(sys.argv[1])
    print(f"wrote {info['rows']} rows")
