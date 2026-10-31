from pipeline.telus.calls_live import row


def test_rows_keep_only_safe_fields():
    r = row({"id": "11", "startTime": "2026-10-31T17:00:00.000Z", "direction": "Inbound", "result": "Missed", "duration": 0,
             "from": {"phoneNumber": "+16045550161", "name": "Someone"}, "to": {"extensionId": "5"}, "recording": {"id": "9"}})
    assert r == {"id": "11", "at": "2026-10-31T17:00:00+00:00", "d": "inbound", "r": "missed", "s": 0, "n": "+16045550161", "rec": "9"}
    assert row({"id": "12", "startTime": "2026-10-31T17:00:00Z", "direction": "Inbound", "from": {"extensionId": "7"}})["d"] == "internal"
    assert row({"id": "../x", "startTime": "2026-10-31T17:00:00Z"}) is None
