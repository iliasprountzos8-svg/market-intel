#!/bin/bash
# one-off (scheduled 08:00): rebuild signal history once FinBERT has scored the backlog, then re-run the signal test and lab
cd ~/market-intel/analysis || exit 1
set -a; . ~/market-intel/.env; set +a
nice -n 10 .venv/bin/python signals.py --backfill 14 --no-alert > ~/market-intel/logs/signals-backfill.log 2>&1
nice -n 10 .venv/bin/python signal_eval.py > ~/market-intel/logs/signal-eval.txt 2>&1
cd ~/market-intel/lab && nice -n 10 .venv/bin/python lab_daily.py --no-prices >> ~/market-intel/logs/lab-daily.txt 2>&1
