import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys

import httpx
import pytest
from pydantic import ValidationError

from pipeline import analytics, cli, config, llm, rules, schema
from pipeline.config import Paths
from conftest import ROOT, run

NUM_RE = re.compile(r"^\d{8}-\d{6}-(101|102|103)-[0-9a-f]{6}$")


def paths_of(root):
    return Paths(root=root, db=f"{root}/private.db", salt=f"{root}/salt.bin",
                 csv=f"{root}/inbox/all_transcripts.csv", out_dir=f"{root}/out", gold_dir=f"{root}/gold")


def q(root, sql, args=()):
    conn = sqlite3.connect(f"{root}/private.db")
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def ref_of(root, call_id):
    return q(root, "SELECT call_ref FROM calls WHERE call_id=?", (call_id,))[0][0]


def labels_by_key(base_env, labeler="rules-v1"):
    root, truth = base_env["root"], base_env["info"]["truth"]
    out = []
    for cid, t in truth.items():
        js = q(root, "SELECT l.json FROM labels l JOIN calls c ON c.call_ref=l.call_ref "
                     "WHERE c.call_id=? AND l.labeler=?", (cid, labeler))
        out.append((cid, t, json.loads(js[0][0])))
    return out


# ------------------------------------------------------------------ load ----------------------------------
def test_load_counts(base_env):
    root, info = base_env["root"], base_env["info"]
    assert q(root, "SELECT COUNT(*) FROM calls")[0][0] == info["unique"] == 60
    assert q(root, "SELECT COUNT(*) FROM calls_fts")[0][0] == 60
    meta = dict(q(root, "SELECT key, value FROM load_meta"))
    assert meta["rows_read"] == "61" and meta["duplicates_skipped"] == "1" and meta["loaded"] == "60"
    cols = [r[1] for r in q(root, "PRAGMA table_info(calls)")]
    assert cols == ["call_id", "call_ref", "ts", "date", "hour", "weekday", "ext", "number_hash",
                    "has_number", "seconds", "language", "transcript", "plain_text", "private_purged"]
    n_null = sum(1 for t in info["truth"].values() if not t["number"])
    assert q(root, "SELECT COUNT(*) FROM calls WHERE has_number=0 AND number_hash IS NULL")[0][0] == n_null


def test_time_fields(base_env):
    root = base_env["root"]
    cid, t = next(iter(base_env["info"]["truth"].items()))
    ts, date, hour, wd, ext = q(root, "SELECT ts, date, hour, weekday, ext FROM calls WHERE call_id=?", (cid,))[0]
    assert ts.startswith(t["dt"][:19]) and ts[-6:] in ("-07:00", "-08:00")
    assert ts[-6:] == "-07:00"  # August is PDT
    assert hour == int(t["dt"][11:13]) and 0 <= wd <= 6 and ext == t["ext"]


def test_call_ref_and_hash(base_env):
    root = base_env["root"]
    salt = open(f"{root}/salt.bin", "rb").read()
    for cid, ref, nh, has in q(root, "SELECT call_id, call_ref, number_hash, has_number FROM calls"):
        assert NUM_RE.match(ref)
        assert ref.endswith(hmac.new(salt, cid.encode(), hashlib.sha256).hexdigest()[:6])
        t = base_env["info"]["truth"][cid]
        if t["number"]:
            assert nh == hmac.new(salt, t["number"].encode(), hashlib.sha256).hexdigest() and has == 1
        else:
            assert nh is None and has == 0


