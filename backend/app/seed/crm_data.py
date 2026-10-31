"""Invented Clinic CRM demo content (docs/CONTRACT_CRM_P1.md).

Everything here is made up: names, pets, phones (604 or 778, 555-01xx), emails (@example.com or
@example.org) and messages. Nothing is medical advice. Canadian spelling.

Time markers:  A(days, hours, minutes) is "that long ago";  AT("9:31 am") is a clock time today,
AT("11:40 pm", y=True) is the evening before.
"""
from __future__ import annotations


def A(d: int = 0, h: int = 0, m: int = 0) -> tuple:
    return ("ago", d, h, m)


def AT(clock: str, y: bool = False) -> tuple:
    return ("at", clock, y)


# kind, name, phone number tail (the 555-01xx part), email, email_consent, sms_consent, source, owner, ai, created days ago,
# preference notes, consent source, unread
CONTACTS = [
    # ---------------------------------------------------------------- patients (18)
    dict(name="Grace Chen", kind="patient", ph="0101", area="604", email="grace.chen@example.com", ec="full", sc="full", source="phone",
         owner="Priya", ai=True, created=700, prefs="Prefers a call before 4 pm. Biscuit gets nervous in the waiting room, so a quiet room helps.",
         cs="Said yes to texts and email at the front desk", unread=True,
         pets=[dict(name="Biscuit", species="dog", breed="Labrador", sex="female", born=(7, 2), kg=29.5, notes="Friendly. Likes the treat jar.",
                    visits=[(620, "vaccine", "Booster vaccines."), (365, "checkup", "Annual exam. Weight recorded."),
                            (150, "sick", "Limping on a back leg. Rechecked in two weeks."), (21, "checkup", "Annual exam and vaccines updated.")])]),
    dict(name="Daniel Walker", kind="patient", ph="0102", area="604", email="daniel.walker@example.com", ec="full", sc="transactional", source="walk_in",
         owner="Marcus", ai=True, created=540, prefs="Texts only for pickup and reminders. Pepper's food order comes in monthly.",
         cs="Signed the consent form at the desk", unread=True,
         pets=[dict(name="Pepper", species="dog", breed="Border Collie mix", sex="male", born=(4, 5), kg=18.2, notes="Very energetic.",
                    visits=[(480, "vaccine", "Puppy booster."), (330, "checkup", "Annual exam."), (45, "sick", "Upset stomach. Sent home with a diet sheet.")])]),
    dict(name="Harpreet Dhillon", kind="patient", ph="0103", area="604", email="harpreet.dhillon@example.com", ec="full", sc="full", source="referral",
         owner="Priya", ai=True, created=900, prefs="Wants results by phone, not text.", cs="Agreed on the phone, noted by Priya", unread=True,
         pets=[dict(name="Simba", species="cat", breed="Domestic shorthair", sex="male", born=(12, 0), kg=4.6, notes="Senior cat. Takes pills in a treat.",
                    visits=[(700, "checkup", "Senior wellness exam."), (400, "vaccine", "Vaccines updated."), (240, "sick", "Weight check."),
                            (9, "checkup", "Senior exam. Blood and thyroid tests sent to the lab.")])]),
    dict(name="Linh Nguyen", kind="patient", ph="0104", area="778", email="linh.nguyen@example.org", ec="full", sc="full", source="website",
         owner="Priya", ai=True, created=300, prefs="Mochi is her first rabbit. Appreciates plain explanations.", cs="Ticked the box on the website form", unread=True,
         pets=[dict(name="Mochi", species="rabbit", breed="Holland Lop", sex="female", born=(2, 3), kg=1.8, notes="Needs a quiet exam room.",
                    visits=[(280, "checkup", "First visit and nail trim."), (110, "checkup", "Wellness check.")])]),
    dict(name="Minh Tran", kind="patient", ph="0105", area="604", email="minh.tran@example.com", ec="transactional", sc="transactional", source="phone",
         owner="Marcus", ai=True, created=420, prefs="Reminders only, no promotional messages.", cs="Asked for reminders only, noted at the desk", unread=True,
         pets=[dict(name="Willow", species="dog", breed="Golden Retriever", sex="female", born=(3, 8), kg=27.0, notes="",
                    visits=[(380, "vaccine", "Puppy series finished."), (200, "checkup", "Annual exam."), (60, "other", "Nail trim.")])]),
    dict(name="Ngozi Okafor", kind="patient", ph="0106", area="604", email="ngozi.okafor@example.org", ec="full", sc="full", source="phone",
         owner=None, ai=True, created=610, prefs="Free after 3 pm on weekdays.", cs="Agreed on the phone", unread=True,
         pets=[dict(name="Teddy", species="dog", breed="Poodle mix", sex="male", born=(5, 0), kg=9.4, notes="",
                    visits=[(560, "checkup", "Annual exam."), (330, "vaccine", "Vaccines updated."), (330 - 14, "other", "Weight check.")])]),
    dict(name="Colin Fraser", kind="patient", ph="0107", area="604", email="colin.fraser@example.com", ec="full", sc="full", source="phone",
         owner="Marcus", ai=False, created=800, prefs="Likes to speak to a person. Please do not use the automated assistant.",
         cs="Agreed at the desk", unread=True,
         pets=[dict(name="Luna", species="dog", breed="Siberian Husky", sex="female", born=(6, 4), kg=23.0, notes="Medicine refills are approved by Dr. Garg.",
                    visits=[(500, "checkup", "Annual exam."), (260, "sick", "Itchy skin. Cytology sent to the lab."), (14, "other", "Medication recheck.")])]),
    dict(name="Imran Ali", kind="patient", ph="0108", area="778", email="imran.ali@example.com", ec="full", sc="full", source="walk_in",
         owner="Priya", ai=True, created=250, prefs="", cs="Signed at the desk", unread=True,
         pets=[dict(name="Rocky", species="dog", breed="Boxer", sex="male", born=(5, 6), kg=31.5, notes="Had a dental yesterday.",
                    visits=[(300, "checkup", "Annual exam. Dental recommended."), (1, "dental", "Dental cleaning. Went home the same afternoon.")])]),
    dict(name="Sofia Pereira", kind="patient", ph="0109", area="604", email="sofia.pereira@example.org", ec="full", sc="none", source="website",
         owner=None, ai=True, created=380, prefs="Email is best. Has not agreed to texts yet.", cs="Website form, email only", unread=True,
         pets=[dict(name="Oscar", species="dog", breed="Beagle", sex="male", born=(4, 1), kg=12.1, notes="Will eat anything.",
                    visits=[(420, "vaccine", "Vaccines updated."), (190, "checkup", "Annual exam."), (40, "other", "Weight check.")])]),
    dict(name="Arjun Singh", kind="patient", ph="0110", area="604", email="arjun.singh@example.com", ec="full", sc="full", source="phone",
         owner="Marcus", ai=False, created=950, prefs="Calls are better than texts. Maple takes a while to settle.", cs="Agreed at the desk", unread=False,
         pets=[dict(name="Maple", species="dog", breed="Cocker Spaniel", sex="female", born=(9, 3), kg=13.8, notes="",
                    visits=[(720, "checkup", "Senior wellness exam."), (390, "vaccine", "Vaccines updated."), (180, "dental", "Dental cleaning."),
                            (3, "checkup", "Wellness exam. Blood panel sent to the lab.")])]),
    dict(name="Hannah Kowalski", kind="patient", ph="0111", area="778", email="hannah.kowalski@example.org", ec="full", sc="full", source="referral",
         owner="Priya", ai=True, created=200, prefs="Two cats, usually booked together.", cs="Agreed on the phone", unread=False,
         pets=[dict(name="Nori", species="cat", breed="Domestic longhair", sex="female", born=(3, 0), kg=4.1, notes="",
                    visits=[(190, "vaccine", "Vaccines updated."), (30, "checkup", "Annual exam.")]),
               dict(name="Pixel", species="cat", breed="Domestic shorthair", sex="male", born=(3, 0), kg=5.2, notes="",
                    visits=[(190, "vaccine", "Vaccines updated."), (30, "checkup", "Annual exam.")])]),
    dict(name="Tomas Lindqvist", kind="patient", ph="0112", area="604", email="tomas.lindqvist@example.com", ec="transactional", sc="transactional", source="phone",
         owner="Marcus", ai=True, created=640, prefs="Reminders only.", cs="Asked for reminders only", unread=False,
         pets=[dict(name="Bruno", species="dog", breed="Bernese Mountain Dog", sex="male", born=(6, 0), kg=48.5, notes="Large dog, please book the big exam room.",
                    visits=[(520, "checkup", "Annual exam."), (160, "sick", "Lump noted on a recheck."), (20, "checkup", "Pre-surgery exam.")])]),
    dict(name="Aisha Rahman", kind="patient", ph="0113", area="778", email="aisha.rahman@example.com", ec="full", sc="full", source="walk_in",
         owner=None, ai=True, created=130, prefs="", cs="Signed at the desk", unread=False,
         pets=[dict(name="Mango", species="cat", breed="Bengal", sex="female", born=(1, 10), kg=3.9, notes="Climbs everything.",
                    visits=[(120, "vaccine", "Kitten vaccines finished."), (35, "other", "Nail trim.")])]),
    dict(name="Peter Gagnon", kind="patient", ph=None, area="604", email="peter.gagnon@example.org", ec="full", sc="none", source="website",
         owner="Priya", ai=True, created=870, prefs="Email only. No phone number on file.", cs="Website form", unread=False,
         pets=[dict(name="Hank", species="dog", breed="Dachshund", sex="male", born=(14, 0), kg=8.0, notes="Passed away last spring.", status="deceased",
                    visits=[(700, "checkup", "Senior exam."), (400, "sick", "Check-up for a cough.")]),
               dict(name="Pretzel", species="dog", breed="Dachshund mix", sex="male", born=(2, 6), kg=7.2, notes="",
                    visits=[(200, "vaccine", "Vaccines updated."), (80, "checkup", "Annual exam.")])]),
    dict(name="Rosa Delgado", kind="patient", ph="0115", area="604", email=None, ec="none", sc="full", source="phone",
         owner="Marcus", ai=True, created=460, prefs="Text only. No email address on file.", cs="Agreed on the phone", unread=False,
         pets=[dict(name="Churro", species="bird", breed="Cockatiel", sex="unknown", born=(4, 0), kg=0.09, notes="Wing trim every spring.",
                    visits=[(400, "checkup", "Wellness exam and wing trim."), (35, "checkup", "Wellness exam.")])]),
    dict(name="Beth Cooper", kind="patient", ph="0116", area="778", email="beth.cooper@example.com", ec="full", sc="full", source="referral",
         owner="Priya", ai=True, created=170, prefs="Two guinea pigs, booked together.", cs="Agreed at the desk", unread=False,
         pets=[dict(name="Clementine", species="guinea pig", breed="", sex="female", born=(2, 0), kg=0.95, notes="",
                    visits=[(100, "checkup", "Wellness exam.")]),
               dict(name="Hazel", species="guinea pig", breed="", sex="female", born=(2, 0), kg=1.0, notes="",
                    visits=[(100, "checkup", "Wellness exam.")])]),
    dict(name="Yusuf Demir", kind="patient", ph="0117", area="604", email="yusuf.demir@example.org", ec="unsubscribed", sc="transactional", source="phone",
         owner=None, ai=True, created=560, prefs="Unsubscribed from email. Appointment texts are fine.", cs="Unsubscribed by reply, recorded by Marcus", unread=False,
         pets=[dict(name="Pasha", species="cat", breed="Persian", sex="male", born=(8, 0), kg=5.5, notes="",
                    visits=[(300, "checkup", "Annual exam."), (75, "dental", "Dental cleaning.")])]),
    dict(name="Olivia Park", kind="patient", ph="0118", area="604", email="olivia.park@example.com", ec="full", sc="full", source="walk_in",
         owner="Marcus", ai=True, created=90, prefs="", cs="Signed at the desk", unread=False,
         pets=[dict(name="Latte", species="dog", breed="Pomeranian", sex="female", born=(7, 0), kg=3.6, notes="",
                    visits=[(85, "checkup", "First visit. Dental recommended."), (12, "other", "Weight check.")])]),
    # ---------------------------------------------------------------- inquiries (12)
    dict(name="Jamie Whitfield", kind="inquiry", ph="0119", area="604", email=None, ec="none", sc="full", source="phone",
         owner="Priya", ai=True, created=6, prefs="Does not have a pet. Is looking after a friend's dog this month.",
         cs="Agreed by text", unread=True, pets=[]),
    dict(name="Elena Walker", kind="inquiry", ph="0120", area="604", email="elena.walker@example.org", ec="full", sc="none", source="other",
         owner="Marcus", ai=True, created=25, prefs="Daniel's spouse. Picks up Pepper's food order.", cs="Added by Marcus with Daniel's okay", unread=False, pets=[]),
    dict(name="Margaret Ellis", kind="inquiry", ph="0121", area="778", email=None, ec="none", sc="transactional", source="phone",
         owner=None, ai=True, created=40, prefs="Walks Maple on weekdays. Allowed to pick up medicine.", cs="Added by Marcus", unread=False, pets=[]),
    dict(name="Nathan Boyce", kind="inquiry", ph="0122", area="604", email="nathan.boyce@example.com", ec="full", sc="full", source="website",
         owner="Priya", ai=True, created=4, prefs="New puppy coming home next week.", cs="Website form", unread=True,
         pets=[dict(name="Scout", species="dog", breed="Labradoodle", sex="male", born=(0, 2), kg=4.2, notes="", visits=[])]),
    dict(name="Priyanka Menon", kind="inquiry", ph="0123", area="778", email="priyanka.menon@example.org", ec="transactional", sc="transactional", source="referral",
         owner="Marcus", ai=True, created=8, prefs="Referred by Hannah Kowalski.", cs="Agreed on the phone", unread=True,
         pets=[dict(name="Koko", species="cat", breed="Domestic shorthair", sex="female", born=(0, 5), kg=2.7, notes="", visits=[])]),
    dict(name="Dwayne Tremblay", kind="inquiry", ph="0124", area="604", email="dwayne.tremblay@example.com", ec="full", sc="full", source="after_hours",
         owner=None, ai=True, created=2, prefs="", cs="Agreed to texts during the after-hours chat", unread=True,
         pets=[dict(name="Smudge", species="cat", breed="Domestic shorthair", sex="male", born=(6, 0), kg=5.9, notes="", visits=[])]),
    dict(name="Fatima Haddad", kind="inquiry", ph=None, area="604", email="fatima.haddad@example.org", ec="full", sc="none", source="website",
         owner="Priya", ai=True, created=5, prefs="Moving to the area in December.", cs="Website form", unread=False,
         pets=[dict(name="Zaytun", species="dog", breed="Saluki mix", sex="female", born=(3, 0), kg=19.0, notes="", visits=[])]),
    dict(name="Carl Jensen", kind="inquiry", ph="0126", area="604", email=None, ec="none", sc="none", source="walk_in",
         owner="Marcus", ai=True, created=1, prefs="Walked in with a found kitten. No consent recorded yet.", cs="", unread=False, pets=[]),
    dict(name="Mia Sutherland", kind="inquiry", ph="0127", area="778", email="mia.sutherland@example.com", ec="full", sc="full", source="phone",
         owner="Dr. Garg", ai=True, created=3, prefs="", cs="Agreed on the phone", unread=True, pets=[]),
    dict(name="Kwame Mensah", kind="inquiry", ph="0128", area="604", email="kwame.mensah@example.org", ec="full", sc="none", source="website",
         owner=None, ai=True, created=7, prefs="Asked whether the clinic sees rabbits.", cs="Website form", unread=False,
         pets=[dict(name="Biscuit Jr", species="rabbit", breed="Mini Rex", sex="unknown", born=(1, 0), kg=1.6, notes="", visits=[])]),
    dict(name="Tessa Lindgren", kind="inquiry", ph="0129", area="778", email="tessa.lindgren@example.com", ec="full", sc="full", source="other",
         owner="Priya", ai=True, created=10, prefs="", cs="Agreed by email", unread=False,
         pets=[dict(name="Juniper", species="dog", breed="Shepherd mix", sex="female", born=(1, 3), kg=22.0, notes="", visits=[])]),
    dict(name="Owen Patel", kind="inquiry", ph="0130", area="604", email="owen.patel@example.org", ec="none", sc="full", source="phone",
         owner="Marcus", ai=True, created=1, prefs="", cs="Agreed on the phone", unread=False,
         pets=[dict(name="Ziggy", species="cat", breed="Domestic shorthair", sex="male", born=(0, 8), kg=3.4, notes="", visits=[])]),
]

