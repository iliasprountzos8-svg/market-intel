#!/bin/bash
# homelab-watchdog: local dead-man checks with ntfy alerts (cooldown + "resolved" notices),
# a daily SSD wear log and a per-step cycle timing log.
# Usage: homelab-watchdog.sh [check|report|test]   (DRY=1 prints instead of sending)
set -u
export PATH=/usr/local/sbin:/usr/sbin:/usr/local/bin:/usr/bin:/sbin:/bin
MI=/home/ilias/market-intel
ST=/home/ilias/.local/state/homelab-watchdog
REP=/home/ilias/agents/reports
TOPIC_FILE=/home/ilias/services/ntfy/topic.txt
CSV=$REP/ssd-wear.csv
TIMINGS=$MI/logs/step-timings.jsonl
mkdir -p "$ST" "$REP"
NOW=$(date +%s)

send() { # title prio tags msg
  if [ "${DRY:-0}" = 1 ]; then echo "[DRY] ($2) $1 :: $4"; return; fi
  if ! curl -sf -m 10 -H "Title: $1" -H "Priority: $2" -H "Tags: $3" -d "$4" "http://100.83.128.73:8090/$(cat "$TOPIC_FILE")" >/dev/null 2>&1; then
    echo "$(date -Is) UNSENT: $1 :: $4" >> "$ST/unsent.log"
    logger -t homelab-watchdog "ntfy unreachable, alert NOT delivered: $1"
    return 1
  fi
}
alert() { # key prio title msg [cooldown_s]
  local key=$1 prio=$2 title=$3 msg=$4 cool=${5:-21600} last=0
  [ -f "$ST/$key.last" ] && last=$(cat "$ST/$key.last")
  touch "$ST/$key.active"
  if [ $((NOW-last)) -ge "$cool" ]; then send "$title" "$prio" warning "$msg" && echo "$NOW" > "$ST/$key.last"; fi
}
ok() { # key
  if [ -f "$ST/$1.active" ]; then
    send "Resolved: $1" default white_check_mark "The $1 condition on homelab has cleared."
    rm -f "$ST/$1.active" "$ST/$1.last"
  fi
}
age_of() { local t; t=$(date -d "$1" +%s 2>/dev/null) || { echo 999999999; return; }; echo $((NOW-t)); }

if [ "${1:-check}" = test ]; then send "Watchdog online" default white_check_mark "homelab-watchdog test message ($(date '+%F %H:%M'))."; exit 0; fi

if [ "${1:-check}" = report ]; then
python3 - "$CSV" "$TIMINGS" <<'PY'
import sys, csv, json, os, statistics as s
from datetime import date
csv_p, tim_p = sys.argv[1:3]
print("== SSD wear")
rows = list(csv.DictReader(open(csv_p))) if os.path.exists(csv_p) else []
if len(rows) < 2:
    print(f"  {len(rows)} day(s) logged; need 2+ days for a rate.")
else:
    a, b = rows[0], rows[-1]
    days = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days or 1
    gb = (int(b["data_units_written"]) - int(a["data_units_written"])) * 512000 / 1e9
    dp = int(b["percentage_used"]) - int(a["percentage_used"])
    print(f"  {a['date']} -> {b['date']} ({days} d): {gb/days:.1f} GB/day written, wear {a['percentage_used']}% -> {b['percentage_used']}%")
    if dp > 0:
        print(f"  projected years to 100% at this rate: {(100-int(b['percentage_used']))/(dp/days)/365:.1f}")
    else:
        print("  wear counter has not moved yet (it moves in 1% steps).")
print("== cycle timings (median seconds per step, last 48 cycles)")
if os.path.exists(tim_p):
    recs = [json.loads(l) for l in open(tim_p)][-48:]
    names = sorted({k for r in recs for k in r["steps"]})
    for n in names:
        v = [r["steps"][n] for r in recs if n in r["steps"]]
        print(f"  {n:<16} median {s.median(v):7.1f}  max {max(v):7.1f}")
    t = [r["total"] for r in recs]
    print(f"  {'TOTAL':<16} median {s.median(t):7.1f}  max {max(t):7.1f}  (n={len(recs)})")
else:
    print("  no timings yet")
PY
exit 0
fi

# ---- boot notice (detects reboots / power loss), then grace period ----
BOOT=$(cat /proc/sys/kernel/random/boot_id)
if [ "$(cat "$ST/boot_id" 2>/dev/null)" != "$BOOT" ]; then
  [ -f "$ST/boot_id" ] && send "homelab rebooted" default arrows_counterclockwise "Booted at $(uptime -s). If you did not reboot it, check power or crash logs."
  echo "$BOOT" > "$ST/boot_id"
fi
[ "$(cut -d. -f1 /proc/uptime)" -lt 600 ] && exit 0