def test_no_raw_numbers_outside_call_id(base_env):
    root = base_env["root"]
    numbers = {t["number"] for t in base_env["info"]["truth"].values() if t["number"]}
    conn = sqlite3.connect(f"{root}/private.db")
    bad = 0
    for (tbl,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{tbl}")')]
        for row in conn.execute(f'SELECT * FROM "{tbl}"'):
            for col, v in zip(cols, row):
                if tbl == "calls" and col == "call_id":
                    continue
                if isinstance(v, str) and any(n in re.sub(r"\D", "", v) for n in numbers):
                    bad += 1
    conn.close()
    assert bad == 0
    # the raw file name (with .mp3) is not stored anywhere
    raw = open(f"{root}/private.db", "rb").read()
    assert b".mp3" not in raw


def test_load_idempotent(env, capsys):
    assert cli.main(["load", "--root", env["root"]]) == 2
    assert cli.main(["load", "--root", env["root"], "--force"]) == 0
    assert q(env["root"], "SELECT COUNT(*) FROM calls")[0][0] == 60
    assert q(env["root"], "SELECT COUNT(*) FROM calls_fts")[0][0] == 60
    assert q(env["root"], "SELECT COUNT(*) FROM labels")[0][0] == 60  # labels survive a forced reload


# ------------------------------------------------------------------ audit -----------------------------------
def test_audit(env, capsys):
    code, out = run(["audit", "--root", env["root"]], capsys)
    assert code == 0
    d = json.load(open(f"{env['root']}/out/DATA_AUDIT.json"))
    assert d["total_rows"] == 60 and d["duplicate_ids"] == 1 and d["empty_texts"] == 1
    assert d["speaker_labels"]["with_both"] > 10
    assert sum(d["per_extension"].values()) == 60 and sum(d["per_weekday"].values()) == 60
    assert d["durations"]["lt_5s"] == 2 and d["date_range"][0] == "2026-08-03"
    md = open(f"{env['root']}/out/DATA_AUDIT.md").read()
    assert "Durations" in md
    for t in env["info"]["truth"].values():
        assert t["plain"][:40] not in md or not t["plain"]
        if t["number"]:
            assert t["number"] not in md


# ------------------------------------------------------------------ labels -----------------------------------
def test_labels_validate_and_schema_strict(base_env):
    rows = labels_by_key(base_env)
    assert len(rows) == 60
    for _cid, _t, lab in rows:
        m = schema.CallLabel.model_validate(lab)
        assert len(m.red_flags) <= 5 and all(len(f) <= 40 for f in m.red_flags)
    good = rows[0][2]
    with pytest.raises(ValidationError):
        schema.CallLabel.model_validate({**good, "extra": 1})
    with pytest.raises(ValidationError):
        schema.CallLabel.model_validate({**good, "red_flags": ["free text from the call"]})
    with pytest.raises(ValidationError):
        schema.CallLabel.model_validate({**good, "red_flags": ["bleeding"] * 6})
    with pytest.raises(ValidationError):
        schema.CallLabel.model_validate({**good, "followup_text": "copied sentence"})
    with pytest.raises(ValidationError):
        schema.CallLabel.model_validate({**good, "confidence": 1.5})


def test_rules_against_truth(base_env):
    rows = labels_by_key(base_env)
    types = {lab["caller_type"] for _c, _t, lab in rows}
    assert types == {c.value for c in schema.CallerType}
    for _cid, t, lab in rows:
        assert lab["caller_type"] == t["caller_type"], t["key"]
        if t["direction"] != "unknown":
            assert lab["real_direction"] == t["direction"], t["key"]
        for k in ("intent", "outcome", "urgency"):
            if k in t:
                assert lab[k] == t[k], (t["key"], k)
        assert lab["contains_private_content"] == bool(t.get("private", False)), t["key"]
        if "flags" in t:
            assert sorted(lab["red_flags"]) == sorted(t["flags"]), t["key"]
    assert sum(1 for _c, t, _l in rows if t.get("urgency") == "emergency_signs") == 5
    # languages
    assert {lab["language"] for _c, _t, lab in rows} >= {"eng", "hin", "pan", "fra"}
    # follow-up vocabulary only
    for _c, _t, lab in rows:
        assert lab["followup_text"] is None or lab["followup_text"] in schema.FOLLOWUP_VOCAB


def test_negated_symptom_not_flagged():
    rs = rules.load_rules()
    lab = rules.label_call(rs, "Walnut Grove Animal Hospital how can I help you? My dog is not having any "
                               "trouble breathing and there is no seizure, I just want to book a checkup "
                               "appointment for next week please.", 60, "eng")
    assert lab.urgency == "none" and lab.red_flags == []


def test_rules_tunable_without_code(tmp_path):
    import yaml
    cfg = yaml.safe_load(open(config.DEFAULT_RULES))
    cfg["symptoms"]["seizure"] = []
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(cfg))
    rs = rules.load_rules(str(p))
    lab = rules.label_call(rs, "Walnut Grove Animal Hospital how can I help? He is having a seizure right now "
                               "on the floor, my dog is shaking please help", 30, "eng")
    assert "seizure" not in lab.red_flags


