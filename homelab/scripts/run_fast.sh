#!/bin/bash
# Fast lane (every 5 min): pull whatever feeds are due, classify, score new articles with FinBERT.
# The full 30-minute cycle still does fulltext, signals, outcomes, notify and mirror.
exec 8>/tmp/mi-fast.lock
flock -n 8 || exit 0
flock -n /tmp/mi-cycle.lock true || exit 0   # the full cycle is running: it does the same work
cd ~/market-intel || exit 1
set -a; . ./.env; set +a
echo "started $(date -Is)" > logs/fast-status.txt
ingest/.venv/bin/python ingest/ingest.py --max-minutes 3 > logs/fast-lane.txt 2>&1; R1=$?
analysis/.venv/bin/python analysis/cli.py bulk-classify --rule-based --limit 2000 >> logs/fast-lane.txt 2>&1
nlp/.venv/bin/python nlp/score_finbert.py --max-minutes 2 --threads 2 >> logs/fast-lane.txt 2>&1
NEW=$(grep -o 'new=[0-9]*' logs/fast-lane.txt | tail -n 1)
echo "finished $(date -Is) rc=$R1 ${NEW}" > logs/fast-status.txt
exit $R1
