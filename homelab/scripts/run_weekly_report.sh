#!/bin/bash
# Weekly: honest track record + does-the-signal-work test, pushed to ntfy. Read-only.
cd ~/market-intel/analysis || exit 1
set -a; . ~/market-intel/.env; set +a
.venv/bin/python track_record.py --notify > ~/market-intel/logs/track-record.txt 2>&1
.venv/bin/python signal_eval.py --notify > ~/market-intel/logs/signal-eval.txt 2>&1
