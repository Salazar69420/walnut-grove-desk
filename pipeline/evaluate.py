"""Compare a labeler to gold. Counts and call_refs only."""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict

from . import db as dbm

FIELDS = ["real_direction", "caller_type", "intent", "outcome", "urgency", "red_flags",
          "promised_followup", "followup_text", "contains_private_content", "language"]


def _prf(tp: int, fp: int, fn: int) -> dict:
    return {"tp": tp, "fp": fp, "fn": fn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None}


def compute(conn, labeler: str) -> dict:
    gold = {r["call_ref"]: json.loads(r["json"]) for r in conn.execute("SELECT call_ref, json FROM gold")}
    pred = {r["call_ref"]: json.loads(r["json"])
            for r in conn.execute("SELECT call_ref, json FROM labels WHERE labeler=?", (labeler,))}
    common = sorted(set(gold) & set(pred))
    res = {"labeler": labeler, "gold_total": len(gold), "compared": len(common),
           "gold_without_prediction": len(set(gold) - set(pred)),
           "accuracy": {}, "confusion": {}, "disagreements": {}}
    for f in FIELDS:
        ok = 0
        conf = Counter()
        dis = []
        for ref in common:
            g, p = gold[ref][f], pred[ref][f]
            gk = json.dumps(sorted(g)) if isinstance(g, list) else str(g)
            pk = json.dumps(sorted(p)) if isinstance(p, list) else str(p)
            if gk == pk:
                ok += 1
            else:
                dis.append(ref)
            conf[f"{gk} -> {pk}"] += 1
        res["accuracy"][f] = ok / len(common) if common else None
        res["confusion"][f] = dict(sorted(conf.items()))
        res["disagreements"][f] = dis
    emerg = Counter()
    priv = Counter()
    for ref in common:
        ge, pe = gold[ref]["urgency"] == "emergency_signs", pred[ref]["urgency"] == "emergency_signs"
        emerg["tp" if ge and pe else "fp" if pe else "fn" if ge else "tn"] += 1
        gp, pp = bool(gold[ref]["contains_private_content"]), bool(pred[ref]["contains_private_content"])
        priv["tp" if gp and pp else "fp" if pp else "fn" if gp else "tn"] += 1
    res["emergency_signs"] = _prf(emerg["tp"], emerg["fp"], emerg["fn"])
    res["emergency_signs"]["tn"] = emerg["tn"]
    res["contains_private_content"] = _prf(priv["tp"], priv["fp"], priv["fn"])
    res["contains_private_content"]["tn"] = priv["tn"]
    return res


def to_markdown(r: dict) -> str:
    fmt = lambda x: "n/a" if x is None else f"{x:.3f}"  # noqa: E731
    L = [f"# Evaluation of {r['labeler']} against gold", "",
         f"- gold labels: {r['gold_total']}", f"- compared: {r['compared']}",
         f"- gold without prediction: {r['gold_without_prediction']}", "",
         "## Accuracy per field", "", "| field | accuracy | disagreements |", "|---|---|---|"]
    for f in FIELDS:
        L.append(f"| {f} | {fmt(r['accuracy'][f])} | {len(r['disagreements'][f])} |")
    for key in ("emergency_signs", "contains_private_content"):
        x = r[key]
        L += ["", f"## {key}", "", f"- tp={x['tp']} fp={x['fp']} fn={x['fn']} tn={x['tn']}",
              f"- precision={fmt(x['precision'])} recall={fmt(x['recall'])}"]
    L += ["", "## Confusion counts (gold -> predicted)", ""]
    for f in FIELDS:
        L.append(f"### {f}")
        for k, v in r["confusion"][f].items():
            L.append(f"- {k}: {v}")
        L.append("")
    L += ["## Disagreeing call_refs", ""]
    for f in FIELDS:
        L.append(f"### {f}")
        L.append(", ".join(r["disagreements"][f]) or "(none)")
        L.append("")
    return "\n".join(L) + "\n"


def run(paths, labeler: str) -> int:
    conn = dbm.connect(paths.db)
    r = compute(conn, labeler)
    conn.close()
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", labeler)
    os.makedirs(paths.out_dir, mode=0o700, exist_ok=True)
    base = os.path.join(paths.out_dir, f"EVALUATION_{safe}")
    with open(base + ".md", "w", encoding="utf-8") as f:
        f.write(to_markdown(r))
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2)
    print(f"evaluated {r['compared']} calls ({labeler}); "
          f"emergency recall={r['emergency_signs']['recall']} precision={r['emergency_signs']['precision']}; "
          f"private recall={r['contains_private_content']['recall']} "
          f"precision={r['contains_private_content']['precision']}")
    for f in FIELDS:
        a = r["accuracy"][f]
        print(f"accuracy {f}: {'n/a' if a is None else f'{a:.3f}'}")
    return 0
