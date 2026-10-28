"""The production policy (private_aggressive) flags anything the rules cannot place as clinic work."""
from pipeline import rules


def test_aggressive_flags_uncertain_calls(monkeypatch):
    monkeypatch.setenv("WALNUT_PRIVATE_AGGRESSIVE", "1")
    rs = rules.load_rules()
    lab = rules.label_call(rs, "Hello, is this the vet? Oh sorry, wrong number, I was looking for a different place.", 30)
    assert lab.contains_private_content
    lab = rules.label_call(rs, "Namaste, kuch baat karni thi, baad mein phone karta hoon, theek hai.", 40, "hin")
    assert lab.contains_private_content


def test_aggressive_keeps_clear_clinic_work(monkeypatch):
    monkeypatch.setenv("WALNUT_PRIVATE_AGGRESSIVE", "1")
    rs = rules.load_rules()
    text = ("Walnut Grove Animal Hospital, how may I help you? Hi, I would like to book an appointment for my dog "
            "for his vaccines next week. Sure, we have Tuesday at ten. Great, see you then. Thank you, bye.")
    assert not rules.label_call(rs, text, 60).contains_private_content
