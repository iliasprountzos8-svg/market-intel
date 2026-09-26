#!/bin/bash
cd ~/market-intel/lab || exit 1
set -a; . ~/market-intel/.env; set +a
nice -n 10 .venv/bin/python lab_wf.py --h 5 --refresh > ~/market-intel/logs/lab-wf-h5.log 2>&1
nice -n 10 .venv/bin/python lab_wf.py --h 20 > ~/market-intel/logs/lab-wf-h20.log 2>&1
