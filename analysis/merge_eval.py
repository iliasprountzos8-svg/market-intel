"""Precision/recall of story-merge rules against labeled pairs (eval/pairs_sampleN.jsonl + eval/pairs_vN.txt).

    python merge_eval.py 1            # threshold sweep on set 1
    python merge_eval.py 1 --at 0.72  # detail at one threshold, listing wrong merges and misses
Rules compared: cosine only; cosine + guards (time/companies/numbers); cosine + guards + "not opinion/listicle" (both stories'
event tags != opinion). A wrong merge hides a real story, so precision is what we optimise; recall is the bonus.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent / "eval"


def load(n):
    pairs = [json.loads(l) for l in open(HERE / f"pairs_sample{n}.jsonl", encoding="utf-8")]
    lab = {}
    for line in open(HERE / f"pairs_v{n}.txt", encoding="utf-8"):
        if line.strip() and not line.startswith("#"):
            i, y = line.strip().split("|")
            lab[int(i)] = y == "Y"
    sys.path.insert(0, str(Path(__file__).parent))
    import embed_logic as el
    import story_logic as sl
    for p in pairs:
        p["y"] = lab[p["i"]]
        p["ea"], p["eb"] = sl.event_v2(p["a"]), sl.event_v2(p["b"])  # current rules, so tag changes are evaluated too
        if "pa" in p:  # recompute the guards with the CURRENT code so rule changes are evaluated against the same labels
            a = {"title": p["a"], "tickers": set(p["ta"]), "ents": set(p["xa"]), "first_pub": p["pa"]}
            b = {"title": p["b"], "tickers": set(p["tb"]), "ents": set(p["xb"]), "first_pub": p["pb"]}
            p["gate"] = el.gates(a, b)[1]
    return pairs


RULES = {
    "cosine only": lambda p, t: p["cos"] >= t,
    "cos + guards": lambda p, t: p["cos"] >= t and p["gate"] == "ok",
    "cos + guards + not-opinion": lambda p, t: p["cos"] >= t and p["gate"] == "ok" and p.get("ea") != "opinion" and p.get("eb") != "opinion",
}


def stats(pairs, rule, t):
    tp = sum(1 for p in pairs if rule(p, t) and p["y"])
    fp = sum(1 for p in pairs if rule(p, t) and not p["y"])
    fn = sum(1 for p in pairs if not rule(p, t) and p["y"])
    prec = tp / (tp + fp) if tp + fp else float("nan")
    rec = tp / (tp + fn) if tp + fn else float("nan")
    return tp, fp, fn, prec, rec


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    pairs = load(n)
    print(f"{len(pairs)} labeled pairs, {sum(p['y'] for p in pairs)} truly the same event")
    if "--at" in sys.argv:
        t = float(sys.argv[sys.argv.index("--at") + 1])
        rule = RULES[sys.argv[sys.argv.index("--rule") + 1]] if "--rule" in sys.argv else RULES["cos + guards + not-opinion"]
        tp, fp, fn, prec, rec = stats(pairs, rule, t)
        print(f"threshold {t}: precision {prec:.0%} ({tp}/{tp + fp}), recall {rec:.0%} ({tp}/{tp + fn})")
        for p in pairs:
            if rule(p, t) and not p["y"]:
                print(f"  WRONG MERGE cos {p['cos']} [{p['gate']}]: {p['a'][:70]}  ##  {p['b'][:70]}")
        for p in pairs:
            if not rule(p, t) and p["y"]:
                print(f"  missed      cos {p['cos']} [{p['gate']}]: {p['a'][:70]}  ##  {p['b'][:70]}")
        return
    for name, rule in RULES.items():
        print(f"\n{name}")
        for t in (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
            tp, fp, fn, prec, rec = stats(pairs, rule, t)
            print(f"  cos>={t:.2f}: precision {prec:>4.0%} ({tp:>2}/{tp + fp:<2})  recall {rec:>4.0%} ({tp:>2}/{tp + fn:<2})")


if __name__ == "__main__":
    main()
