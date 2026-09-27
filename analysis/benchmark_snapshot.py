"""One dated snapshot of "how good is market-intel right now", aggregating existing metric
outputs so later changes (confidence-scored calibration, new signal sources, bias fixes, ...)
can be compared against a real baseline instead of guesswork.

Read-only: takes no pipeline actions, only reads the local Postgres (calibration, ledger,
data-quality tables) and whatever logs/*.json the other analysis/lab scripts already wrote on
their own schedule. Safe to run any time.

    python benchmark_snapshot.py            # write logs/benchmark/snapshot-<date>.{json,md}
    python benchmark_snapshot.py --print     # also print the markdown to stdout
"""
import json
import random
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
STALE_AFTER_DAYS = 8


def load_json_artifact(path, stale_after_days=STALE_AFTER_DAYS):
    """Read a logs/*.json artifact another script owns. Returns (data, note):
    data is None when the file is missing or too stale to trust; note explains why."""
    if not path.exists():
        return None, f"not yet generated (expected at {path})"
    age = datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)
    if age > timedelta(days=stale_after_days):
        return None, f"stale ({age.days}d old, older than {stale_after_days}d threshold) at {path}"
    try:
        return json.loads(path.read_text()), None
    except (json.JSONDecodeError, OSError) as e:
        return None, f"unreadable ({e}) at {path}"


def boot_ci(vals, n=2000, seed=7):
    """95% bootstrap CI of the mean. Same method as lab/ledger_report.py (not imported from
    there: that module runs its full docker-exec/report/notify pipeline as side effects of
    import, so the ~4-line CI routine is duplicated here instead)."""
    rnd = random.Random(seed)
    k = len(vals)
    means = sorted(statistics.fmean(rnd.choices(vals, k=k)) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def get_calibration(conn):
    sys.path.insert(0, str(Path(__file__).parent))
    import calibration as cal
    with conn.cursor() as cur:
        cur.execute("select confidence, outcome from ai_calls_log where outcome in ('correct','incorrect','unclear')")
        rows = cur.fetchall()
    pairs = [(c / 100.0, 1 if o == "correct" else 0) for c, o in rows if c is not None and o in ("correct", "incorrect")]
    return {
        "n_scoreable": len(pairs),
        "n_unclear": sum(1 for _, o in rows if o == "unclear"),
        "n_no_confidence": sum(1 for c, _ in rows if c is None),
        "brier": cal.brier(pairs),
        "bins": cal.bins(pairs),
        "min_n_for_signal": cal.MIN_N,
    }


def get_ledger(conn):
    query = ("select book, side, signal_date, avg(net_excess_pct), count(*) "
             "from lab_positions where status <> 'pending' and net_excess_pct is not null "
             "group by book, side, signal_date order by book, side, signal_date")
    with conn.cursor() as cur:
        cur.execute("select count(*) from lab_positions where status = 'pending'")
        pending = cur.fetchone()[0]
        cur.execute("select count(*) from lab_positions where status <> 'pending'")
        settled_positions = cur.fetchone()[0]
        cur.execute(query)
        rows = cur.fetchall()

    by = {}
    for book, side, _signal_date, mean_excess, _n in rows:
        by.setdefault((book, side), []).append(float(mean_excess))

    if not by:
        return {"pending_positions": pending, "settled_positions": settled_positions,
                "note": "nothing settled yet (first settlements expected ~2026-10-01)", "by_book_side": []}

    by_book_side = []
    for (book, side), vals in sorted(by.items()):
        n = len(vals)
        mean = statistics.fmean(vals)
        hit = sum(v > 0 for v in vals) / n
        entry = {"book": book, "side": side, "n_dates": n, "mean_net_excess_pct": mean, "hit_rate": hit}
        if n < 15:
            entry["ci95"] = None
            entry["verdict"] = "too few dates to say anything"
        else:
            lo, hi = boot_ci(vals)
            entry["ci95"] = [lo, hi]
            entry["verdict"] = ("CI above 0 (still needs multiple-test caution)" if lo > 0
                                 else "CI below 0" if hi < 0 else "no evidence of edge")
        by_book_side.append(entry)

    return {"pending_positions": pending, "settled_positions": settled_positions, "by_book_side": by_book_side}


def get_dq(conn):
    with conn.cursor() as cur:
        cur.execute("select name, value from dq_metrics where ts = (select max(ts) from dq_metrics)")
        rows = cur.fetchall()
    if not rows:
        return {"note": "no dq_metrics rows yet"}
    return {name: value for name, value in rows}


def build_snapshot():
    sys.path.insert(0, str(Path(__file__).parent))
    from db import connect

    track_record, tr_note = load_json_artifact(LOGS_DIR / "track-record.json")
    signal_eval, se_note = load_json_artifact(LOGS_DIR / "signal-eval.json")
    walkforward, wf_note = load_json_artifact(LOGS_DIR / "lab-walkforward-h5.json")

    with connect() as conn:
        calibration_section = get_calibration(conn)
        ledger_section = get_ledger(conn)
        dq_section = get_dq(conn)

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "calibration": calibration_section,
        "ledger": ledger_section,
        "data_quality": dq_section,
        "track_record": track_record if track_record is not None else {"note": tr_note},
        "signal_eval": (signal_eval or {}).get("summary") if signal_eval is not None else {"note": se_note},
        "walkforward": (walkforward or {}).get("results") if walkforward is not None else {"note": wf_note},
    }