def test_fuzzy_clinic_name_variants():
    rs = rules.load_rules()
    for variant in ["walnut grove", "wal not grove", "wall nut grove", "walnut grow", "whole nut grove",
                    "walnutgrove", "wallnut grove"]:
        assert any(p.search(variant) for p in rs.clinic_name), variant


def test_label_rules_prints_only_counts(env, capsys):
    code, out = run(["label-rules", "--root", env["root"]], capsys)
    assert code == 0
    for t in env["info"]["truth"].values():
        if len(t["plain"]) > 30:
            assert t["plain"][:30] not in out.out


# ------------------------------------------------------------------ gold / evaluate -----------------------
def test_gold_flow(env, capsys):
    root = env["root"]
    code, _ = run(["gold-sample", "--root", root, "-n", "25", "--seed", "3"], capsys)
    refs = open(f"{root}/gold/sample.txt").read().split()
    assert code == 0 and len(refs) == 25 and len(set(refs)) == 25 and all(NUM_RE.match(r) for r in refs)
    again = run(["gold-sample", "--root", root, "-n", "25", "--seed", "3"], capsys)
    assert open(f"{root}/gold/sample.txt").read().split() == refs  # seeded => reproducible

    code, out = run(["gold-candidates", "--root", root, "--query", "chocolate OR zylitol", "--limit", "10"], capsys)
    lines = [l for l in out.out.strip().splitlines()]
    assert code == 0 and len(lines) == 2
    assert all(re.fullmatch(r"\d{8}-\d{6}-\d{3}-[0-9a-f]{6}\t[\d.]+", l) for l in lines)

    vm = ref_of(root, next(c for c, t in env["info"]["truth"].items() if t["key"] == "vm_inbound_1"))
    code, out = run(["gold-show", "--root", root, vm, "--chars", "400"], capsys)
    assert code == 0 and "604 555 0188" not in out.out and "### ### ####" in out.out
    assert not re.search(r"\d{3}", out.out.split("\n", 1)[1])
    assert "weekday=" in out.out and "hour=" in out.out and "language=" in out.out
    code, out2 = run(["gold-show", "--root", root, vm, "--chars", "20", "--offset", "10"], capsys)
    assert len(out2.out.split("\n", 1)[1].strip()) <= 20

    base = json.loads(q(root, "SELECT json FROM labels WHERE call_ref=?", (vm,))[0][0])
    assert run(["gold-label", "--root", root, vm, json.dumps(base)], capsys)[0] == 0
    assert q(root, "SELECT COUNT(*) FROM gold")[0][0] == 1
    assert run(["gold-label", "--root", root, vm, json.dumps({**base, "bogus": 1})], capsys)[0] == 2
    assert run(["gold-label", "--root", root, vm, "{not json"], capsys)[0] == 2
    assert run(["gold-label", "--root", root, "nope", json.dumps(base)], capsys)[0] == 2


def _gold_from_truth(root, truth):
    for cid, t in truth.items():
        ref = ref_of(root, cid)
        lab = json.loads(q(root, "SELECT json FROM labels WHERE call_ref=? AND labeler='rules-v1'", (ref,))[0][0])
        lab["caller_type"] = t["caller_type"]
        lab["urgency"] = t.get("urgency", "none")
        lab["contains_private_content"] = bool(t.get("private", False))
        lab["red_flags"] = t.get("flags", [])
        cli.main(["gold-label", "--root", root, ref, json.dumps(lab)])


