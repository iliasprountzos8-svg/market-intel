"""Daily prediction engine + paper-trading ledger (runs after the US close).

For each "book" (mom, rev, lowvol, hgb price-only ML, news_ens = the news-sentiment signal) it
ranks the S&P 500, records the top-10 picks (and bottom-10 "avoid" list) and opens PAPER positions:
entry at the next trading day's close, exit 5 trading days later, compared with SPY over the same
window, minus 10 bps per side. Nothing here trades real money; the ledger is the honest, forward
(out-of-sample) track record of every idea, including the news signal.

Run: python lab_daily.py [--notify] [--no-prices]
"""
import argparse
import json
import os
import sys
import urllib.request
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import lab_data as ld  # noqa: E402
import lab_wf  # noqa: E402
sys.path.insert(0, str(Path(__file__).parent.parent / "analysis"))
from db import connect  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = ld.ROOT
H = 5
TOP = 10
COST_PCT = 0.2  # 10 bps per side, round trip
DDL = """
create table if not exists lab_predictions (pred_date date, book text, horizon int, symbol text, score real, rank int, primary key (pred_date, book, horizon, symbol));
create table if not exists lab_positions (book text, side text, symbol text, signal_date date, entry_date date, entry_px real, exit_date date, exit_px real,
  ret_pct real, spy_ret_pct real, excess_pct real, net_excess_pct real, horizon int, status text default 'pending', primary key (book, side, symbol, signal_date));
create table if not exists lab_report (as_of date primary key, report jsonb);
"""


def scores_at(panel, R, latest, ny):
    """Score every symbol at `latest` for each book."""
    day = R.xs(latest, level="date")
    books = {"mom": day["r_mom_12_1"], "rev": -day["r_r5"], "lowvol": -day["r_vol60"]}
    mp = ld.DATA / "models" / f"hgb_h{H}.joblib"
    if mp.exists():
        m = joblib.load(mp)
        ok = day.dropna(subset=m["features"])
        books["hgb"] = pd.Series(m["model"].predict(ok[m["features"]].values), index=ok.index)
    n = ny
    if n is not None and not n.empty:
        try:
            nd = n.xs(latest, level="date")
            for model in ("ens", "finbert", "lex", "old"):
                col = f"n_z_{model}" if f"n_z_{model}" in nd else (f"n_idx_{model}" if f"n_idx_{model}" in nd else None)
                if col and nd[col].notna().sum() >= 50:
                    books[f"news_{model}"] = nd[col].dropna()
        except KeyError:
            pass
    return books


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    ap.add_argument("--no-prices", action="store_true")
    args = ap.parse_args()
    if not args.no_prices:
        ld.update_prices()
    close, vol = ld.load_wide()
    panel = ld.build_panel(close, vol)
    R = lab_wf.ranked(panel)
    latest = R.dropna().index.get_level_values("date").max()
    news = ld.news_features(panel.index)
    books = scores_at(panel, R, latest, news if len(news) else None)
    dates = close.index
    with connect() as conn:
        conn.execute(DDL)
        # 1. record predictions and open pending paper positions (top = long, bottom = avoid/short)
        for book, s in books.items():
            s = s.dropna().sort_values(ascending=False)
            if len(s) < 50:
                continue
            rows = [(latest.date(), book, H, sym, float(v), int(i + 1)) for i, (sym, v) in enumerate(s.items()) if i < 25 or i >= len(s) - 25]
            with conn.cursor() as cur:
                cur.executemany("insert into lab_predictions values (%s,%s,%s,%s,%s,%s) on conflict do nothing", rows)
                pos = [(book, "long", sym, latest.date(), H) for sym in s.index[:TOP]] + [(book, "avoid", sym, latest.date(), H) for sym in s.index[-TOP:]]
                cur.executemany("insert into lab_positions (book, side, symbol, signal_date, horizon) values (%s,%s,%s,%s,%s) on conflict do nothing", pos)
        # 2. settle pending positions whose windows have completed
        pend = conn.execute("select book, side, symbol, signal_date, horizon from lab_positions where status='pending'").fetchall()
        for book, side, sym, sd, h in pend:
            sd = pd.Timestamp(sd)
            if sym not in close.columns:
                continue
            after = dates[dates > sd]
            if len(after) < 1 + h:
                continue  # entry (next close) or exit not available yet
            e_d, x_d = after[0], after[h]
            p0, p1 = float(close.loc[e_d, sym]), float(close.loc[x_d, sym])
            s0, s1 = float(close.loc[e_d, "SPY"]), float(close.loc[x_d, "SPY"])
            if not all(np.isfinite([p0, p1, s0, s1])) or p0 <= 0 or s0 <= 0:
                continue  # missing price: leave pending rather than record garbage
            ret, spy = (p1 / p0 - 1) * 100, (s1 / s0 - 1) * 100
            excess = ret - spy if side == "long" else spy - ret  # 'avoid' scored as a short vs SPY
            conn.execute("update lab_positions set entry_date=%s, entry_px=%s, exit_date=%s, exit_px=%s, ret_pct=%s, spy_ret_pct=%s, excess_pct=%s, net_excess_pct=%s, status='closed' "
                         "where book=%s and side=%s and symbol=%s and signal_date=%s", (e_d.date(), p0, x_d.date(), p1, ret, spy, excess, excess - COST_PCT, book, side, sym, sd.date()))
        conn.commit()
        # 3. report
        rep = {}
        for book, side, n, mean_ex, net, hit, first, last in conn.execute(
                """select book, side, count(*), avg(excess_pct), avg(net_excess_pct), avg((excess_pct>0)::int), min(signal_date), max(signal_date)
                   from lab_positions where status='closed' group by 1,2 order by 1,2""").fetchall():
            rep[f"{book}/{side}"] = {"closed_positions": n, "mean_excess_pct": float(mean_ex), "mean_net_excess_pct": float(net), "hit_rate": float(hit), "from": str(first), "to": str(last)}
        top = {b: [str(x) for x in s.dropna().sort_values(ascending=False).index[:5]] for b, s in books.items()}
        opened = conn.execute("select count(*) from lab_positions where status='pending'").fetchone()[0]
        out = {"as_of": str(latest.date()), "books": list(books), "top5": top, "open_paper_positions": opened, "ledger": rep,
               "note": "paper trading only; skill requires many settled positions with positive NET excess vs SPY, see walk-forward files"}
        conn.execute("insert into lab_report values (%s,%s) on conflict (as_of) do update set report=excluded.report", (latest.date(), json.dumps(out)))
        conn.commit()
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "lab-daily.json").write_text(json.dumps(out, indent=1))
    lines = [f"Lab as of {out['as_of']}: {len(books)} books, {opened} open paper positions"]
    for b, t in top.items():
        lines.append(f"  {b:9s} top5: {', '.join(t)}")
    for k, v in rep.items():
        lines.append(f"  settled {k:14s} n={v['closed_positions']:<4d} net excess vs SPY {v['mean_net_excess_pct']:+.2f}%  hit {v['hit_rate']:.0%}")
    print("\n".join(lines))
    if args.notify and os.environ.get("NTFY_SERVER"):
        try:
            urllib.request.urlopen(urllib.request.Request(os.environ["NTFY_SERVER"].rstrip("/"), data=json.dumps({
                "topic": os.environ["NTFY_TOPIC"], "title": "Lab: daily ranked ideas (paper only)", "priority": 2, "tags": ["test_tube"],
                "message": "\n".join(lines[:8])[:900] + "\nResearch/paper-trading only, not advice."}).encode(), headers={"Content-Type": "application/json"}), timeout=10).read()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
