"""Evaluate the news pipeline against the gold set (analysis/eval/).

    python eval_gold.py                      # uses eval/sample_unlabeled.jsonl + eval/labels_v1.txt
    python eval_gold.py --recompute-v2       # re-tag titles with the CURRENT story_logic.event_v2 (after rule changes)

Reports: event-tag accuracy, precision when the system does tag something, recall of real events, opinion detection,
FinBERT direction accuracy, feed-ticker vs company-name attribution. Split into dev (first half) and holdout (second
half) so rule tuning on dev errors can be checked on items the rules never saw. Labels are by an LLM: see labels file.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import story_logic as sl  # noqa: E402

HERE = Path(__file__).parent / "eval"
NOISE = {"other", "opinion", "insider", "geopolitics"}


def load(recompute=False, n=1):
    sample = {}
    sfile, lfile = ("sample_unlabeled.jsonl", "labels_v1.txt") if n == 1 else (f"sample{n}_unlabeled.jsonl", f"labels_v{n}.txt")
    for line in open(HERE / sfile, encoding="utf-8"):
        d = json.loads(line)
        sample[d["i"]] = d
    gold = []
    for line in open(HERE / lfile, encoding="utf-8"):
        if not line.strip() or line.startswith("#"):
            continue
        i, ev, about, dr, rel = line.rstrip("\n").split("|")
        d = dict(sample[int(i)])
        d.update(g_event=ev, g_about=about, g_dir=int(dr), g_rel=int(rel))
        if recompute:
            import re
            m = re.search(r"Items?:\s*([0-9., ]+)", d.get("summary") or "")
            items = re.findall(r"\d\.\d\d", m.group(1)) if m else None
            if not items:
                m2 = re.findall(r"\b(\d\.\d\d)\b", d["title"])
                items = m2 or None
            d["pred_event_v2"] = sl.event_v2(d["title"], d.get("summary"), items)
        gold.append(d)
    rp = HERE / f"reader_pred_set{n}.json"
    if rp.exists():
        pred = json.load(open(rp, encoding="utf-8"))["pred"]
        for d in gold:
            r = pred.get(str(d["i"]))
            if r:
                d.update(r_event=r["event"], r_dir=r["direction"], r_rel=r["relevant"], r_about=r["about"])
    return gold


def event_report(rows, key, label):
    n = len(rows)
    acc = sum(r[key] == r["g_event"] for r in rows) / n
    tagged = [r for r in rows if r[key] not in (None, "other", "none")]
    prec = sum(r[key] == r["g_event"] for r in tagged) / len(tagged) if tagged else float("nan")
    real = [r for r in rows if r["g_event"] not in NOISE]
    rec = sum(r[key] == r["g_event"] for r in real) / len(real) if real else float("nan")
    cov = len(tagged) / n
    return f"{label:<14} accuracy {acc:5.0%} | coverage(non-other) {cov:5.0%} | precision when tagged {prec:5.0%} | recall of real events {rec:5.0%}  (n={n}, real events={len(real)})"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recompute-v2", action="store_true")
    ap.add_argument("--errors", type=int, default=0, help="print v2 errors")
    ap.add_argument("--set", type=int, default=1, help="gold set number (1 = tuned on, 2 = fresh blind test)")
    a = ap.parse_args()
    gold = load(a.recompute_v2, a.set)
    dev, hold = gold[: len(gold) // 2], gold[len(gold) // 2:]
    for name, rows in (("ALL", gold), ("DEV (tune here)", dev), ("HOLDOUT (report)", hold)):
        print(f"\n=== {name}")
        for r in rows:
            r["old"] = r["pred_event_old"] or "none"
        print(event_report(rows, "old", "old classifier"))
        print(event_report(rows, "pred_event_v2", "v2"))
        rr = [r for r in rows if "r_event" in r]
        if rr:
            print(event_report(rr, "r_event", "LLM reader"))
            dn = [r for r in rr if r["g_dir"] != 0]
            if dn:
                print(f"LLM reader direction: {sum(r['r_dir'] == r['g_dir'] for r in dn) / len(dn):.0%} exact on {len(dn)} directional items; "
                      f"sign-correct when it takes a side: {sum(1 for r in dn if r['r_dir'] != 0 and r['r_dir'] == r['g_dir'])}/{sum(1 for r in dn if r['r_dir'] != 0)}")
            print(f"LLM reader relevance agreement: {sum(r['r_rel'] == r['g_rel'] for r in rr) / len(rr):.0%}; "
                  f"company match: {sum(1 for r in rr if r['g_about'] and r['r_about'].split('.')[0] == r['g_about'].split('.')[0])}/{sum(1 for r in rr if r['g_about'])}")
        op_t = [r for r in rows if r["g_event"] == "opinion"]
        op_p = [r for r in rows if r["pred_event_v2"] == "opinion"]
        tp = sum(r["pred_event_v2"] == "opinion" for r in op_t)
        prec = sum(r["g_event"] == "opinion" for r in op_p) / len(op_p) if op_p else float("nan")
        print(f"opinion detection (v2): recall {tp / len(op_t):.0%} of {len(op_t)}, precision {prec:.0%} of {len(op_p)}")
        dirs = [r for r in rows if r["g_dir"] != 0 and r.get("pred_fb") is not None and abs(r["pred_fb"]) >= 0.15]
        if dirs:
            ok = sum((r["pred_fb"] > 0) == (r["g_dir"] > 0) for r in dirs)
            print(f"FinBERT direction (|score|>=0.15 on {len(dirs)} directional items): {ok / len(dirs):.0%} correct")
        about = [r for r in rows if r["g_about"]]
        feed_ok = sum(r["g_about"].split(".")[0] in [t.split(".")[0] for t in r["feed_tickers"]] for r in about if r["feed_tickers"])
        feed_n = sum(1 for r in about if r["feed_tickers"])
        print(f"feed-ticker tags contain the true company: {feed_ok}/{feed_n} of items that have both")
        rel_low = [r for r in rows if r["g_rel"] == 0]
        print(f"irrelevant items in sample: {len(rel_low)}/{len(rows)} ({len(rel_low) / len(rows):.0%})")
    if a.errors:
        print("\n=== v2 errors (dev only)")
        for r in dev:
            if r["pred_event_v2"] != r["g_event"]:
                print(f"  gold={r['g_event']:<12} v2={r['pred_event_v2']:<12} {r['title'][:85]}")
        print("=== v2 errors (holdout)")
        for r in hold:
            if r["pred_event_v2"] != r["g_event"]:
                print(f"  gold={r['g_event']:<12} v2={r['pred_event_v2']:<12} {r['title'][:85]}")


if __name__ == "__main__":
    main()