def test_evaluate_emergency_recall(env, capsys):
    root = env["root"]
    _gold_from_truth(root, env["info"]["truth"])
    capsys.readouterr()
    code, out = run(["evaluate", "--root", root], capsys)
    assert code == 0
    r = json.load(open(f"{root}/out/EVALUATION_rules-v1.json"))
    assert r["compared"] == 60
    assert r["emergency_signs"]["recall"] == 1.0 and r["emergency_signs"]["precision"] == 1.0
    assert r["contains_private_content"]["recall"] == 1.0
    assert r["accuracy"]["caller_type"] == 1.0
    assert os.path.exists(f"{root}/out/EVALUATION_rules-v1.md")
    # a wrong gold label shows up as a disagreement listing only call_refs
    ref = ref_of(root, next(c for c, t in env["info"]["truth"].items() if t["key"] == "booking"))
    lab = json.loads(q(root, "SELECT json FROM gold WHERE call_ref=?", (ref,))[0][0])
    lab["intent"] = "complaint"
    cli.main(["gold-label", "--root", root, ref, json.dumps(lab)])
    run(["evaluate", "--root", root], capsys)
    r = json.load(open(f"{root}/out/EVALUATION_rules-v1.json"))
    assert r["disagreements"]["intent"] == [ref]
    md = open(f"{root}/out/EVALUATION_rules-v1.md").read()
    assert ref in md


# ------------------------------------------------------------------ purge ---------------------------------
def _db_bytes(root):
    data = b""
    for suffix in ("", "-wal", "-shm"):
        p = f"{root}/private.db{suffix}"
        if os.path.exists(p):
            data += open(p, "rb").read()
    return data.lower()


def test_purge_private(env, capsys):
    root, marker = env["root"], env["info"]["marker"]
    assert marker.encode() in _db_bytes(root)  # sanity: present before
    assert len(q(root, "SELECT call_ref FROM calls_fts WHERE calls_fts MATCH ?", (marker,))) == 1
    n_private = sum(1 for t in env["info"]["truth"].values() if t.get("private"))
    code, out = run(["purge-private", "--root", root, "--shred-csv"], capsys)
    assert code == 0
    assert f"purged: {n_private}" in out.out and "csv_shredded: 1" in out.out
    assert not os.path.exists(f"{root}/inbox/all_transcripts.csv")
    data = _db_bytes(root)
    assert marker.encode() not in data
    # other private text is gone too
    assert b"love you" not in data and b"mortgage" not in data
    assert q(root, "SELECT COUNT(*) FROM calls WHERE private_purged=1 AND plain_text IS NULL "
                   "AND transcript IS NULL")[0][0] == n_private
    assert q(root, "SELECT COUNT(*) FROM calls_fts")[0][0] == 60 - n_private
    assert q(root, "SELECT COUNT(*) FROM calls_fts WHERE calls_fts MATCH ?", (marker,))[0][0] == 0
    # non-private text is untouched and still searchable
    assert q(root, "SELECT COUNT(*) FROM calls_fts WHERE calls_fts MATCH 'chocolate'")[0][0] == 1
    assert q(root, "SELECT COUNT(*) FROM calls WHERE private_purged=0 AND plain_text IS NOT NULL")[0][0] \
        >= 60 - n_private - 1
    # purging again is a no-op
    code, out = run(["purge-private", "--root", root], capsys)
    assert "purged: 0" in out.out


def test_purge_respects_gold_override(env, capsys):
    root = env["root"]
    pid = next(c for c, t in env["info"]["truth"].items() if t["key"] == "private_1")
    ref = ref_of(root, pid)
    lab = json.loads(q(root, "SELECT json FROM labels WHERE call_ref=?", (ref,))[0][0])
    lab["contains_private_content"] = False
    cli.main(["gold-label", "--root", root, ref, json.dumps(lab)])
    code, out = run(["purge-private", "--root", root], capsys)
    n_private = sum(1 for t in env["info"]["truth"].values() if t.get("private"))
    assert f"purged: {n_private - 1}" in out.out
    assert env["info"]["marker"].encode() in _db_bytes(root)  # gold said not private => kept


