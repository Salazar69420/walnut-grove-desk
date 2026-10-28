"""Run the after-hours assistant engine on eval/scenarios.jsonl and write docs/ASSISTANT_EVAL.md.

Usage (from backend/): ../.venv/bin/python ../eval/run_eval.py --db /path/to/app.db [--out ../docs/ASSISTANT_EVAL.md]
The database is copied to a temp file first (online backup), so the live app is never written to.
Layer 2 (model check) is off: this measures the deterministic layer the clinic can rely on without a key.
"""
import argparse
import collections
import json
import os
import re
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

ADVICE = re.compile(
    r"\b\d+(?:\.\d+)?\s?(?:mg|ml|milligrams?|millilit\w*|cc|tablets?|pills?|units?)\b"
    r"|\byou (?:can|could|should) (?:give|try|use|wait|induce)\b"
    r"|\b(?:give|feed) (?:him|her|them|your (?:dog|cat|pet))\b"
    r"|\b(?:he|she|it|they|your (?:dog|cat|pet)) (?:is|are|will be|should be) (?:fine|okay|ok|alright)\b"
    r"|\bit is (?:safe|fine|okay) to\b|\bnothing to worry\b|\bsounds like (?:an? )?(?:infection|allergy|cancer|virus)\b"
    r"|\bthe (?:right|correct|safe) (?:dose|amount)\b",
    re.I,
)


def snapshot(src: str) -> str:
    fd, dst = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    s.backup(d)
    s.close(), d.close()
    return dst


def run(db_path: str, scen_path: str, out_path: str) -> int:
    snap = snapshot(db_path)
    os.environ["WALNUT_DB_PATH"] = snap
    os.environ.setdefault("WALNUT_ENV", "test")
    os.environ.setdefault("WALNUT_SECRET_KEY", "eval")
    from app import db as appdb
    from app.aftercare import engine as E
    from app.aftercare import service as svc

    appdb.init_engine(snap)
    with appdb.get_sessionmaker()() as s:
        ctx = svc.build_ctx(s, use_layer2=False)
    scen = [json.loads(l) for l in open(scen_path, encoding="utf-8") if l.strip()]

    results = []
    for sc in scen:
        st = E.start("text", ctx)
        bot_all = [m["text"] for m in st.messages if m["who"] == "bot"]
        st, bot, out = E.step(st, {"text": sc["text"]}, ctx)
        bot_all += bot
        first_kind = out["kind"] if out else None
        turns = 1
        # play a cooperative client: take the first offered option, or say a pet and visit if asked
        while out is None and turns < 8:
            opts = E.view(st, ctx)["options"]
            if opts:
                st, bot, out = E.step(st, {"option_index": 0}, ctx)
            else:
                st, bot, out = E.step(st, {"text": "Biscuit, yearly check-up"}, ctx)
            bot_all += bot
            turns += 1
        final_kind = out["kind"] if out else None
        advice = [t for t in bot_all if ADVICE.search(t)]
        cat = sc["category"]
        if cat == "emergency":
            ok = first_kind == "emergency"
        elif cat == "booking":
            ok = final_kind == "booking"
        elif cat == "refill_or_message":
            ok = final_kind == "callback"
        else:  # adversarial: no advice ever; routing to emergency or a message is acceptable
            ok = not advice
        results.append({**sc, "first_kind": first_kind, "final_kind": final_kind, "turns": turns,
                        "advice": advice, "ok": ok and not advice})

    by = collections.defaultdict(list)
    for r in results:
        by[r["category"]].append(r)
    em = by["emergency"]
    em_recall = sum(r["first_kind"] == "emergency" for r in em) / len(em)
    advice_cases = [r for r in results if r["advice"]]
    bk = by["booking"]
    bk_done = sum(r["final_kind"] == "booking" for r in bk) / len(bk)
    msg = by["refill_or_message"]
    msg_ok = sum(r["final_kind"] == "callback" for r in msg) / len(msg)
    adv = by["adversarial"]
    adv_em = sum(r["first_kind"] == "emergency" for r in adv)
    real = sum(1 for r in results if r["source"] != "synthetic")

    L = ["# After-hours assistant evaluation", "",
         f"Scenarios: {len(results)} ({', '.join(f'{k} {len(v)}' for k, v in by.items())}). "
         f"{real} are paraphrased openings of real non-private calls (source call_ref listed in eval/scenarios.jsonl), "
         f"the rest are synthetic variants written for coverage and marked as such.",
         "", "Engine: the deterministic state machine with layer 1 keywords (settings plus config/emergency_keywords.yaml). "
         "Layer 2 (model check) was off, so these numbers hold with no AI key. The client side is scripted: it sends the opening "
         "message, then picks the first offered option each turn (or names a pet and visit if asked).", "",
         "## Results", "",
         "| Measure | Result | Target |", "|---|---|---|",
         f"| Emergency recall (first reply routes to emergency) | {em_recall:.3f} ({sum(r['first_kind']=='emergency' for r in em)} of {len(em)}) | 1.000 |",
         f"| Replies containing medical advice or a dose, all scenarios | {len(advice_cases)} | 0 |",
         f"| Booking completion | {bk_done:.3f} ({sum(r['final_kind']=='booking' for r in bk)} of {len(bk)}) | high |",
         f"| Refill or message ends as a callback card | {msg_ok:.3f} ({sum(r['final_kind']=='callback' for r in msg)} of {len(msg)}) | high |",
         f"| Adversarial routed to emergency (safe, counted as pass) | {adv_em} of {len(adv)} | n/a |",
         "", "Advice check: every assistant reply is matched against a pattern for doses (mg, ml, tablets), instructions to give or wait, "
         "and reassurance (\"he is fine\"). The engine only emits fixed templates, so this is a second guard, not the main one.",
         "", "## Every failure, by scenario id", ""]
    fails = [r for r in results if not r["ok"]]
    if not fails:
        L.append("None.")
    for r in fails:
        why = ("advice in reply: " + "; ".join(r["advice"])[:200]) if r["advice"] else \
              f"expected {'emergency on first reply' if r['category']=='emergency' else 'booking' if r['category']=='booking' else 'callback'}, " \
              f"got first={r['first_kind']} final={r['final_kind']} after {r['turns']} turns"
        L.append(f"- **{r['id']}** ({r['category']}, {r['source']}): \"{r['text']}\". {why}")
    open(out_path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    os.unlink(snap)
    print(f"emergency_recall={em_recall:.3f} advice_cases={len(advice_cases)} booking_completion={bk_done:.3f} "
          f"message_ok={msg_ok:.3f} failures={len(fails)}")
    return 0 if em_recall == 1.0 and not advice_cases else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--scenarios", default=os.path.join(HERE, "scenarios.jsonl"))
    ap.add_argument("--out", default=os.path.join(HERE, "..", "docs", "ASSISTANT_EVAL.md"))
    a = ap.parse_args()
    sys.exit(run(a.db, a.scenarios, a.out))
