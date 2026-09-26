#!/bin/bash
# One data-pull cycle with a lock (no overlap) and a status file for the phone page.
exec 9>/tmp/mi-cycle.lock
flock -n 9 || exit 0
cd ~/market-intel
export MI_ENABLE_SIGNALS=1
echo "started $(date -Is)" > logs/cycle-status.txt
python3 run_cycle.py > logs/cycle-latest.txt 2>&1
RC=$?
echo "finished $(date -Is) rc=$RC" > logs/cycle-status.txt
exit $RC
