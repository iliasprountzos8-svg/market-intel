#!/bin/bash
cd ~/market-intel/lab || exit 1
set -a; . ~/market-intel/.env; set +a
nice -n 10 .venv/bin/python lab_wf.py --h 5 --refresh > ~/market-intel/logs/lab-wf-h5.log 2>&1
nice -n 10 .venv/bin/python lab_wf.py --h 20 > ~/market-intel/logs/lab-wf-h20.log 2>&1
# event-study research: refresh EDGAR 8-K history, re-test 8-K item types out-of-sample, re-run the event-feature ablation
nice -n 10 .venv/bin/python edgar_events.py --since 2015-01-01 > ~/market-intel/logs/edgar-backfill.log 2>&1
nice -n 10 .venv/bin/python event_study.py --refresh-prices > ~/market-intel/logs/event-study.log 2>&1
nice -n 10 .venv/bin/python ablation.py --h 5 > ~/market-intel/logs/ablation-h5.log 2>&1
