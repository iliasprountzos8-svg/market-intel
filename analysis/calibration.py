"""Calibration scorecard for the digest's calls: does stated confidence match how often calls turn out right?

    python calibration.py            # print the scorecard
Uses ai_calls_log rows that carry a confidence (0-100) and a resolved outcome ('correct' or 'incorrect'; 'unclear' is
skipped). Brier score: mean of (confidence/100 - outcome)^2, lower is better; 0.25 is what always saying 50% scores.
Below 30 resolved calls the numbers are anecdote and the script says so.
"""
import sys
from pathlib import Path

MIN_N = 30


def brier(pairs):
    """pairs: [(probability 0..1, outcome 0/1)] -> mean squared error, or None if empty."""
    return sum((p - o) ** 2 for p, o in pairs) / len(pairs) if pairs else None


def bins(pairs, edges=(0.0, 0.4, 0.6, 0.8, 1.01)):
    out = []
    for lo, hi in zip(edges, edges[1:]):
        sel = [(p, o) for p, o in pairs if lo <= p < hi]
        if sel:
            out.append({"range": f"{lo:.0%}-{min(hi, 1.0):.0%}", "n": len(sel), "stated": sum(p for p, _ in sel) / len(sel),
                        "hit_rate": sum(o for _, o in sel) / len(sel)})
    return out


def report(pairs, n_unclear=0, n_no_conf=0):
    lines = [f"resolved calls with a confidence: {len(pairs)} ({n_unclear} unclear, {n_no_conf} without confidence skipped)"]
    if not pairs:
        lines.append("no scoreable calls yet: new digests must log confidence and a horizon (analysis/cli.py log-call).")
        return "\n".join(lines)
    b = brier(pairs)
    lines.append(f"Brier score {b:.3f} (always-50% baseline 0.250)")
    for r in bins(pairs):
        lines.append(f"  stated {r['range']:<8} n={r['n']:<3} stated {r['stated']:.0%} vs actual {r['hit_rate']:.0%}")
    if len(pairs) < MIN_N:
        lines.append(f"only {len(pairs)} resolved calls: anecdote, not evidence (need {MIN_N}+).")
    return "\n".join(lines)


def main():
    sys.path.insert(0, str(Path(__file__).parent))
    from db import connect
    with connect() as conn, conn.cursor() as cur:
        cur.execute("select confidence, outcome from ai_calls_log where outcome in ('correct','incorrect','unclear')")
        rows = cur.fetchall()
    pairs = [(c / 100.0, 1 if o == "correct" else 0) for c, o in rows if c is not None and o in ("correct", "incorrect")]
    print(report(pairs, n_unclear=sum(1 for c, o in rows if o == "unclear"), n_no_conf=sum(1 for c, o in rows if c is None)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