# ------------------------------------------------------------------ analytics -------------------------------
def test_analytics(env, capsys):
    root = env["root"]
    code, out = run(["analytics", "--root", root], capsys)
    assert code == 0
    res = json.load(open(f"{root}/out/analytics.json"))
    meta, rows = res["metadata"], res["rows"]
    assert set(meta) >= {"generated_at", "date_range", "total_calls", "labeler"}
    assert meta["total_calls"] == 60 and meta["date_range"] == ["2026-08-03", "2026-08-15"]
    n_private = sum(1 for t in env["info"]["truth"].values() if t.get("private"))
    assert meta["calls_in_mix"] == 60 - n_private
    for r in rows:
        assert set(r) == {"source", "metric", "dim1", "dim2", "value", "n", "suppressed"}
        assert r["source"] == "derived_from_real_calls"
        if r["suppressed"]:
            assert r["value"] is None and r["n"] is None
        else:
            assert r["n"] is not None and r["n"] >= 5
    assert any(r["suppressed"] for r in rows) and any(not r["suppressed"] for r in rows)
    cell = [r for r in rows if r["metric"] == "calls_total_by_weekday_hour" and r["dim1"] == "tue" and r["dim2"] == "10"]
    assert cell[0]["value"] == 6 and not cell[0]["suppressed"]
    avg = [r for r in rows if r["metric"] == "calls_avg_per_day_by_weekday_hour" and r["dim1"] == "tue" and r["dim2"] == "10"]
    assert avg[0]["value"] == 3.0  # 6 calls over 2 Tuesdays in range (Aug 4, Aug 11)
    # a small cell is suppressed
    small = [r for r in rows if r["metric"] == "calls_by_intent" and r["dim1"] == "complaint"]
    assert small[0]["suppressed"]
    priv = [r for r in rows if r["metric"] == "private_or_personal_total"]
    assert len(priv) == 1  # 3 < 5 so suppressed, but present
    # no private-only categories leak into mix
    assert not any(r["dim1"] == "private_personal" for r in rows if r["metric"] == "calls_by_caller_type")
    # no text, numbers or ids anywhere in the file
    raw = open(f"{root}/out/analytics.json").read()
    for cid, t in env["info"]["truth"].items():
        assert cid not in raw and (not t["number"] or t["number"] not in raw)
        assert not t["plain"] or t["plain"][:25] not in raw
    assert not re.search(r"\d{8}-\d{6}", raw)
    # leak scan on the whole out dir is clean
    assert cli.main(["verify-no-leak", "--root", root, "--paths", f"{root}/out"]) == 0


def test_after_hours_definition():
    f = analytics.is_after_hours
    assert f(0, 7) and not f(0, 8) and not f(4, 17) and f(4, 18)
    assert not f(5, 13) and f(5, 14) and f(6, 10)


def test_analytics_with_all_private_excluded(env, capsys):
    root = env["root"]
    cli.main(["purge-private", "--root", root])
    capsys.readouterr()
    cli.main(["analytics", "--root", root])
    res = json.load(open(f"{root}/out/analytics.json"))
    assert res["metadata"]["calls_in_mix"] == 60 - sum(1 for t in env["info"]["truth"].values() if t.get("private"))  # purged rows keep metadata and labels but stay excluded


# ------------------------------------------------------------------ verify-no-leak -------------------------
def test_verify_no_leak(env, tmp_path, capsys):
    root = env["root"]
    truth = env["info"]["truth"]
    sample_number = next(t["number"] for t in truth.values() if t["number"])
    pretty = f"{sample_number[:3]}-{sample_number[3:6]}-{sample_number[6:]}"
    sentence = next(t["plain"] for t in truth.values() if t["key"] == "billing")

    clean = tmp_path / "clean"
    clean.mkdir()
    (clean / "notes.txt").write_text("Totally unrelated notes about the weather. Rain expected on Tuesday, 12 mm.")
    (clean / "data.json").write_text(json.dumps({"n": 5, "share": 0.25}))
    code, out = run(["verify-no-leak", "--root", root, "--paths", str(clean)], capsys)
    assert code == 0 and "matched_files_total: 0" in out.out

    dirty = tmp_path / "dirty"
    dirty.mkdir()
    (dirty / "a.txt").write_text(f"please call {pretty} tomorrow")
    (dirty / "b.md").write_text("summary: " + sentence)
    nh = q(root, "SELECT number_hash FROM calls WHERE number_hash IS NOT NULL LIMIT 1")[0][0]
    (dirty / "c.json").write_text(json.dumps({"id": nh}))
    db = sqlite3.connect(str(dirty / "d.db"))
    db.execute("CREATE TABLE t(x TEXT)")
    db.execute("INSERT INTO t VALUES (?)", ("blah " + sentence,))
    db.commit()
    db.close()
    (dirty / "e.txt").write_text("nothing here")
    code, out = run(["verify-no-leak", "--root", root, "--paths", str(dirty), str(clean), "--list-files"], capsys)
    assert code == 1
    assert "matched_files_total: 4" in out.out
    first = [l for l in out.out.splitlines() if l.startswith(str(dirty))][0]
    assert "phone_patterns=1" in first and "known_numbers=1" in first and "number_hashes=1" in first
    assert "shingles=" in first and "shingles=0" not in first
    # never prints the matched content
    assert sentence[:30] not in out.out and pretty not in out.out and nh not in out.out
    # ignore regex suppresses the phone-like hit
    (dirty / "a.txt").write_text("call 604-555-0100")
    only = tmp_path / "only"
    only.mkdir()
    (only / "a.txt").write_text("call 604-555-0100")
    assert cli.main(["verify-no-leak", "--root", root, "--paths", str(only), "--ignore-regex", r"^604"]) == 0