# Associated contacts: (contact, related contact, relationship, note). The row reads "related is the <relationship> of contact".
LINKS = [
    ("Grace Chen", "Jamie Whitfield", "friend", "Jamie is looking after Biscuit this month and called first."),
    ("Daniel Walker", "Elena Walker", "spouse", "Elena picks up Pepper's food order."),
    ("Ngozi Okafor", "Aisha Rahman", "neighbour", "Lives next door. Sometimes drops Teddy off."),
    ("Arjun Singh", "Margaret Ellis", "caregiver", "Walks Maple on weekdays."),
]

# Note history on preference notes: (contact, old, new, editor, days ago)
NOTE_HISTORY = [
    ("Grace Chen", "Prefers a call before 4 pm.", "Prefers a call before 4 pm. Biscuit gets nervous in the waiting room, so a quiet room helps.", "Priya", 12),
    ("Colin Fraser", "", "Likes to speak to a person. Please do not use the automated assistant.", "Marcus", 30),
    ("Arjun Singh", "Maple takes a while to settle.", "Calls are better than texts. Maple takes a while to settle.", "Marcus", 50),
]

# Conversations. Each item: kind, ...
#   ("cin", body, when)                       client text in
#   ("cein", subject, body, when)             client email in
#   ("sout", who, body, when)                 text out (who is a staff name, or "ai")
#   ("eout", who, subject, body, when)        email out
#   ("call", "in"|"out", who_or_none, summary, when)   call entry
#   ("note", who, body, when, [mentions])     internal note
TIMELINES: dict[str, list] = {
    "Grace Chen": [
        ("eout", "Priya", "Biscuit's annual exam", "Hi Grace, it is Priya at Walnut Grove Animal Hospital. Biscuit is due for her annual exam next month. Reply here or call us and we will find a time that suits you.", A(44, 3)),
        ("cein", "Re: Biscuit's annual exam", "Thanks Priya! Mornings work best for us. Could we do a Tuesday?", A(44, 1)),
        ("sout", "Priya", "Hi Grace, Tuesday at 9:30 am is open for Biscuit. Shall I book it for you?", A(43, 22)),
        ("cin", "Yes please, that works.", A(43, 20)),
        ("sout", "Priya", "Booked. We will see you and Biscuit on Tuesday at 9:30 am. Reply here if anything changes.", A(43, 19)),
        ("call", "in", None, "Voicemail from Grace asking for a carprofen refill before the weekend. Passed to Dr. Garg to approve.", AT("8:12 am")),
        ("sout", "ai", "Hi Grace, this is the automated assistant for Walnut Grove Animal Hospital. I have passed your refill request to the front desk. A vet has to approve any medicine refill, so someone will call you after 8 am.", A(0, 1, 40)),
        ("note", "Priya", "Grace prefers a call before 4 pm. The refill needs @Dr. Garg to approve before she picks it up.", A(0, 0, 50), ["Dr. Garg"]),
        ("cin", "Thanks, any news on the refill?", A(0, 0, 25)),
        ("sout", "Anita", "Hi Grace, Dr. Garg has approved it. You can pick it up at the front desk any time before 6 pm today.", A(0, 0, 10)),
    ],
    "Daniel Walker": [
        ("sout", "Marcus", "Hi Daniel, Pepper's food order is in. You can pick it up at the front desk, we are open until 6 pm.", A(33, 4)),
        ("cin", "Great, I'll be there Friday after work.", A(33, 2)),
        ("sout", "Marcus", "Hi Daniel, it is Marcus at Walnut Grove Animal Hospital. Pepper is due for his vaccines next month. Want me to find a time?", A(20, 5)),
        ("cin", "Can you do early mornings? Elena can bring him in.", A(20, 3)),
        ("note", "Marcus", "Elena (spouse) can bring Pepper. Added her as an associated contact. @Priya please keep early slots open.", A(1, 3), ["Priya"]),
        ("call", "in", None, "Missed call, no message. Food order arrived.", AT("9:38 am")),
    ],
    "Harpreet Dhillon": [
        ("call", "out", "Anita", "Called to say Simba's blood test was sent to the lab. Results usually take a few days.", A(8, 3)),
        ("sout", "Anita", "Hi Harpreet, just confirming we sent Simba's samples to the lab. We will call you with the results.", A(8, 2)),
        ("cin", "Thank you. Please call, I don't always see texts.", A(8, 1)),
        ("note", "Anita", "Harpreet wants results by phone only. @Marcus please do not text results.", A(8, 0, 40), ["Marcus"]),
        ("call", "in", None, "Voicemail asking about Simba's blood test results.", AT("9:31 am")),
    ],
    "Linh Nguyen": [
        ("eout", "Priya", "Welcome to Walnut Grove Animal Hospital", "Hi Linh, welcome! It was lovely to meet you and Mochi. If you have any questions about her care between visits, reply to this email or call us.", A(110, 2)),
        ("cein", "Re: Welcome", "Thank you so much! One question, what kind of hay do you stock?", A(109, 20)),
        ("eout", "Priya", "Re: Welcome", "We carry timothy hay in the front. Ask at the desk and we will show you the sizes.", A(109, 18)),
        ("call", "in", None, "Voicemail. Says Mochi has not eaten since last night and asks if she should come in. Flagged for a nurse.", AT("9:20 am")),
        ("note", "Anita", "Called Linh back and booked a same-day visit with Dr. Garg.", A(0, 0, 18), []),
    ],
    "Minh Tran": [
        ("sout", "Marcus", "Hi Minh, Willow's vaccines are due soon. Reply YES and we will call to book a time.", A(30, 6)),
        ("cin", "Yes please", A(30, 4)),
        ("call", "out", "Marcus", "Left a voicemail to book Willow's vaccines. No answer.", A(29, 22)),
        ("cin", "Hi, can I book Willow in for her vaccines?", AT("9:12 pm", y=True)),
        ("sout", "ai", "Of course. Dr. Garg has Thu at 9:30 am or Fri at 2:00 pm. Which works?", AT("9:13 pm", y=True)),
        ("cin", "Thursday morning please", AT("9:15 pm", y=True)),
        ("sout", "ai", "Done. I have asked for Thursday at 9:30 am. The front desk will confirm by text after 8 am.", AT("9:16 pm", y=True)),
    ],
    "Ngozi Okafor": [
        ("eout", "Priya", "Teddy's yearly check-up", "Hi Ngozi, Teddy is due for his yearly check-up. Let us know what times suit you and we will find a spot.", A(14, 4)),
        ("call", "in", None, "Automated assistant took a call. Wants a yearly check-up for Teddy, free after 3 pm next week.", AT("10:47 pm", y=True)),
        ("sout", "ai", "Hi Ngozi, this is the automated assistant for Walnut Grove Animal Hospital. I have asked for Monday at 3:30 pm for Teddy. The front desk will text you to confirm after 8 am.", AT("10:50 pm", y=True)),
    ],
    "Colin Fraser": [
        ("call", "in", None, "Called about Luna's skin. Booked a recheck with Dr. Garg.", A(30, 3)),
        ("note", "Marcus", "Colin does not want the automated assistant. Switched it off. Please call him, not text.", A(30, 2), []),
        ("sout", "Marcus", "Hi Colin, a reminder that Luna's recheck is tomorrow at 10 am. See you then.", A(15, 3)),
        ("cin", "Thanks, we'll be there.", A(15, 2)),
        ("call", "in", None, "Automated assistant took a call. Needs a refill for Luna. Passed to the front desk.", AT("6:55 am")),
    ],
    "Imran Ali": [
        ("sout", "Priya", "Hi Imran, Rocky's dental is booked for tomorrow. Drop off is between 8 and 9 am, and we will go over the pre-visit sheet with you.", A(2, 4)),
        ("cin", "Understood, thanks Priya.", A(2, 3)),
        ("sout", "Anita", "Hi Imran, Rocky's dental went well and he is ready to go home. You can pick him up any time after 3 pm.", A(1, 6)),
        ("cin", "On my way, thank you!", A(1, 5)),
        ("cin", "Rocky has not eaten since his dental yesterday and he is very sleepy", AT("5:30 am")),
        ("sout", "ai", "I cannot tell you what is wrong, but a pet that is not eating and is very sleepy after surgery should be seen by a vet. Boundary Bay Veterinary Specialty Hospital in Surrey is on the clinic emergency list as open 24 hours. The clinic will check in with you this morning.", AT("5:31 am")),
    ],
    "Sofia Pereira": [
        ("eout", "Priya", "Oscar's visit summary", "Hi Sofia, here is the summary from Oscar's last visit. Let us know if you have any questions.", A(40, 3)),
        ("cin", "Oscar just ate a whole bar of dark chocolate", AT("11:40 pm", y=True)),
        ("sout", "ai", "That sounds like it needs a vet tonight. Boundary Bay Veterinary Specialty Hospital in Surrey is on the clinic emergency list as open 24 hours. I am texting you their address and phone number now. The clinic will check in with you this morning.", AT("11:41 pm", y=True)),
    ],
    "Arjun Singh": [
        ("call", "in", None, "Called about Maple's blood panel results. Told him the lab results are in and the nurse will go over them.", A(2, 3)),
        ("sout", "Anita", "Hi Arjun, the lab results for Maple are in. Dr. Garg is reviewing them. I will call you once he has.", A(2, 2)),
        ("cin", "OK thank you. Call me at lunch if you can.", A(2, 1)),
        ("note", "Anita", "Arjun asked for a lunchtime call. @Jordan can you take it? I am off tomorrow.", A(2, 0, 30), ["Jordan"]),
        ("call", "out", "Jordan", "Called Arjun with the results as reviewed by Dr. Garg. He will book a recheck.", A(1, 4)),
    ],
    "Hannah Kowalski": [
        ("eout", "Priya", "Nori and Pixel's annual exams", "Hi Hannah, both cats are due for their annual exams. We can book them back to back. What days work?", A(35, 5)),
        ("cein", "Re: annual exams", "Thursdays are best. Do you have anything at 4?", A(35, 2)),
        ("eout", "Priya", "Re: annual exams", "Thursday at 4 pm is open. I have put both cats in. See you then!", A(35, 1)),
        ("sout", "Priya", "Thanks for coming in with Nori and Pixel. Hope the ride home went well!", A(29, 4)),
        ("cin", "They were both exhausted. Thank you for being so gentle with them.", A(29, 2)),
    ],
    "Tomas Lindqvist": [
        ("call", "in", None, "Called to book a pre-surgery exam for Bruno. Booked.", A(25, 4)),
        ("sout", "Marcus", "Hi Tomas, reminder that Bruno's pre-surgery exam is tomorrow at 2 pm. Please bring any questions.", A(21, 5)),
        ("sout", "Marcus", "Hi Tomas, Bruno's procedure is booked. We will send the drop-off details the day before.", A(16, 3)),
        ("cin", "Got it, thanks.", A(16, 1)),
    ],
    "Aisha Rahman": [
        ("sout", "Priya", "Hi Aisha, welcome to Walnut Grove Animal Hospital! Mango's next vaccine is due in a few weeks. Reply here if you want us to book it.", A(36, 4)),
        ("cin", "Yes please! Any day next week works.", A(36, 2)),
        ("sout", "Priya", "Booked for Thursday at 11 am. See you then!", A(36, 1)),
    ],
    "Peter Gagnon": [
        ("eout", "Priya", "Thinking of you and Hank", "Hi Peter, we were so sorry to hear about Hank. He was a favourite here. If you would like, we can send his paw print. No rush at all.", A(200, 3)),
        ("cein", "Re: Thinking of you and Hank", "Thank you, that means a lot. Yes, I would love the paw print.", A(199, 22)),
        ("eout", "Priya", "Pretzel's annual exam", "Hi Peter, Pretzel is due for his annual exam. Reply here and we can find a time.", A(14, 3)),
    ],
    "Rosa Delgado": [
        ("sout", "Marcus", "Hi Rosa, Churro's wellness exam is booked for next Tuesday at 10 am. See you then!", A(40, 4)),
        ("cin", "Perfect, see you then.", A(40, 3)),
    ],
    "Beth Cooper": [
        ("eout", "Priya", "Welcome", "Hi Beth, thanks for bringing in Clementine and Hazel. If you have any questions about their care, reply here and we will help.", A(99, 3)),
        ("cein", "Re: Welcome", "They love the new hay! Thank you for the tips.", A(98, 21)),
        ("sout", "Priya", "Hi Beth, a friendly reminder that Hazel and Clementine are due for their wellness check next week. Reply here and we will book a time.", A(0, 1), {"sched_hours": 18}),
    ],
    "Yusuf Demir": [
        ("sout", "Marcus", "Hi Yusuf, Pasha's dental cleaning is booked for Friday at 8:30 am. We will text the drop-off details the day before.", A(80, 3)),
        ("cin", "OK thanks", A(80, 2)),
        ("note", "Marcus", "Yusuf replied STOP to a promotional email. Marked email as unsubscribed. Appointment texts are still fine.", A(60, 2), []),
    ],
    "Olivia Park": [
        ("sout", "Marcus", "Hi Olivia, welcome to Walnut Grove Animal Hospital. Latte's dental is booked. We will send a reminder the day before.", A(11, 4)),
        ("cin", "Thank you! Can I bring her treats to the appointment?", A(11, 3)),
        ("sout", "Marcus", "Of course, bring a few favourites. It helps with the nerves.", A(11, 2)),
    ],
    "Jamie Whitfield": [
        ("call", "in", None, "Jamie called to ask whether Biscuit (a friend's dog) is due for her vaccines. Explained we can only discuss the account with Grace. Took a number to call back.", A(6, 4)),
        ("sout", "Priya", "Hi Jamie, it is Priya at Walnut Grove Animal Hospital. I spoke with Grace and she is happy for us to talk with you about Biscuit's visits while you are looking after her.", A(5, 3)),
        ("cin", "Thank you! She seems to be limping a bit when she gets up. Should I bring her in?", A(0, 2)),
        ("note", "Priya", "Jamie is a friend of Grace Chen's looking after Biscuit this month. Grace okayed us talking with him. @Anita can you call him about the limp?", A(0, 1, 40), ["Anita"]),
    ],
    "Elena Walker": [
        ("eout", "Marcus", "Pepper's food order", "Hi Elena, Pepper's food order is in. You are on the pickup list, so you can come by any time before 6 pm.", A(25, 3)),
        ("cein", "Re: Pepper's food order", "Thanks Marcus, I'll swing by on my way home.", A(25, 1)),
    ],
    "Margaret Ellis": [
        ("call", "in", None, "Called to say she walks Maple on weekdays and may need to pick up medicine. Added as a caregiver on Arjun Singh's account.", A(40, 3)),
    ],
    "Nathan Boyce": [
        ("cein", "Our new puppy", "Hi there, we are bringing home a Labradoodle puppy named Scout next week. When should we book his first vaccines?", A(4, 2)),
        ("eout", "Priya", "Re: Our new puppy", "Congratulations! We can book his first visit any time in the next two weeks. Do you prefer mornings or afternoons? Please bring any paperwork from the breeder.", A(3, 22)),
        ("cein", "Re: Our new puppy", "Mornings please. Thank you!", A(0, 3)),
    ],
    "Priyanka Menon": [
        ("cin", "Hi, Hannah Kowalski told me about you. I have a new kitten called Koko and want to ask about getting her spayed. What does it cost?", A(8, 3)),
        ("sout", "ai", "Hi Priyanka, this is the automated assistant for Walnut Grove Animal Hospital. I do not have our fees yet, so I have asked the front desk to call you back. Is there a good time?", A(8, 3)),
        ("cin", "Afternoons are best.", A(8, 2)),
    ],
    "Dwayne Tremblay": [
        ("cin", "Do you board cats over Christmas? We are going away for a week.", A(2, 4)),
        ("sout", "ai", "Hi Dwayne, this is the automated assistant for Walnut Grove Animal Hospital. I am not sure about boarding, so I have asked the front desk to get back to you. They open at 8 am.", A(2, 4)),
    ],
    "Fatima Haddad": [
        ("cein", "Moving to the area", "Hello, we are moving to the area in December with our dog Zaytun. How do we transfer her records and become clients?", A(5, 5)),
        ("eout", "Priya", "Re: Moving to the area", "Welcome in advance! Your current clinic can send Zaytun's records to us. I will send you the details once I have checked the process.", A(5, 3)),
    ],
    "Carl Jensen": [
        ("call", "in", None, "Walked in with a kitten he found near the park. Asked what his options are. No consent recorded yet, so no texts or emails.", A(1, 5)),
    ],
    "Mia Sutherland": [
        ("call", "in", None, "Called to say the phone rang a long time before anyone answered last Tuesday. Said she was frustrated. Wants a call from the manager.", A(3, 2)),
        ("note", "Priya", "Mia is upset about the wait on the phone. @Dr. Garg she asked for a call back from you.", A(3, 1), ["Dr. Garg"]),
    ],
    "Kwame Mensah": [
        ("cein", "Do you see rabbits?", "Hi, do you treat rabbits? We just adopted a Mini Rex and want a good vet.", A(7, 4)),
        ("eout", "Priya", "Re: Do you see rabbits?", "Yes, we do see rabbits. Dr. Garg is happy to meet Biscuit Jr. Would you like to book a first visit?", A(7, 2)),
    ],
    "Tessa Lindgren": [
        ("cein", "Second opinion", "Hi, my dog Juniper has been seen elsewhere and I would like a second visit with you. Can I bring her records?", A(10, 5)),
        ("eout", "Priya", "Re: Second opinion", "Of course. Please bring any records you have, and we will book a longer visit so Dr. Garg has time.", A(10, 3)),
        ("cein", "Re: Second opinion", "Great, thank you!", A(9, 20)),
    ],
    "Owen Patel": [
        ("cin", "Hi, I got a kitten called Ziggy. Do you do first vaccines?", A(1, 5)),
        ("sout", "Marcus", "Hi Owen, yes we do. It is Marcus at Walnut Grove Animal Hospital. Do you want to book this week?", A(1, 3)),
    ],
}