def _fmt_bins(bins_):
    if not bins_:
        return "  (no bins — no scoreable calls)"
    return "\n".join(f"  stated {b['range']:<8} n={b['n']:<3} stated {b['stated']:.0%} vs actual {b['hit_rate']:.0%}" for b in bins_)


def to_markdown(snap):
    lines = [f"# Benchmark snapshot — {snap['generated']}", "", "Research only, not investment advice.", ""]

    c = snap["calibration"]
    lines += ["## Calibration (digest call confidence vs outcome)",
               f"- scoreable calls: {c['n_scoreable']} ({c['n_unclear']} unclear, {c['n_no_confidence']} no confidence logged)"]
    if c["brier"] is None:
        lines.append("- Brier score: N/A — no scoreable calls yet")
    else:
        lines.append(f"- Brier score: {c['brier']:.3f} (always-50% baseline 0.250)")
        lines.append(_fmt_bins(c["bins"]))
        if c["n_scoreable"] < c["min_n_for_signal"]:
            lines.append(f"- only {c['n_scoreable']} resolved calls: anecdote, not evidence (need {c['min_n_for_signal']}+)")
    lines.append("")

    lg = snap["ledger"]
    lines += ["## Paper ledger (forward, out-of-sample)",
               f"- settled positions: {lg['settled_positions']}, pending: {lg['pending_positions']}"]
    if not lg.get("by_book_side"):
        lines.append(f"- {lg.get('note', 'no data')}")
    else:
        lines += ["", "| book/side | dates | mean net excess % | hit rate | 95% CI | verdict |", "|---|---|---|---|---|---|"]
        for e in lg["by_book_side"]:
            ci = "n/a" if e["ci95"] is None else f"[{e['ci95'][0]:+.2f}, {e['ci95'][1]:+.2f}]"
            lines.append(f"| {e['book']}/{e['side']} | {e['n_dates']} | {e['mean_net_excess_pct']:+.2f} | {e['hit_rate']:.0%} | {ci} | {e['verdict']} |")
    lines.append("")

    dq = snap["data_quality"]
    lines.append("## Data quality (latest dq_metrics run)")
    if "note" in dq:
        lines.append(f"- {dq['note']}")
    else:
        for name, value in sorted(dq.items()):
            lines.append(f"- {name}: {value}")
    lines.append("")

    tr = snap["track_record"]
    lines.append("## Fair call track record")
    if "note" in tr:
        lines.append(f"- {tr['note']}")
    else:
        lines.append(f"- n_decided={tr.get('n_decided')} hits={tr.get('hits')} hit_rate={tr.get('hit_rate')} ci95={tr.get('ci95')} verdict={tr.get('verdict')}")
    lines.append("")

    se = snap["signal_eval"]
    lines.append("## Signal edge (sentiment predictiveness tests)")
    if isinstance(se, dict) and "note" in se:
        lines.append(f"- {se['note']}")
    elif se:
        sig = [k for k, v in se.items() if v.get("p") is not None and v["p"] < 0.05]
        lines.append(f"- {len(sig)}/{len(se)} tests significant at p<0.05 (~{len(se) * 0.05:.1f} expected by chance)")
        for k in sig:
            v = se[k]
            lines.append(f"  - {k}: rho={v['rho']:.3f} p={v['p']:.3f} n={v['n']}")
    else:
        lines.append("- no summary data")
    lines.append("")

    wf = snap["walkforward"]
    lines.append("## Walk-forward strategy quality (5-day horizon)")
    if isinstance(wf, dict) and "note" in wf:
        lines.append(f"- {wf['note']}")
    elif wf:
        for r in wf:
            if "note" in r:
                lines.append(f"- {r['name']}: {r['note']}")
            else:
                lines.append(f"- {r['name']}: IC={r['ic_mean']:.3f} (t={r['ic_t']:.1f}) net={r['spread_net_pct']:+.2f}% skill={r['skill']}")
    else:
        lines.append("- no results data")
    lines.append("")

    return "\n".join(lines)


def main():
    snap = build_snapshot()
    out_dir = LOGS_DIR / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    date_tag = datetime.now().strftime("%Y-%m-%d")
    json_path = out_dir / f"snapshot-{date_tag}.json"
    md_path = out_dir / f"snapshot-{date_tag}.md"

    json_path.write_text(json.dumps(snap, indent=2, default=str))
    md = to_markdown(snap)
    md_path.write_text(md)

    print(f"wrote {json_path}")
    print(f"wrote {md_path}")
    if "--print" in sys.argv:
        print()
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