def test_verify_no_leak_catches_private_db_itself(env, capsys):
    # the private DB itself contains call_ids with numbers, so scanning it must fail
    code, _ = run(["verify-no-leak", "--root", env["root"], "--paths", f"{env['root']}/private.db"], capsys)
    assert code == 1


# ------------------------------------------------------------------ LLM labeler ----------------------------
def _env_file(tmp_path, allow="1", key="sk-test"):
    p = tmp_path / "llm.env"
    p.write_text(f"# comment\nBASE_URL=https://example.invalid/v1\nAPI_KEY={key}\nMODEL=test-model\n"
                 f"ALLOW_TRANSCRIPT_LABELING={allow}\n")
    return str(p)


GOOD = {"real_direction": "inbound", "caller_type": "client", "intent": "booking", "outcome": "unclear",
        "urgency": "none", "red_flags": [], "promised_followup": False, "followup_text": None,
        "contains_private_content": False, "language": "eng", "confidence": 0.8}


def test_llm_refuses_without_flag_or_key(env, tmp_path):
    p = paths_of(env["root"])
    assert llm.run(p, _env_file(tmp_path, allow="0")) == 3
    assert llm.run(p, _env_file(tmp_path, key="")) == 3
    assert llm.run(p, str(tmp_path / "missing.env")) == 2


def test_llm_labeling(env, tmp_path, capsys):
    root = env["root"]
    seen = []
    state = {"bad_once": set()}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"] == {"type": "json_object"}
        assert request.headers["authorization"] == "Bearer sk-test"
        user = body["messages"][-1]["content"]
        seen.append(body)
        if "WRONGJSON" in json.dumps(body["messages"]) and len(body["messages"]) == 2:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    transport = httpx.MockTransport(handler)
    p = paths_of(root)
    assert llm.run(p, _env_file(tmp_path), transport=transport) == 0
    out = capsys.readouterr().out
    n_private = sum(1 for t in env["info"]["truth"].values() if t.get("private"))
    n_empty = 1
    expected = 60 - n_private - n_empty
    assert f"attempted: {expected}" in out and f"ok: {expected}" in out
    assert q(root, "SELECT COUNT(*) FROM labels WHERE labeler='llm-test-model'")[0][0] == expected
    # nothing from private calls was sent
    blob = json.dumps(seen).lower()
    assert env["info"]["marker"] not in blob and "love you" not in blob
    assert all(len(b["messages"][-1]["content"]) <= 6000 + 20 for b in seen)
    # resumable: second run attempts nothing
    seen.clear()
    assert llm.run(p, _env_file(tmp_path), transport=transport) == 0
    assert "attempted: 0" in capsys.readouterr().out and not seen


def test_llm_retries_once_then_counts_invalid(env, tmp_path, capsys):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"message": {"content": "{\"nope\": 1}"}}]})

    assert llm.run(paths_of(env["root"]), _env_file(tmp_path), limit=3, transport=httpx.MockTransport(handler)) == 0
    out = capsys.readouterr().out
    assert "invalid: 3" in out and len(calls) == 6  # exactly one retry per call
    assert q(env["root"], "SELECT COUNT(*) FROM labels WHERE labeler='llm-test-model'")[0][0] == 0