# Tasks. title is unique. contact is a display name or None. conv is an overnight conversation id (o1..o5) or None.
TASKS = [
    dict(title="Follow up on an emergency for Oscar", kind="emergency_followup", cls="patient", contact="Sofia Pereira", conv="o3", prio="high",
         status="open", assigned=None, by="ai", age=A(0, 8),
         reason="Says Oscar ate a whole bar of dark chocolate. Pointed to Boundary Bay Veterinary Specialty Hospital."),
    dict(title="Follow up on an emergency for Rocky", kind="emergency_followup", cls="patient", contact="Imran Ali", conv="o5", prio="high",
         status="in_progress", assigned="Priya", by="ai", age=A(0, 3),
         reason="Says Rocky has not eaten since his dental and is very sleepy. Pointed to the emergency list."),
    dict(title="Confirm Willow's vaccines for Thursday", kind="booking", cls="patient", contact="Minh Tran", conv="o1", prio="normal",
         status="open", assigned=None, by="ai", age=A(0, 12), reason="Wants vaccines. Asked for Thursday at 9:30 am."),
    dict(title="Confirm Teddy's check-up", kind="booking", cls="patient", contact="Ngozi Okafor", conv="o2", prio="normal",
         status="open", assigned="Marcus", by="ai", age=A(0, 11), reason="Wants a yearly check-up. Free after 3 pm next week."),
    dict(title="Refill request for Luna", kind="refill", cls="patient", contact="Colin Fraser", conv="o4", prio="normal",
         status="open", assigned=None, by="ai", age=A(0, 3),
         reason="Asked for a refill overnight. A vet has to approve any medicine refill."),
    dict(title="Carprofen refill for Biscuit", kind="refill", cls="patient", contact="Grace Chen", conv=None, prio="normal",
         status="in_progress", assigned="Anita", by="human", age=A(0, 1),
         reason="Voicemail asks for a refill before the weekend. Needs Dr. Garg's approval."),
    dict(title="Call Harpreet about Simba's blood test", kind="results", cls="patient", contact="Harpreet Dhillon", conv=None, prio="normal",
         status="open", assigned="Anita", by="rule", age=A(0, 2), reason="The doctor has reviewed the results. Harpreet wants them by phone."),
    dict(title="Check in on Mochi", kind="escalation", cls="patient", contact="Linh Nguyen", conv=None, prio="normal",
         status="in_progress", assigned="Anita", by="human", age=A(0, 0, 40),
         reason="Not eating since last night. Anita booked a same-day visit and will check in after."),
    dict(title="Do we board cats over Christmas?", kind="unknown_knowledge", cls="inquiry", contact="Dwayne Tremblay", conv=None, prio="normal",
         status="open", assigned=None, by="ai", age=A(2, 4), reason="Asked whether we board cats over Christmas. No knowledge entry yet."),
    dict(title="Spay pricing for Koko", kind="unknown_knowledge", cls="inquiry", contact="Priyanka Menon", conv=None, prio="normal",
         status="open", assigned="Marcus", by="ai", age=A(8, 3), reason="Asked what a spay costs. Fees are not in the knowledge base yet. Prefers afternoon calls."),
    dict(title="Call Mia about the phone wait", kind="complaint", cls="inquiry", contact="Mia Sutherland", conv=None, prio="normal",
         status="open", assigned="Dr. Garg", by="human", age=A(3, 1), reason="Said the phone rang a long time before it was answered and asked for a call back."),
    dict(title="Send Fatima the records transfer steps", kind="callback", cls="inquiry", contact="Fatima Haddad", conv=None, prio="low",
         status="open", assigned=None, by="human", age=A(5, 3), reason="Moving in December. Needs the steps to move Zaytun's records here."),
    dict(title="Book Scout's first puppy visit", kind="booking", cls="inquiry", contact="Nathan Boyce", conv=None, prio="normal",
         status="open", assigned="Priya", by="ai", age=A(4, 2), reason="New Labradoodle puppy coming home next week. Prefers mornings."),
    dict(title="Add Elena as a pickup contact", kind="other", cls="patient", contact="Daniel Walker", conv=None, prio="low",
         status="open", assigned=None, by="human", age=A(20, 2), reason="Elena Walker may pick up Pepper's food orders. Confirm on the next visit."),
    dict(title="Call Daniel about the missed call", kind="callback", cls="patient", contact="Daniel Walker", conv=None, prio="normal",
         status="done", assigned="Marcus", by="rule", age=A(0, 6), done_ago=A(0, 2),
         reason="Missed call, no message. Food order arrived. Marcus called back."),
]

