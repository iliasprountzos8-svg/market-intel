#!/bin/bash
# homelab-camera: capture the laptop webcam once and publish TWO renditions (hi/lo bitrate) to
# the local mediamtx relay, so the phone's HLS player can auto-switch quality on a bad connection
# instead of just buffering. Off until this script runs, off again the moment it exits -- the
# camera hardware is untouched otherwise. Holds an flock for the whole run so camera_app.py's
# is_running() check (joblock.py) works, and writes its own PID so the app's /stop can signal the
# whole process group (this script + ffmpeg) to end the run.
set -u
export PATH=/usr/local/sbin:/usr/sbin:/usr/local/bin:/usr/bin:/sbin:/bin
LOCK=/tmp/mi-camera.lock
PIDFILE=/tmp/mi-camera.pid
TOPIC_FILE=/home/ilias/services/ntfy/topic.txt
SESSIONS=/home/ilias/services/camera/sessions.jsonl

exec 200>"$LOCK"
flock -n 200 || { echo "already running"; exit 1; }
echo $$ > "$PIDFILE"

notify() { # title msg
  curl -sf -m 10 -H "Title: $1" -H "Priority: default" -H "Tags: video_camera" -d "$2" \
    "http://100.83.128.73:8090/$(cat "$TOPIC_FILE")" >/dev/null 2>&1
}
log_session() { # event
  printf '{"event":"%s","ts":"%s"}\n' "$1" "$(date -Is)" >> "$SESSIONS"
}
START_TS=$(date +%s)
cleanup() {
  rm -f "$PIDFILE"
  DUR=$(( $(date +%s) - START_TS ))
  log_session "off"
  notify "Camera turned OFF" "Home camera live view stopped ($(date '+%H:%M'), was on for $((DUR/60))m $((DUR%60))s)."
}
trap cleanup EXIT

log_session "on"
notify "Camera turned ON" "Home camera live view started ($(date '+%H:%M')). Remember to turn it off when done."

# One device open, two encodes: hi (640x480/~800kbps) for good connections, lo (320x240/~200kbps)
# for cellular/weak links. hls.js and native HLS both auto-switch between them from the master
# playlist camera_app.py serves -- no manual quality picker needed.
ffmpeg -nostdin -loglevel error \
  -f v4l2 -input_format mjpeg -video_size 640x480 -framerate 15 -i /dev/video0 \
  -filter_complex "[0:v]split=2[vhi][vlo];[vlo]scale=320:240[vlo_s]" \
  -map "[vhi]" -c:v libx264 -preset ultrafast -tune zerolatency -b:v 800k -maxrate 800k -bufsize 1600k -g 30 \
    -f rtsp rtsp://127.0.0.1:8554/cam_hi \
  -map "[vlo_s]" -c:v libx264 -preset ultrafast -tune zerolatency -b:v 200k -maxrate 200k -bufsize 400k -g 30 \
    -f rtsp rtsp://127.0.0.1:8554/cam_lo