# ---- full cycle: hung / stale ----
read -r kind ts _ < "$MI/logs/cycle-status.txt" 2>/dev/null || { kind=none; ts=""; }
age=$(age_of "$ts")
if [ "$kind" = started ] && [ "$age" -gt 3600 ]; then alert cycle_hung urgent "Market Intel cycle looks hung" "The current cycle started $((age/60)) min ago (limit 50 min)."; else ok cycle_hung; fi
if [ "$kind" = finished ] && [ "$age" -gt 4500 ]; then alert cycle_stale high "Market Intel cycle is not running" "Last cycle finished $((age/60)) min ago; expected every 30 min. Check: systemctl status market-intel.timer"; else ok cycle_stale; fi

# ---- fast lane ----
read -r fkind fts _ < "$MI/logs/fast-status.txt" 2>/dev/null || { fkind=none; fts=""; }
fage=$(age_of "$fts")
if [ "$fkind" = finished ] && [ "$fage" -gt 1800 ]; then alert fast_stale default "Fast lane stalled" "Last fast-lane run finished $((fage/60)) min ago (expected every 5 min)."; else ok fast_stale; fi

# ---- database reachable and data still flowing ----
SEC=$(docker exec marketdb-db-1 psql -U postgres -d marketintel -Atc "select coalesce(extract(epoch from now()-max(scraped_at))::int,999999) from articles" 2>/dev/null)
if [ -z "$SEC" ]; then
  alert db_down urgent "Market DB not answering" "psql against marketdb-db-1 returned nothing. Check: docker ps; docker logs marketdb-db-1" 3600
else
  ok db_down
  if [ "$SEC" -gt 5400 ]; then alert data_stalled high "No new articles for $((SEC/60)) min" "Newest article scraped $((SEC/60)) min ago. Feeds, network or ingest may be down."; else ok data_stalled; fi
fi

# ---- containers that are "Up" but lost their ports or network: self-heal first, then alert if it did not work ----
heal_out=$(sudo -n /usr/local/bin/homelab-docker-heal.sh 2>&1); heal_rc=$?
if echo "$heal_out" | grep -q "healed=[1-9]"; then
  send "Self-heal: recreated containers" default wrench "$(echo "$heal_out" | grep -E "healed|PROBLEM" | head -4 | tr '\n' ' ')"
fi
if [ "$heal_rc" -eq 2 ]; then alert containers_broken urgent "Containers without ports or network" "$(echo "$heal_out" | grep -E "PROBLEM|skipped|cannot|failed" | head -3 | tr '\n' ' ')" 3600; else ok containers_broken; fi
# the alert channel itself: if ntfy does not answer, nothing above can reach the phone
if curl -sf -m 5 http://100.83.128.73:8090/v1/health >/dev/null 2>&1; then ok ntfy_down; else logger -t homelab-watchdog "ntfy health check FAILED"; alert ntfy_down urgent "ntfy is not answering" "The phone alert channel is down. Check: docker ps; docker logs ntfy-ntfy-1" 3600; fi

# ---- services and containers ----
bad=""
for s in market-intel-sync market-intel-hq; do systemctl is-active --quiet "$s" || bad="$bad $s"; done
if [ -n "$bad" ]; then alert svc_down high "Service down:$bad" "systemd reports inactive:$bad. Check: journalctl -u <name> -n 50"; else ok svc_down; fi
cbad=$(docker ps -a --format '{{.Names}}\t{{.Status}}' 2>/dev/null | awk -F'\t' '$2 !~ /^Up/ || $2 ~ /unhealthy|Restarting/ {printf "%s ", $1}')
if [ -n "$cbad" ]; then alert containers high "Container problem: $cbad" "Not running or unhealthy: $cbad"; else ok containers; fi

# ---- backup freshness (newest restic snapshot file) ----
newest=$(sudo -n find /srv/backups/restic/snapshots -type f -printf '%T@\n' 2>/dev/null | sort -n | tail -1 | cut -d. -f1)
if [ -z "$newest" ]; then alert backup_old high "Cannot read backup repo" "No snapshot files found under /srv/backups/restic/snapshots." 43200
elif [ $((NOW-newest)) -gt 93600 ]; then alert backup_old high "Backup is stale" "Newest restic snapshot is $(( (NOW-newest)/3600 )) h old (expected daily)." 43200
else ok backup_old; fi

# ---- USB (second) backup freshness ----
usb_ok=$(cat /var/lib/homelab-backup-usb/last_ok 2>/dev/null)
if [ -n "$usb_ok" ] && [ $((NOW-usb_ok)) -gt 259200 ]; then alert usb_backup high "USB backup is stale" "The second backup copy last succeeded $(( (NOW-usb_ok)/86400 )) days ago: stick unplugged or the job is failing." 43200; else ok usb_backup; fi