# ------------------------------------------------------------------ WIPE.sh ---------------------------------
def test_wipe_script(env, tmp_path, capsys):
    root = env["root"]
    assert cli.main(["wipe-script", "--root", root]) == 0
    script = f"{root}/WIPE.sh"
    assert os.access(script, os.X_OK)
    text = open(script).read()
    assert text.startswith("#!/usr/bin/env bash") and "set -euo pipefail" in text
    assert "\u2014" not in text and "\u2013" not in text

    app = tmp_path / "app.db"
    c = sqlite3.connect(app)
    c.execute("CREATE TABLE call_analytics(id INTEGER PRIMARY KEY, source TEXT, metric TEXT)")
    c.execute("CREATE TABLE imported_calls(id INTEGER PRIMARY KEY, note TEXT)")
    c.execute("CREATE TABLE keepme(x TEXT)")
    c.executemany("INSERT INTO call_analytics(source, metric) VALUES (?,?)",
                  [("derived_from_real_calls", "a"), ("derived_from_real_calls", "b"), ("synthetic", "c")])
    c.executemany("INSERT INTO imported_calls(note) VALUES (?)", [("x",), ("y",)])
    c.execute("INSERT INTO keepme VALUES ('keep')")
    c.commit()
    c.close()
    claude = tmp_path / "claude" / "proj"
    (claude / "sess" / "tool-results").mkdir(parents=True)
    (claude / "s1.jsonl").write_text("secret")
    (claude / "sess" / "tool-results" / "r1.txt").write_text("secret")
    (claude / "notes.txt").write_text("keep me")

    envv = dict(os.environ, WIPE_ROOT=root, WIPE_APP_DB=str(app), WIPE_CLAUDE_DIR=str(tmp_path / "claude"),
                WIPE_SKIP_ROOT_CHECK="1")
    # dry run changes nothing
    r = subprocess.run(["bash", script], env={**envv, "DRY_RUN": "1"}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "dry_run=1" in r.stdout and "app_call_analytics_rows_deleted=2" in r.stdout
    assert os.path.exists(f"{root}/private.db") and (claude / "s1.jsonl").exists()

    r = subprocess.run(["bash", script], env=envv, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not os.path.exists(root)
    for line in ("app_call_analytics_rows_deleted=2", "app_imported_calls_rows_deleted=2",
                 "claude_log_files_shredded=2", "user_removed=0"):
        assert line in r.stdout, r.stdout
    assert re.search(r"files_shredded=\d+", r.stdout)
    c = sqlite3.connect(app)
    assert c.execute("SELECT COUNT(*) FROM call_analytics").fetchone()[0] == 1
    assert c.execute("SELECT source FROM call_analytics").fetchone()[0] == "synthetic"
    assert c.execute("SELECT COUNT(*) FROM imported_calls").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM keepme").fetchone()[0] == 1
    c.close()
    assert not (claude / "s1.jsonl").exists() and not (claude / "sess" / "tool-results").exists()
    assert (claude / "notes.txt").read_text() == "keep me"


def test_wipe_script_refuses_dangerous_root(tmp_path):
    cli.main(["wipe-script", "--root", str(tmp_path)])
    for bad in ("/", "/srv", "relative/path"):
        r = subprocess.run(["bash", str(tmp_path / "WIPE.sh")], capture_output=True, text=True,
                           env=dict(os.environ, WIPE_ROOT=bad, WIPE_SKIP_ROOT_CHECK="1", DRY_RUN="1",
                                    WIPE_APP_DB=str(tmp_path / "none.db"), WIPE_CLAUDE_DIR=str(tmp_path / "none")))
        assert r.returncode != 0


# ------------------------------------------------------------------ misc -------------------------------------
def test_no_em_dashes_in_package():
    pkg = os.path.join(ROOT, "pipeline")
    for dp, dn, fn in os.walk(pkg):
        dn[:] = [d for d in dn if d not in (".venv", "__pycache__", ".pytest_cache")]
        for f in fn:
            if f.endswith((".py", ".yaml", ".sh", ".md", ".txt")):
                s = open(os.path.join(dp, f), encoding="utf-8").read()
                assert "\u2014" not in s, f


def test_cli_module_entrypoint(tmp_path):
    r = subprocess.run([sys.executable, "-m", "pipeline", "--help"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0 and "purge-private" in r.stdout
    r = subprocess.run([sys.executable, "-m", "pipeline", "audit", "--root", str(tmp_path)], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 2  # no database yet; nothing created
    assert not (tmp_path / "private.db").exists()
