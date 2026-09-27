"""Run the full market-intel improvement cycle in one command:
  scrape -> backfill full text -> correlate tickers -> check call outcomes

Each step runs in its own subprocess so one step's failure (e.g. a flaky feed)
doesn't take down the others -- the script reports per-step pass/fail and
keeps going. This replaces manually invoking scrape.py / fetch_fulltext.py /
correlate.py / check_outcomes.py in sequence.

Note: this does NOT run the AI analysis pass (mark-processed / write-digest).
That stays a deliberate, on-demand, human-in-the-loop step -- see README
"3. On-demand analysis". This script only handles the parts that are safe and
cheap to fully automate.

Env vars required (loaded from scraper/.env and analysis/.env via the
sub-scripts' own dotenv calls -- nothing extra needed here):
  SUPABASE_URL
  SUPABASE_SERVICE_KEY

Run: python run_cycle.py
"""

import os
import subprocess
import sys
import time
from pathlib import Path

from logger import get_logger
log = get_logger("run_cycle")

ROOT = Path(__file__).parent
IS_WINDOWS = sys.platform == "win32"


def venv_python(venv_dir: Path) -> str:
    """Prefer the step's own virtualenv interpreter (has its deps installed);
    fall back to the system python if no venv exists yet, e.g. in CI."""
    candidate = venv_dir / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
    return str(candidate) if candidate.exists() else sys.executable


SCRAPER_DIR = ROOT / "scraper"
ANALYSIS_DIR = ROOT / "analysis"
SCRAPER_PY = venv_python(SCRAPER_DIR / ".venv")
ANALYSIS_PY = venv_python(ANALYSIS_DIR / ".venv")
INGEST_DIR = ROOT / "ingest"
INGEST_PY = venv_python(INGEST_DIR / ".venv")
NLP_DIR = ROOT / "nlp"
NLP_PY = venv_python(NLP_DIR / ".venv")

STEPS = [
    ("scrape", SCRAPER_DIR, [SCRAPER_PY, "scrape.py"]),
    ("ingest", INGEST_DIR, [INGEST_PY, "ingest.py"]),
    ("sec8k", INGEST_DIR, [INGEST_PY, "sec8k.py"]),
    ("sec_form4", INGEST_DIR, [INGEST_PY, "sec_form4.py"]),
    ("stories", ANALYSIS_DIR, [ANALYSIS_PY, "stories.py"]),
    ("merge", ANALYSIS_DIR, [NLP_PY, "merge_stories.py"]),
    ("fetch_fulltext", SCRAPER_DIR, ["timeout", "300", SCRAPER_PY, "fetch_fulltext.py", "--limit", "100", "--delay", "0.3", "--max-seconds", "180"]),  # bounded: slow sites must not hold up the cycle
    ("classify", ANALYSIS_DIR, [ANALYSIS_PY, "cli.py", "bulk-classify", "--rule-based", "--limit", "4000"]),
    ("finbert", NLP_DIR, [NLP_PY, "score_finbert.py", "--max-minutes", "15", "--threads", "4"]),
    ("populate", ANALYSIS_DIR, [ANALYSIS_PY, "populate_cells.py", "--hours", "72"]),
    ("form4_signal", ANALYSIS_DIR, [ANALYSIS_PY, "form4_signal.py"]),
    ("signals", ANALYSIS_DIR, [ANALYSIS_PY, "signals.py"]),
    ("correlate", ANALYSIS_DIR, [ANALYSIS_PY, "correlate.py", "--window-hours", "72"]),
    ("check_outcomes", ANALYSIS_DIR, [ANALYSIS_PY, "check_outcomes.py", "--min-age-days", "3", "--move-threshold", "0.5"]),
    ("notify", ANALYSIS_DIR, [ANALYSIS_PY, "notify.py"]),
]

# signals.py keeps its own SQLite store and pushes to ntfy: only meaningful on the homelab, so it is
# opt-in (MI_ENABLE_SIGNALS=1) and skipped on GitHub Actions.
if os.environ.get("MI_ENABLE_SIGNALS") != "1":
    STEPS = [st for st in STEPS if st[0] not in ("signals", "ingest", "sec8k", "sec_form4", "stories", "merge", "finbert", "mirror", "populate", "form4_signal")]

# sec_form4 / form4_signal are new (2026-09-27): proven via manual dry-run, not yet turned on in
# the live 30-min cycle (a second full SEC submissions poll per company plus a per-filing XML
# fetch, and a new DB table growing every cycle -- a real behavior change worth a deliberate
# opt-in, separate from the already-live MI_ENABLE_SIGNALS steps above). Set MI_ENABLE_FORM4=1
# once the dry-run output looks right to fold these into the regular cycle.
if os.environ.get("MI_ENABLE_FORM4") != "1":
    STEPS = [st for st in STEPS if st[0] not in ("sec_form4", "form4_signal")]


def run_step(name: str, cwd: Path, cmd: list[str]) -> bool:
    log.info(f"Starting step: {name}")
    start = time.time()
    result = subprocess.run(cmd, cwd=cwd, text=True)
    elapsed = time.time() - start
    timeboxed = name == "fetch_fulltext" and result.returncode == 124  # designed time-box, not a failure
    ok = result.returncode == 0 or timeboxed
    status = "TIME-BOXED (ok)" if timeboxed else ("OK" if ok else f"FAILED (exit {result.returncode})")
    log.info(f"Finished step: {name} - {status} ({elapsed:.1f}s)")
    return ok


def main():
    results = {}
    for name, cwd, cmd in STEPS:
        results[name] = run_step(name, cwd, cmd)

    log.info("Cycle summary:")
    for name, ok in results.items():
        log.info(f"  {'OK  ' if ok else 'FAIL'}  {name}")

    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
