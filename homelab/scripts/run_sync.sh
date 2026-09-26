#!/bin/bash
# "Sync Now" from the site/phone: poll EVERY source, classify, FinBERT, fill the AI cells. The sync daemon then pushes the result to the site.
exec 9>/tmp/mi-cycle.lock
flock -n 9 || exit 3      # a full cycle is already running (it does the same work)
cd ~/market-intel || exit 1
set -a; . ./.env; set +a
echo "started $(date -Is)" > logs/sync-status.txt
ingest/.venv/bin/python ingest/ingest.py --force --max-poll 60 --max-minutes 3 > logs/sync-run.txt 2>&1; R1=$?
analysis/.venv/bin/python analysis/cli.py bulk-classify --rule-based --limit 3000 >> logs/sync-run.txt 2>&1
nlp/.venv/bin/python nlp/score_finbert.py --max-minutes 2 --threads 4 >> logs/sync-run.txt 2>&1
analysis/.venv/bin/python analysis/populate_cells.py --hours 48 >> logs/sync-run.txt 2>&1
NEW=$(grep -o 'new=[0-9]*' logs/sync-run.txt | tail -n 1 | cut -d= -f2)
echo "synced: +${NEW:-0} new articles, key sources polled ($(date +%H:%M))" > logs/sync-status.txt
exit $R1
