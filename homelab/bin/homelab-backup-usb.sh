#!/bin/bash
# Second backup copy: mirrors the primary restic repo onto the USB stick (encrypted, same password).
# The stick is mounted only while this runs. Existing files on the stick are never touched:
# everything lives under /homelab-backup/ on it.
# Sundays: also prune old snapshots, verify 5% of data blocks, and do a test restore.
set -u
export HOME=/root
export RESTIC_CACHE_DIR=/var/cache/restic
export PATH=/usr/local/sbin:/usr/sbin:/usr/local/bin:/usr/bin:/sbin:/bin
UUID=165E-0FAA
MNT=/mnt/usb-backup
SRC=/srv/backups/restic
DST=$MNT/homelab-backup/restic
PASS=/root/.restic-pass
STATE=/var/lib/homelab-backup-usb
TOPIC_FILE=/home/ilias/services/ntfy/topic.txt
mkdir -p "$STATE"; chmod 755 "$STATE"

notify() { curl -s -m 10 -H "Title: $1" -H "Priority: $2" -H "Tags: floppy_disk" -d "$3" "http://100.83.128.73:8090/$(cat "$TOPIC_FILE")" >/dev/null 2>&1; }
R() { restic -r "$DST" --password-file "$PASS" "$@"; }

# Do not overlap with the nightly backup
for i in $(seq 1 30); do systemctl is-active --quiet homelab-backup.service || break; sleep 10; done

if [ ! -e "/dev/disk/by-uuid/$UUID" ]; then
  n=$(( $(cat "$STATE/missing_days" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/missing_days"
  echo "USB backup drive not connected (day $n)"
  [ "$n" -eq 3 ] || [ $((n % 7)) -eq 0 ] && notify "USB backup drive missing" high "The backup USB stick has not been connected for $n days. Plug it in so the second copy can run."
  exit 0
fi
echo 0 > "$STATE/missing_days"

mkdir -p "$MNT"
mountpoint -q "$MNT" || mount -o uid=0,gid=0,umask=077,quiet,flush,noatime "UUID=$UUID" "$MNT" || { echo "mount failed"; exit 1; }
cleanup() { sync; umount "$MNT" 2>/dev/null; }
trap cleanup EXIT

mkdir -p "$MNT/homelab-backup"
if [ ! -f "$DST/config" ]; then
  echo "initialising USB repo (chunker params copied from primary)"
  R init --copy-chunker-params --from-repo "$SRC" --from-password-file "$PASS" || { echo "init failed"; exit 1; }
fi

echo "== copy new snapshots"
R copy --from-repo "$SRC" --from-password-file "$PASS" || { echo "copy failed"; exit 1; }

if [ "$(date +%u)" = 7 ] || [ "${FORCE_WEEKLY:-0}" = 1 ]; then
  echo "== weekly: prune"
  R forget --keep-daily 7 --keep-weekly 4 --keep-monthly 6 --prune || { echo "prune failed"; exit 1; }
  echo "== weekly: verify 5% of data"
  R check --read-data-subset=5% || { echo "check FAILED"; notify "USB backup check FAILED" urgent "restic check on the USB copy reported errors. Do not trust this copy; investigate."; exit 1; }
  echo "== weekly: test restore"
  T=$(mktemp -d)
  R restore latest --target "$T" --include /home/ilias/homelab-config/REBUILD.md >/dev/null 2>&1
  if [ -s "$T/home/ilias/homelab-config/REBUILD.md" ]; then echo "test restore OK"; else echo "test restore FAILED"; rm -rf "$T"; notify "USB backup test restore FAILED" urgent "Could not restore a test file from the USB copy."; exit 1; fi
  rm -rf "$T"
fi

SNAPS=$(R snapshots --json 2>/dev/null | python3 -c 'import sys,json;print(len(json.load(sys.stdin)))' 2>/dev/null || echo "?")
date +%s > "$STATE/last_ok"; chmod 644 "$STATE/last_ok"
echo "USB backup OK: $SNAPS snapshots on stick"
exit 0
