#!/bin/bash
cd ~/market-intel/lab || exit 1
set -a; . ~/market-intel/.env; set +a
nice -n 10 .venv/bin/python lab_daily.py --notify > ~/market-intel/logs/lab-daily.txt 2>&1