# ---- host limits ----
dpct=$(df --output=pcent / | tail -1 | tr -dc 0-9)
if [ "${dpct:-0}" -gt 80 ]; then alert disk high "Disk ${dpct}% full" "Root filesystem is ${dpct}% used."; else ok disk; fi
memmb=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)
if [ "${memmb:-9999}" -lt 400 ]; then alert memory high "Low memory: ${memmb} MB available" "Check top consumers: ps -eo rss,comm --sort=-rss | head"; else ok memory; fi
tmax=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)
if [ "${tmax:-0}" -gt 85000 ]; then alert thermal high "CPU hot: $((tmax/1000)) C" "Sustained heat with the lid closed. Check load and ventilation." 3600; else ok thermal; fi

# ---- upkeep nudges ----
if [ -f /var/run/reboot-required ]; then alert reboot_needed default "Reboot needed" "A kernel or library update is waiting for a reboot. Pick a quiet time (not 03:30-04:45)." 604800; else ok reboot_needed; fi
exp=$(tailscale status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"].get("KeyExpiry") or "")' 2>/dev/null)
if [ -n "$exp" ]; then
  d=$(( ($(date -d "$exp" +%s) - NOW) / 86400 ))
  if [ "$d" -lt 30 ]; then alert ts_key high "Tailscale key expires in ${d} days" "Disable key expiry for this machine in the Tailscale admin console, or the homelab drops off your tailnet." 259200; else ok ts_key; fi
fi

# ---- daily SSD wear log ----
TODAY=$(date +%F)
if ! grep -q "^$TODAY," "$CSV" 2>/dev/null; then
  out=$(sudo -n nvme smart-log /dev/nvme0 2>/dev/null)
  pu=$(echo "$out" | awk -F: '/percentage_used/{gsub(/[ %]/,"",$2);print $2}')
  du=$(echo "$out" | awk -F: '/Data Units Written/{split($2,a," ");print a[1]}')
  ph=$(echo "$out" | awk -F: '/power_on_hours/{gsub(/ /,"",$2);print $2}')
  if [ -n "$pu" ] && [ -n "$du" ]; then
    [ -f "$CSV" ] || echo "date,percentage_used,data_units_written,power_on_hours" > "$CSV"
    echo "$TODAY,$pu,$du,${ph:-0}" >> "$CSV"
    if [ "$pu" -ge 50 ]; then alert ssd_wear high "SSD wear at ${pu}%" "NVMe percentage_used is ${pu}%. Plan a replacement and confirm the second backup exists." 604800; else ok ssd_wear; fi
  fi
fi

# ---- daily package-integrity check (debsums): modified system files = possible tampering ----
if ! grep -q "^$TODAY$" "$ST/integrity_day" 2>/dev/null && command -v debsums >/dev/null; then
  echo "$TODAY" > "$ST/integrity_day"
  bad_files=$(sudo -n nice -n 15 ionice -c3 debsums -s 2>&1 | head -n 5)
  if [ -n "$bad_files" ]; then
    alert integrity high "System file integrity warning" "debsums reports changed or missing package files: $(echo "$bad_files" | tr '\n' ' ' | cut -c1-300)" 86400
  else ok integrity; fi
fi

# ---- per-step cycle timings (only after a cycle has finished) ----
if [ "$kind" = finished ]; then
python3 - "$MI/logs/cycle-latest.txt" "$ST" "$TIMINGS" <<'PY'
import json, re, os, sys
p, st, out = sys.argv[1:4]
if not os.path.exists(p): sys.exit()
steps, first = {}, None
for line in open(p, errors="ignore"):
    try: d = json.loads(line)
    except Exception: continue
    first = first or d.get("timestamp")
    r = re.match(r"Finished step: (\S+) - .*\(([\d.]+)s\)\s*$", d.get("message", ""))
    if r: steps[r.group(1)] = float(r.group(2))
mark = f"{st}/last_timing"
if not steps or (os.path.exists(mark) and open(mark).read() == first): sys.exit()
total = round(sum(steps.values()), 1)
with open(out, "a") as f: f.write(json.dumps({"ts": first, "total": total, "steps": steps}) + "\n")
open(mark, "w").write(first)
open(f"{st}/last_total", "w").write(str(total))
PY
tot=$(cat "$ST/last_total" 2>/dev/null || echo 0)
if [ "${tot%.*}" -gt 1200 ]; then alert cycle_slow default "Slow cycle: $((${tot%.*}/60)) min" "Last full cycle took $((${tot%.*}/60)) min (normal about 8). See: homelab-watchdog.sh report" 21600; else ok cycle_slow; fi
fi
exit 0
