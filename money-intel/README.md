# money-intel

Same idea as market-intel, pointed at your bank statement instead of the news wire: turn a pile
of noisy transactions into an honest signal (real recurring charges, a calibrated end-of-month
forecast) instead of a vague feeling about where the money goes. Self-hosted, free, runs on the
homelab's existing Postgres (`marketdb-db-1`).

No bank API, no Plaid-style credential sharing: you export a CSV from your bank's own site and
feed it in by hand.

## Setup (one-time, once a real statement is uploaded)

1. Drop the exported CSV somewhere under this folder, e.g. `money-intel/incoming/statement.csv`.
2. Run `python import_csv.py incoming/statement.csv --sniff` to see what columns it detected.
   Column names vary by bank; if the auto-detect guesses wrong, add an explicit mapping to
   `bank_config.json` (created on first run with a template) and re-run without `--sniff`.
3. `python import_csv.py incoming/statement.csv` to actually load it. Re-running on the same
   file is safe -- rows are deduped by (date, amount, normalized description).

## Day to day

```
python import_csv.py incoming/statement.csv   # load a fresh export
python categorize.py                          # rule-based categorization (free, no LLM)
python recurring.py                           # detect/refresh recurring charges
python forecast.py                            # end-of-month projection + budget check
python alert.py                               # ntfy: new recurring charges, bills due, forecast over budget
```

Or just `python run.py incoming/statement.csv` to do all of the above in one shot.

## Design notes

- **Categorization is rule-based keyword matching** (`categories.py`), not an LLM call --
  merchant strings are short and repetitive, so a keyword table gets most of it for free.
  Anything unmatched is tagged `uncategorized` and left visible rather than guessed at.
- **Recurring-charge detection** groups transactions by normalized merchant text and flags a
  group as recurring only if it repeats at a roughly stable interval (offering the tool
  something to reason about deterministically) -- a single repeated purchase (e.g. two trips to
  the same supermarket 3 days apart) is not a subscription.
- **Forecast is a confidence band, not a fake-precise number** -- bootstrap resampling of daily
  spend (same method as `analysis/benchmark_snapshot.py`'s `boot_ci`), reported as a range.
- **Alerts go over the existing ntfy channel** (`NTFY_SERVER`/`NTFY_TOPIC` env vars, same as
  `analysis/notify.py`), not a new dashboard to remember to check.
