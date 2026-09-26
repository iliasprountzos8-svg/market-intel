"""Does the news signal predict anything? Tests every scoring model in snapshots2 (lex, finbert,
ens, and 'old' = the original rule-based labels) against REAL forward returns.

One observation per (symbol, day): the last snapshot before ~20:00 UTC (before the US close);
forward returns from that day's close over 1/3/5 trading days, raw and market-adjusted (minus
S&P 500). Reports Spearman rank correlation, an approximate p-value and the top-third minus
bottom-third spread. Windows overlap for 3d/5d, so p-values are optimistic, and testing many
models x horizons produces false positives by chance: any "signal" here is a hypothesis for the
forward paper ledger (lab book news_*), never proof.

Read-only. Run: python signal_eval.py [--notify]
"""

import argparse
import json
import math
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

sys.path.append(str(Path(__file__).parent))
import signals  # noqa: E402

load_dotenv()
ROOT = Path(__file__).parent.parent
PRICE_SYM = {"OIL": "CL=F", "GOLD": "GC=F", "USD": "DX-Y.NYB", "MARKET": "^GSPC", "RATES": "^TNX"}
MODELS = ("lex", "finbert", "ens", "old")


def spearman(x, y):
    if len(x) < 8:
        return None, None, len(x)
    r = float(pd.Series(x).rank().corr(pd.Series(y).rank()))
    n = len(x)
    if abs(r) >= 1:
        return r, 0.0, n
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return r, 2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2)))), n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--min-stories", type=int, default=3)
    ap.add_argument("--notify", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(signals.DB_PATH, timeout=120)
    snaps = pd.read_sql_query("select ts, symbol, model, idx, z, n_lead from snapshots2 where idx is not null", con)
    snaps["ts"] = pd.to_datetime(snaps["ts"], utc=True, format="ISO8601")
    snaps = snaps[(snaps["ts"] >= datetime.now(timezone.utc) - timedelta(days=args.days)) & (snaps["ts"].dt.hour <= 20) & (snaps["n_lead"] >= args.min_stories)]
    snaps["date"] = snaps["ts"].dt.tz_localize(None).dt.normalize()
    daily = snaps.sort_values("ts").groupby(["symbol", "date", "model"]).tail(1)
    wide = daily.pivot_table(index=["symbol", "date"], columns="model", values=["idx", "z"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reset_index()

    tickers = sorted(set(wide["symbol"]))
    closes = {}
    data = yf.download([PRICE_SYM.get(t, t) for t in tickers] + ["^GSPC"], period="3mo", interval="1d", auto_adjust=True, group_by="ticker", progress=False, threads=True)
    for t in tickers + ["_SPX"]:
        ps = "^GSPC" if t == "_SPX" else PRICE_SYM.get(t, t)
        try:
            h = data[ps]["Close"].dropna()
            h.index = h.index.tz_localize(None).normalize()
            closes[t] = h
        except Exception:  # noqa: BLE001
            pass
    spx = closes.get("_SPX")
    obs = []
    for _, row in wide.iterrows():
        c = closes.get(row["symbol"])
        if c is None or row["date"] not in c.index:
            continue
        i = c.index.get_loc(row["date"])
        rec = row.to_dict()
        for h in (1, 3, 5):
            if i + h < len(c):
                fr = c.iloc[i + h] / c.iloc[i] - 1
                rec[f"r{h}"] = fr * 100
                if spx is not None and row["date"] in spx.index and spx.index.get_loc(row["date"]) + h < len(spx):
                    j = spx.index.get_loc(row["date"])
                    rec[f"x{h}"] = (fr - (spx.iloc[j + h] / spx.iloc[j] - 1)) * 100
        obs.append(rec)
    df = pd.DataFrame(obs)
    lines = [f"Signal evaluation: {len(df)} (symbol, day) observations, {df['symbol'].nunique() if len(df) else 0} symbols, last {args.days}d, >= {args.min_stories} stories"]
    summary = {}
    for model in MODELS:
        for col, label in ((f"idx_{model}", "index"), (f"z_{model}", "z")):
            if col not in df:
                continue
            for h in (1, 3, 5):
                for pre, kind in (("r", "raw"), ("x", "vs S&P")):
                    if f"{pre}{h}" not in df:
                        continue
                    sub = df.dropna(subset=[col, f"{pre}{h}"])
                    rho, p, n = spearman(sub[col].tolist(), sub[f"{pre}{h}"].tolist())
                    if rho is None:
                        continue
                    hi, lo = sub[col] >= sub[col].quantile(2 / 3), sub[col] <= sub[col].quantile(1 / 3)
                    spread = sub.loc[hi, f"{pre}{h}"].mean() - sub.loc[lo, f"{pre}{h}"].mean()
                    lines.append(f"{model:8s} {label:5s} {h}d {kind:6s} rho={rho:+.2f} p~{p:.2f} n={n:<4d} top-third minus bottom-third = {spread:+.2f}%")
                    summary[f"{model}|{label}|{h}d|{kind}"] = {"rho": rho, "p": p, "n": n, "spread": spread}
    tests = len(summary)
    sig = [k for k, v in summary.items() if v["p"] < 0.05 and v["n"] >= 30]
    expected_false = tests * 0.05
    lines.append(f"VERDICT: {len(sig)} of {tests} tests have p<0.05 (about {expected_false:.0f} would be expected by chance alone; windows overlap so p-values are optimistic). "
                 + ("Nothing beyond chance yet." if len(sig) <= expected_false + 1 else "Possible signal in: " + ", ".join(sig[:5]) + " - a hypothesis for the forward paper ledger, not proof."))
    print("\n".join(lines))
    (ROOT / "logs").mkdir(exist_ok=True)
    (ROOT / "logs" / "signal-eval.json").write_text(json.dumps({"generated": datetime.now(timezone.utc).isoformat(), "report": "\n".join(lines), "summary": summary}, default=str, indent=1))
    if args.notify and os.environ.get("NTFY_SERVER"):
        best = max(summary.items(), key=lambda kv: abs(kv[1]["rho"])) if summary else None
        try:
            urllib.request.urlopen(urllib.request.Request(os.environ["NTFY_SERVER"].rstrip("/"), data=json.dumps({
                "topic": os.environ["NTFY_TOPIC"], "title": "Market Intel: does the signal work?", "priority": 3, "tags": ["microscope"],
                "message": lines[-1] + (f" Best: {best[0]} rho={best[1]['rho']:+.2f} n={best[1]['n']}" if best else "")}).encode(),
                headers={"Content-Type": "application/json"}), timeout=10).read()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    main()
