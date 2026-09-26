#!/bin/bash
cd ~/market-intel/analysis
for i in $(seq 1 40); do
  OUT=$(.venv/bin/python cli.py bulk-classify --rule-based --limit 4000 2>&1 | grep -E 'Bulk classified|No unprocessed' | tail -n 1)
  echo "$(date +%H:%M:%S) pass $i: $OUT" >> ~/market-intel/logs/classify-backlog.log
  echo "$OUT" | grep -q 'No unprocessed' && break
done
echo "$(date +%H:%M:%S) finished" >> ~/market-intel/logs/classify-backlog.log
