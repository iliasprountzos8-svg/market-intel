#!/bin/bash
# Daily homelab health report: collect facts, have Claude summarise (no tools, read-only text in).
set -uo pipefail
export PATH="$HOME/.local/bin:/usr/sbin:$PATH"
OUT=~/agents/reports/health-$(date +%F).md
FACTS=~/agents/reports/.last-facts.txt
{
  echo "## uptime"; uptime -p
  echo "## memory"; free -h | sed -n '1,2p'
  echo "## disk"; df -h / | tail -n 1
  echo "## load"; cat /proc/loadavg
  echo "## docker containers"; docker ps -a --format '{{.Names}}\t{{.Status}}'
  echo "## restarting/unhealthy"; docker ps -a --filter status=restarting --filter health=unhealthy --format '{{.Names}}'
  echo "## backup timer"; systemctl list-timers homelab-backup.timer --no-pager | sed -n '1,2p'
  echo "## last backup result"; systemctl show homelab-backup.service -p Result -p ExecMainExitTimestamp
  echo "## latest snapshot"; sudo -n env RESTIC_REPOSITORY=/srv/backups/restic RESTIC_PASSWORD_FILE=/root/.restic-pass restic snapshots --latest 1 --compact 2>&1 | tail -n 4
  echo "## ssd health"; sudo -n smartctl -H /dev/nvme0n1 2>&1 | grep -E "overall|result"; sudo -n nvme smart-log /dev/nvme0n1 2>&1 | grep -E "critical_warning|percentage_used|available_spare |temperature"
  echo "## watchdog report (SSD write rate + cycle timings)"; /usr/local/bin/homelab-watchdog.sh report 2>&1 | head -n 22
  echo "## usb backup (second copy)"; U=$(cat /var/lib/homelab-backup-usb/last_ok 2>/dev/null); if [ -n "$U" ]; then echo "last ok: $(date -d @"$U" '+%F %H:%M') ($(( ($(date +%s)-U)/3600 )) h ago)"; else echo "never completed"; fi
  echo "## reboot pending"; [ -f /var/run/reboot-required ] && echo yes || echo no
  echo "## market intel data"; docker compose -f /home/ilias/services/marketdb/compose.yaml exec -T db psql -U postgres -d marketintel -At -F ' | ' -c "select 'articles total', count(*) from articles union all select 'articles last 24h', count(*) from articles where published_at > now() - interval '24 hours' union all select 'sources enabled', count(*) from sources where enabled union all select 'sources failing (fail_count>=3)', count(*) from sources where fail_count >= 3 union all select 'finbert scores', count(*) from article_scores where model='finbert' union all select 'finbert backlog (30d)', count(*) from articles a left join article_scores s on s.article_id=a.id and s.model='finbert' where s.article_id is null and a.published_at > now() - interval '30 days' union all select 'db size', 0" </dev/null 2>&1 | head -n 12
  docker compose -f /home/ilias/services/marketdb/compose.yaml exec -T db psql -U postgres -d marketintel -At -c "select 'db size: ' || pg_size_pretty(pg_database_size('marketintel'))" </dev/null 2>&1
  echo "## market intel last cycle"; cat /home/ilias/market-intel/logs/cycle-status.txt
  echo "## prediction lab"; python3 -c "import json; d=json.load(open('/home/ilias/market-intel/logs/lab-daily.json')); print('as of', d['as_of'], '| open paper positions', d['open_paper_positions'], '| settled', {k:(v['closed_positions'], round(v['mean_net_excess_pct'],2)) for k,v in d['ledger'].items()})" 2>&1
  echo "## service failures last 24h"; for u in market-intel market-intel-digest market-intel-lab-daily market-intel-weekly homelab-backup health-report; do n=$(sudo -n journalctl -u $u.service --since '24 hours ago' --no-pager 2>/dev/null | grep -c "Failed with result"); echo "$u: $n"; done
  echo "## pending updates"; apt list --upgradable 2>/dev/null | tail -n +2 | wc -l
  echo "## failed services"; systemctl --failed --no-legend
  echo "## firewall"; sudo -n ufw status | head -n 2
  echo "## wifi"; ip -br a show wlo1; sudo -n /usr/sbin/iw dev wlo1 link | grep -E 'signal|SSID'
  echo "## tailscale"; tailscale status | head -n 3
  echo "## ssh failed logins (24h)"; sudo -n journalctl -u ssh --since '24 hours ago' --no-pager | grep -c 'Failed\|Invalid'
} > "$FACTS" 2>&1
{
  echo "# Homelab health - $(date '+%F %H:%M')"
  echo
  python3 /home/ilias/agents/health_summary.py < "$FACTS"
} > "$OUT" 2>&1
# push the report to the phone via ntfy (Tailscale-only)
TOPIC=$(cat ~/services/ntfy/topic.txt)
PRIO=default
grep -qi 'STATUS: *FAIL\|needs attention' "$OUT" && PRIO=high
curl -s -H "Title: Homelab health $(date +%F)" -H "Priority: $PRIO" -H "Tags: computer" -d "$(tail -n +3 "$OUT" | head -c 3500)" "http://100.83.128.73:8090/$TOPIC" >/dev/null
