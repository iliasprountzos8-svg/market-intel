#!/bin/bash
# homelab-camera: capture the laptop webcam and publish it to the local mediamtx relay, which
# turns it into an HLS stream the phone can watch. Off until this script runs, off again the
# moment it exits -- the camera hardware is untouched otherwise. Holds an flock for the whole
# run so camera_app.py's is_running() check (joblock.py) works, and writes its own PID so the
# app's /stop can signal the whole process group (this script + ffmpeg) to end the run.
set -u
export PATH=/usr/local/sbin:/usr/sbin:/usr/local/bin:/usr/bin:/sbin:/bin
LOCK=/tmp/mi-camera.lock
PIDFILE=/tmp/mi-camera.pid
TOPIC_FILE=/home/ilias/services/ntfy/topic.txt

exec 200>"$LOCK"
flock -n 200 || { echo "already running"; exit 1; }
echo $$ > "$PIDFILE"

notify() { # title msg
  curl -sf -m 10 -H "Title: $1" -H "Priority: default" -H "Tags: video_camera" -d "$2" \
    "http://100.83.128.73:8090/$(cat "$TOPIC_FILE")" >/dev/null 2>&1
}
cleanup() { rm -f "$PIDFILE"; notify "Camera turned OFF" "Home camera live view stopped ($(date '+%H:%M'))."; }
trap cleanup EXIT

notify "Camera turned ON" "Home camera live view started ($(date '+%H:%M')). Remember to turn it off when done."

# 640x480/15fps/~800kbps: usable over cellular on the viewing end without saturating home upload.
ffmpeg -nostdin -loglevel error \
  -f v4l2 -input_format mjpeg -video_size 640x480 -framerate 15 -i /dev/video0 \
  -c:v libx264 -preset ultrafast -tune zerolatency -b:v 800k -maxrate 800k -bufsize 1600k -g 30 \
  -f rtsp rtsp://127.0.0.1:8554/cam
