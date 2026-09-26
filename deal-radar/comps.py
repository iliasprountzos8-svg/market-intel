"""Turn a listings CSV into per-model comps and (optionally) write them into config.json.

Asking prices are NOT sold prices. resale = median asking * rules.ask_to_sold (an ASSUMED discount,
to be replaced once real sold prices are logged). Models flagged "manual": true in config are only reported.

Usage:
    python comps.py export.csv older.csv               # report only (later files add only listings not already seen)
    python comps.py export.csv older.csv --write       # update config.json comps
"""
import csv
import json
import statistics
import sys
from datetime import date
from pathlib import Path

import score

KEY_MAP = {
    "se1_44": ("apple_watch_se1_44", "Apple Watch SE 1st gen 44mm (2020)"),
    "se2": ("apple_watch_se2", "Apple Watch SE 2nd gen (40/44mm)"),
    "s6": ("apple_watch_s6", "Apple Watch Series 6 (40/44mm)"),
    "s7": ("apple_watch_s7", "Apple Watch Series 7 (41/45mm)"),
    "s8": ("apple_watch_s8", "Apple Watch Series 8 (41/45mm)"),
}


def load_listings(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def merge_listings(paths):
    """Concatenate several listing files. A row from a later file is dropped when an earlier file already has
    a row with the same (model, size, ask, was, local/remote): the same listing seen twice. Duplicates
    inside one file are kept (two different sellers can ask the same price)."""
    rows, seen = [], set()
    for path in paths:
        new_keys = set()
        for r in load_listings(path):
            k = (r["model_key"], r.get("size_mm", ""), r["ask"], r.get("was", ""), r["location"].startswith("REMOTE"))
            if k in seen:
                continue
            new_keys.add(k)
            rows.append(r)
        seen |= new_keys
    return rows


def quantile(vals, q):
    v = sorted(vals)
    return v[round((len(v) - 1) * q)]


def is_remote(row):
    return row["location"].startswith("REMOTE")


def build(rows, ask_to_sold, remote=False):
    out = {}
    for raw_key, (key, label) in KEY_MAP.items():
        sel = [r for r in rows if r["model_key"] == raw_key and r["ask"] and is_remote(r) == remote]
        if not sel:
            continue
        asks = [float(r["ask"]) for r in sel]
        cuts = [(1 - float(r["ask"]) / float(r["was"])) for r in sel if r["was"]]
        out[key] = {
            "label": label, "n": len(asks), "min": min(asks), "median": statistics.median(asks), "max": max(asks),
            "resale_median": round(statistics.median(asks) * ask_to_sold),
            "resale_low": round(quantile(asks, 0.25) * ask_to_sold),
            "resale_high": round(quantile(asks, 0.75) * ask_to_sold),
            "share_cut": len(cuts) / len(sel), "avg_cut": statistics.fmean(cuts) if cuts else 0.0,
        }
    return out


def main(argv):
    paths = [a for a in argv if not a.startswith("--")]
    if not paths:
        print(__doc__); return 1
    cfg = score.load_config()
    a2s = cfg["rules"]["ask_to_sold"]
    rows = merge_listings(paths)
    print(f"{len(rows)} listings from {len(paths)} file(s)")
    stats = build(rows, a2s)
    print("LOCAL (Thessaloniki, within 65 km): these feed config.json")
    print(f"{'model':<24}{'n':>3} {'ask min/med/max':>18} {'cut%':>6} {'avg cut':>8}   resale est (low/med/high)")
    for key, s in stats.items():
        print(f"{key:<24}{s['n']:>3} {s['min']:>6.0f}/{s['median']:>4.0f}/{s['max']:>4.0f} {s['share_cut']:>6.0%} {s['avg_cut']:>8.0%}   "
              f"{s['resale_low']}/{s['resale_median']}/{s['resale_high']}")
    print()
    print("REMOTE (rest of Greece, would need shipping): for comparison only")
    for key, s in build(rows, a2s, remote=True).items():
        print(f"{key:<24}{s['n']:>3} {s['min']:>6.0f}/{s['median']:>4.0f}/{s['max']:>4.0f} {s['share_cut']:>6.0%} {s['avg_cut']:>8.0%}")
    print()
    if "--write" in argv:
        today = date.today().isoformat()
        for key, s in stats.items():
            m = cfg["models"].setdefault(key, {"label": s["label"], "must_check": cfg["default_must_check"]})
            if m.get("manual"):
                m["comps_n"] = s["n"]; m["comps_updated"] = today
                m["comps_source"] = m.get("comps_source", "") + f" | {today}: {s['n']} Thessaloniki asking prices seen (median {s['median']:.0f}), estimate kept manually"
                continue
            m.update({"resale_median": s["resale_median"], "resale_low": s["resale_low"], "resale_high": s["resale_high"],
                      "comps_n": s["n"], "comps_updated": today,
                      "comps_source": f"ASSUMED: {s['n']} Facebook Marketplace Thessaloniki ASKING prices on {today}, median {s['median']:.0f} x {a2s}; no sold prices yet"})
        Path(score.CFG_PATH).write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print("config.json updated")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