# Calendar events: title, kind, classification, contact, pet, staff, day offset from today, "HH:MM", minutes, status, source
EVENTS = [
    ("Recheck: Biscuit", "checkup", "patient", "Grace Chen", "Biscuit", "Dr. Garg", 0, "10:30", 30, "booked", "import"),
    ("Call back: Jamie Whitfield", "inquiry_call", "inquiry", "Jamie Whitfield", None, "Priya", 0, "15:30", 15, "booked", "app"),
    ("Check-up: Mango", "checkup", "patient", "Aisha Rahman", "Mango", "Dr. Garg", 0, "16:00", 30, "booked", "import"),
    ("Vaccines: Nori and Pixel", "vaccine", "patient", "Hannah Kowalski", "Nori", "Anita", 1, "09:30", 30, "booked", "import"),
    ("Recheck: Rocky", "checkup", "patient", "Imran Ali", "Rocky", "Dr. Garg", 1, "11:00", 20, "booked", "import"),
    ("Surgery: Bruno", "surgery", "patient", "Tomas Lindqvist", "Bruno", "Dr. Garg", 2, "08:30", 120, "booked", "import"),
    ("Call back: Priyanka Menon", "inquiry_call", "inquiry", "Priyanka Menon", None, "Marcus", 2, "14:00", 15, "booked", "app"),
    ("Dental: Latte", "dental", "patient", "Olivia Park", "Latte", "Dr. Garg", 3, "09:00", 60, "booked", "import"),
    ("Check-up: Pretzel", "checkup", "patient", "Peter Gagnon", "Pretzel", "Dr. Garg", 3, "13:30", 30, "booked", "import"),
    ("Vaccines: Willow", "vaccine", "patient", "Minh Tran", "Willow", "Dr. Garg", 4, "09:30", 20, "requested", "app"),
    ("Consult: Juniper", "consult", "inquiry", "Tessa Lindgren", "Juniper", "Dr. Garg", 4, "14:00", 45, "booked", "app"),
    ("Check-up: Teddy", "checkup", "patient", "Ngozi Okafor", "Teddy", "Dr. Garg", 5, "15:30", 30, "requested", "app"),
    ("First visit: Scout", "vaccine", "inquiry", "Nathan Boyce", "Scout", "Dr. Garg", 5, "10:00", 30, "booked", "app"),
    ("Check-up: Churro", "checkup", "patient", "Rosa Delgado", "Churro", "Dr. Garg", 6, "10:00", 30, "booked", "import"),
    ("Recheck: Maple", "checkup", "patient", "Arjun Singh", "Maple", "Dr. Garg", 6, "11:00", 30, "booked", "import"),
    ("Surgery: Pretzel", "surgery", "patient", "Peter Gagnon", "Pretzel", "Dr. Garg", 7, "08:30", 60, "booked", "import"),
    ("Vaccines: Mochi", "vaccine", "patient", "Linh Nguyen", "Mochi", "Anita", 7, "13:00", 20, "booked", "import"),
    ("Call back: Dwayne Tremblay", "inquiry_call", "inquiry", "Dwayne Tremblay", None, "Priya", 8, "09:15", 15, "booked", "app"),
    ("Check-up: Simba", "checkup", "patient", "Harpreet Dhillon", "Simba", "Dr. Garg", 8, "15:00", 30, "booked", "import"),
    ("Dental: Pasha", "dental", "patient", "Yusuf Demir", "Pasha", "Dr. Garg", 9, "08:30", 60, "booked", "import"),
    ("Vaccines: Koko", "vaccine", "inquiry", "Priyanka Menon", "Koko", "Anita", 10, "10:30", 20, "booked", "app"),
    ("Check-up: Oscar", "checkup", "patient", "Sofia Pereira", "Oscar", "Dr. Garg", 10, "16:00", 30, "booked", "import"),
    ("Check-up: Luna", "checkup", "patient", "Colin Fraser", "Luna", "Dr. Garg", 12, "09:30", 30, "booked", "import"),
    ("Consult: Biscuit Jr", "consult", "inquiry", "Kwame Mensah", "Biscuit Jr", "Dr. Garg", 13, "11:00", 30, "booked", "app"),
    ("Staff meeting", "staff", "none", None, None, None, 3, "08:00", 30, "booked", "app"),
    ("Order the lab courier pickup", "reminder", "none", None, None, "Marcus", 1, "15:00", 15, "booked", "app"),
]

# Reminders: contact, owner, due in hours from now, text
REMINDERS = [
    ("Grace Chen", "Priya", 26, "Call Grace to check the refill pickup went smoothly"),
    ("Daniel Walker", "Marcus", 20, "Send Daniel the pickup text for Pepper's food order"),
    ("Linh Nguyen", "Anita", 52, "Check on Mochi after the visit"),
]

# Notifications for Priya (unread), created when the users exist.
NOTIFICATIONS = [
    dict(user="Priya", kind="mention", text="Marcus mentioned you on Daniel Walker", link_contact="Daniel Walker", ago=A(1, 3)),
    dict(user="Priya", kind="task_assigned", task="Book Scout's first puppy visit", text="Dr. Garg gave you a task: Book Scout's first puppy visit", ago=A(0, 2, 10)),
]
