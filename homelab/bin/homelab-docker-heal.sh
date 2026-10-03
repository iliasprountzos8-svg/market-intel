#!/bin/bash
# Self-heal for containers that are "Up" but lost their published ports or network attachment.
# Found 2026-09-27: after a reboot, five containers bound to the Tailscale address were running with NO ports and NO network
# (status looked healthy; ntfy, Nextcloud, Uptime Kuma, Homepage and Syncthing were unreachable). `docker restart` did not fix it;
# recreating the container does. Runs at boot (after docker + tailscaled) and every 10 minutes from the watchdog.
# Usage: homelab-docker-heal.sh [--dry-run]     Needs root (docker). Rate limit: 3 recreations per container per hour.
set -u
export PATH=/usr/local/sbin:/usr/sbin:/usr/local/bin:/usr/bin:/sbin:/bin
STATE=/var/lib/homelab-docker-heal
mkdir -p "$STATE"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
NOW=$(date +%s)
healed=0; failed=0

for id in $(docker ps -q); do
  name=$(docker inspect -f '{{.Name}}' "$id" | tr -d /)
  want=$(docker inspect -f '{{len .HostConfig.PortBindings}}' "$id")
  nets=$(docker inspect -f '{{len .NetworkSettings.Networks}}' "$id")
  ports=$(docker inspect -f '{{len .NetworkSettings.Ports}}' "$id")
  [ "$want" -gt 0 ] || continue
  if [ "$nets" -gt 0 ] && [ "$ports" -gt 0 ]; then continue; fi

  echo "PROBLEM: $name is up but has $nets networks and $ports published ports (configured: $want)"
  # rate limit
  recent=$(awk -v now="$NOW" -v n="$name" '$1==n && now-$2<3600' "$STATE/heals.log" 2>/dev/null | wc -l)
  if [ "$recent" -ge 3 ]; then echo "  skipped: already recreated $recent times in the last hour"; failed=$((failed+1)); continue; fi
  [ "$DRY" = 1 ] && continue

  dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$id")
  svc=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.service"}}' "$id")
  if [ -z "$dir" ] || [ -z "$svc" ]; then echo "  cannot heal $name: not managed by compose"; failed=$((failed+1)); continue; fi
  echo "$name $NOW" >> "$STATE/heals.log"
  if (cd "$dir" && docker compose up -d --force-recreate "$svc" >/dev/null 2>&1); then
    sleep 5
    n2=$(docker inspect -f '{{len .NetworkSettings.Networks}}' "$name" 2>/dev/null || echo 0)
    p2=$(docker inspect -f '{{len .NetworkSettings.Ports}}' "$name" 2>/dev/null || echo 0)
    if [ "$n2" -gt 0 ] && [ "$p2" -gt 0 ]; then echo "  healed $name (recreated)"; healed=$((healed+1)); else echo "  recreate did not restore $name"; failed=$((failed+1)); fi
  else
    echo "  recreate failed for $name"; failed=$((failed+1))
  fi
done

echo "docker-heal: healed=$healed failed=$failed"
[ "$healed" -gt 0 ] && logger -t homelab-docker-heal "recreated $healed container(s) that had lost ports or network"
# exit status 2 = some container still broken (the watchdog turns that into a phone alert)
[ "$failed" -gt 0 ] && exit 2
exit 0
