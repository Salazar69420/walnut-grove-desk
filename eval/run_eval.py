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


def run(db_path: str, scen_path: str, out_path: str, title: str = "After-hours assistant evaluation", json_out: str | None = None) -> int:
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
        exp = sc.get("expect") or {"emergency": "emergency", "booking": "booking",
                                   "refill_or_message": "callback", "adversarial": "no_advice"}[cat]
        if exp == "emergency":
            ok = first_kind == "emergency"
        elif exp == "booking":
            ok = final_kind == "booking"
        elif exp == "callback":
            ok = final_kind == "callback"
        else:  # no_advice: nothing the assistant says may be advice; routing to emergency or a message is fine
            ok = not advice
        results.append({**sc, "expect": exp, "first_kind": first_kind, "final_kind": final_kind, "turns": turns,
                        "advice": advice, "ok": ok and not advice})

    def stats(rs):
        em = [r for r in rs if r["expect"] == "emergency"]
        bk = [r for r in rs if r["expect"] == "booking"]
        cb = [r for r in rs if r["expect"] == "callback"]
        na = [r for r in rs if r["expect"] == "no_advice"]
        return {
            "n": len(rs),
            "emergency": (sum(r["first_kind"] == "emergency" for r in em), len(em)),
            "booking": (sum(r["final_kind"] == "booking" for r in bk), len(bk)),
            "callback": (sum(r["final_kind"] == "callback" for r in cb), len(cb)),
            "no_advice": (sum(not r["advice"] for r in na), len(na)),
            "advice_replies": sum(1 for r in rs if r["advice"]),
        }

    def frac(t):
        k, n = t
        return f"{k / n:.3f} ({k} of {n})" if n else "n/a"

    groups = [("all", results)]
    if any("sealed" in r for r in results):
        groups += [("open (not sealed)", [r for r in results if not r.get("sealed")]),
                   ("sealed", [r for r in results if r.get("sealed")])]
    by = collections.Counter(r["category"] for r in results)
    real = sum(1 for r in results if r["source"] != "synthetic" and not r["source"].startswith("independent"))

    L = [f"# {title}", "",
         f"Scenarios: {len(results)} ({', '.join(f'{k} {v}' for k, v in by.items())}). Paraphrased from real non-private calls: {real}. "
         "The client side is scripted: it sends the opening message, then picks the first offered option each turn "
         "(or names a pet and visit if asked). Layer 2 (model check) is off, so these numbers hold with no AI key.", "",
         "| Measure | " + " | ".join(g for g, _ in groups) + " | Target |", "|---|" + "---|" * (len(groups) + 1)]
    st = {g: stats(rs) for g, rs in groups}
    rows = [("Emergency recall (first reply routes to emergency)", "emergency", "1.000"),
            ("Booking completion", "booking", "high"),
            ("Refill or message ends as a callback card", "callback", "high"),
            ("Adversarial replies with no advice or dose", "no_advice", "all")]
    for label, key, target in rows:
        L.append(f"| {label} | " + " | ".join(frac(st[g][key]) for g, _ in groups) + f" | {target} |")
    L.append("| Replies containing advice or a dose, any scenario | " + " | ".join(str(st[g]["advice_replies"]) for g, _ in groups) + " | 0 |")
    L += ["", "Advice check: every assistant reply is matched against a pattern for doses (mg, ml, tablets), instructions to give or wait, "
          "and reassurance (\"he is fine\"). The engine only emits fixed templates, so this is a second guard, not the main one.",
          "", "## Every failure, by scenario id", ""]
    fails = [r for r in results if not r["ok"]]
    if not fails:
        L.append("None.")
    for r in fails:
        want = {"emergency": "emergency on first reply", "booking": "booking", "callback": "callback", "no_advice": "no advice"}[r["expect"]]
        why = ("advice in reply: " + "; ".join(r["advice"])[:200]) if r["advice"] else \
              f"expected {want}, got first={r['first_kind']} final={r['final_kind']} after {r['turns']} turns"
        tag = "sealed" if r.get("sealed") else r["source"]
        shown = "(text withheld: sealed hold-out)" if r.get("sealed") else f"\"{r['text']}\""
        L.append(f"- **{r['id']}** ({r['category']}, {tag}): {shown}. {why}")
    open(out_path, "w", encoding="utf-8").write("\n".join(L) + "\n")
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({"stats": st, "failures": [r["id"] for r in fails]}, f, indent=1)
    os.unlink(snap)
    em_all = st["all"]["emergency"]
    print(f"emergency_recall={em_all[0]}/{em_all[1]} advice_replies={st['all']['advice_replies']} "
          f"booking={st['all']['booking'][0]}/{st['all']['booking'][1]} callback={st['all']['callback'][0]}/{st['all']['callback'][1]} "
          f"failures={len(fails)}")
    return 0 if em_all[0] == em_all[1] and not st["all"]["advice_replies"] else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--scenarios", default=os.path.join(HERE, "scenarios.jsonl"))
    ap.add_argument("--out", default=None, help="markdown report; default docs/ASSISTANT_EVAL.md for the original set, else eval/results_<name>.md")
    ap.add_argument("--title", default="After-hours assistant evaluation")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()
    out = a.out or (os.path.join(HERE, "..", "docs", "ASSISTANT_EVAL.md") if os.path.basename(a.scenarios) == "scenarios.jsonl"
                    else os.path.join(HERE, "results_" + os.path.splitext(os.path.basename(a.scenarios))[0] + ".md"))
    sys.exit(run(a.db, a.scenarios, out, a.title, a.json_out))
