"""telus-numbers: only the client side of a recorded call, only ordinary North American numbers."""
from pipeline.telus.numbers import other_party


def rec(direction="Inbound", frm="+1 (604) 555-0161", to="+16045550100", rid="12345", ext_from=None):
    r = {"id": "1", "direction": direction, "from": {"phoneNumber": frm}, "to": {"phoneNumber": to, "extensionId": "77"}}
    if ext_from:
        r["from"]["extensionId"] = ext_from
    if rid:
        r["recording"] = {"id": rid}
    return r


def test_inbound_gives_the_caller_and_outbound_the_person_called():
    assert other_party(rec()) == ("12345", "+16045550161")
    out = {"id": "1", "direction": "Outbound", "from": {"phoneNumber": "+16045550100", "extensionId": "77"}, "to": {"phoneNumber": "16045550199"}, "recording": {"id": "9"}}
    assert other_party(out) == ("9", "+16045550199")


def test_no_recording_internal_or_odd_numbers_are_left_out():
    assert other_party(rec(rid=None)) is None
    assert other_party(rec(ext_from="55")) is None
    assert other_party(rec(frm="911")) is None
    assert other_party(rec(frm="+44 20 7946 0000")) is None
    assert other_party(rec(direction="Other")) is None
    assert other_party("x") is None
