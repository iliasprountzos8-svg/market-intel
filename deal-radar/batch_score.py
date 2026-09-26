"""Score every listing in a CSV against the rules and rank them.

Usage: python batch_score.py listings_2026-09-26.csv [--condition good] [--channel fb_local]
Rows without a known model_key are skipped. Condition is assumed (photos are not judged here).
"""
import argparse
import sys

import comps
import score

KEY_TO_CFG = {k: v[0] for k, v in comps.KEY_MAP.items()}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+", help="one or more listing files (first file wins on duplicates)")
    ap.add_argument("--condition", default="good")
    ap.add_argument("--channel", default="fb_local")
    ap.add_argument("--all", action="store_true", help="also show PASS rows")
    ap.add_argument("--battery", type=float, default=None, help="assume this battery health for every listing (stress test)")
    ap.add_argument("--remote", action="store_true", help="score the REMOTE listings (shipping, higher risk) instead of the local ones")
    a = ap.parse_args(argv)
    cfg = score.load_config()
    rows = []
    for r in comps.merge_listings(a.csv):
        key = KEY_TO_CFG.get(r["model_key"])
        if not key or key not in cfg["models"] or not r["ask"]:
            continue
        if comps.is_remote(r) != a.remote:
            continue
        res = score.score(cfg, key, float(r["ask"]), a.condition, a.channel, proof=False, price_cut=bool(r["was"]), remote=a.remote, battery=a.battery)
        rows.append((r, res))
    rows.sort(key=lambda x: -x[1]["profit_at_ask"])
    print(f"{'id':>3} {'model':<34} {'mm':>3} {'ask':>4} {'was':>4}  {'verdict':<10} {'ceiling':>7} {'offer':>5} {'profit@ask':>10}  flags")
    for r, res in rows:
        if res["verdict"] == "PASS" and not a.all:
            continue
        flags = ["too-good" if any("too good" in w for w in res["warnings"]) else "", "CHECK: " + r["note"] if r["note"] else ""]
        print(f"{r['id']:>3} {res['model'][:34]:<34} {r['size_mm']:>3} {r['ask']:>4} {r['was']:>4}  {res['verdict'][:10]:<10} "
              f"{res['max_buy']:>7.0f} {res['opening_offer']:>5} {res['profit_at_ask']:>10.0f}  {' '.join(f for f in flags if f)}")
        if r.get("url"):
            print(f"{'':>4}{r['url']}")
    print(f"\n{sum(1 for _, x in rows if x['verdict'].startswith('BUY'))} BUY, {sum(1 for _, x in rows if x['verdict']=='NEGOTIATE')} NEGOTIATE, "
          f"{sum(1 for _, x in rows if x['verdict']=='PASS')} PASS of {len(rows)} scored listings (PASS rows hidden unless --all)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
