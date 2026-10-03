"""Weekly honest read of the forward paper ledger (research only, not advice).

Unit of observation = one signal date per (book, side): the average net excess return of that day's
picks. Overlapping 5-day positions are correlated, so counting positions would overstate the evidence.
Reports n dates, mean net excess vs SPY (after 10 bps costs), hit rate, and a 95% bootstrap CI.
With 8 books x 2 sides = 16 tests, ~1 CI excluding zero is expected by chance alone.
"""
import csv, io, os, random, statistics, subprocess, sys
from collections import defaultdict
from datetime import datetime

OUT = os.path.expanduser("~/market-intel/logs/ledger-report.md")
QUERY = ("select book, side, signal_date, avg(net_excess_pct), count(*), min(status) "
         "from lab_positions where status <> 'pending' and net_excess_pct is not null "
         "group by book, side, signal_date order by book, side, signal_date")

def sql(q):
    cmd = ["docker", "exec", "-i", "marketdb-db-1", "psql", "-U", "postgres", "-d", "marketintel", "-At", "-F", ",", "-c", q]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

def boot_ci(vals, n=2000, seed=7):
    rnd = random.Random(seed)
    k = len(vals)
    means = sorted(statistics.fmean(rnd.choices(vals, k=k)) for _ in range(n))
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]

pending = int(sql("select count(*) from lab_positions where status='pending'").strip() or 0)
settled_positions = int(sql("select count(*) from lab_positions where status<>'pending'").strip() or 0)
by = defaultdict(list)
for row in csv.reader(io.StringIO(sql(QUERY))):
    if len(row) >= 4:
        by[(row[0], row[1])].append(float(row[3]))

lines = [f"# Paper ledger read, {datetime.now():%Y-%m-%d %H:%M}",
         "Research only. Not investment advice. Paper positions, 5-day horizon, net of 10 bps costs, versus SPY.", "",
         f"Positions: {settled_positions} settled, {pending} pending.", ""]
summary = []
if not by:
    lines.append("Nothing has settled yet, so there is no result to read. The first positions settle about 2026-10-01.")
    summary.append(f"No settled positions yet ({pending} pending).")
else:
    lines.append("| book/side | dates | mean net excess % | hit rate | 95% CI | verdict |")
    lines.append("|---|---|---|---|---|---|")
    for (book, side), vals in sorted(by.items()):
        n = len(vals)
        mean = statistics.fmean(vals)
        hit = sum(v > 0 for v in vals) / n
        if n < 15:
            ci, verdict = "n/a", "too few dates to say anything"
        else:
            lo, hi = boot_ci(vals)
            ci = f"[{lo:+.2f}, {hi:+.2f}]"
            verdict = ("CI above 0 (still needs multiple-test caution)" if lo > 0
                       else "CI below 0" if hi < 0 else "no evidence of edge")
        lines.append(f"| {book}/{side} | {n} | {mean:+.2f} | {hit:.0%} | {ci} | {verdict} |")
        summary.append(f"{book}/{side}: n={n} {mean:+.2f}% {verdict}")
    lines += ["", "Caveats: 16 book/side tests so about one CI excluding 0 is expected by chance; the walk-forward universe has survivorship bias; "
              "dates within a week overlap. Treat anything here as a hypothesis until it holds over many weeks."]

open(OUT, "w").write("\n".join(lines) + "\n")
print("\n".join(lines))

if "--notify" in sys.argv:
    topic = open(os.path.expanduser("~/services/ntfy/topic.txt")).read().strip()
    body = "\n".join(summary[:12]) + "\nResearch only. See logs/ledger-report.md"
    subprocess.run(["curl", "-s", "-m", "10", "-H", "Title: Paper ledger weekly read", "-H", "Priority: min", "-H", "Tags: bar_chart",
                    "-d", body, f"http://100.83.128.73:8090/{topic}"], capture_output=True)
