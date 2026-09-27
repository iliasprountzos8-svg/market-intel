#!/usr/bin/env python3
"""Victus camera agent: the small piece that has to run ON this laptop (Victus), since it's the
only thing that can actually turn its webcam on/off. Everything else -- viewing, the master
playlist, the phone UI -- lives on the homelab's existing camera_app.py, which this agent
publishes into over Tailscale (mediamtx paths victus_hi/victus_lo, same relay as the homelab's
own camera). There is deliberately no trust relationship between the two machines' servers: the
phone's browser calls this agent directly (over Tailscale) for start/stop, and calls the
homelab for everything else. See homelab/camera/camera_app.py's SOURCES dict and
homelab/victus-camera/README.md.

On-demand only (explicit choice, matching the homelab camera's design): nothing captures until
/api/start is called, and it stops the moment /api/stop is called or this agent exits.

Run with: python victus_camera_agent.py   (needs ffmpeg on PATH; see winget install instructions
in the README). Auto-started at logon via Task Scheduler -- see setup_task.ps1.
"""
import base64
import hmac
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
TOKEN_FILE = HERE / ".control-token"
if not TOKEN_FILE.exists():
    import secrets
    TOKEN_FILE.write_text(secrets.token_urlsafe(24))
TOKEN = TOKEN_FILE.read_text().strip()

BIND = ("100.114.19.15", 8097)  # this machine's Tailscale IP
MEDIAMTX_HOST = "100.83.128.73"  # the homelab, over Tailscale
CAMERA_DEVICE = "video=HP Wide Vision HD Camera"
NTFY_URL = "http://100.83.128.73:8090"
NTFY_TOPIC = "homelab-6ee4746a"
REMINDER_EVERY_S = 20 * 60

_proc_lock = threading.Lock()
_proc = None  # the running ffmpeg Popen, or None -- this agent is the only thing that starts/stops
              # it, so an in-memory handle is enough (no cross-process lock file needed, unlike the
              # homelab side where systemd could restart the control app independently).
_on_since = None


def notify(title, msg, tags="video_camera", priority="default"):
    try:
        subprocess.run(["curl", "-sf", "-m", "10", "-H", f"Title: {title}", "-H", f"Priority: {priority}",
                         "-H", f"Tags: {tags}", "-d", msg, f"{NTFY_URL}/{NTFY_TOPIC}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass


def camera_on():
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def start_camera():
    global _proc, _on_since
    with _proc_lock:
        if _proc is not None and _proc.poll() is None:
            return "already running"
        # One device open, two encodes (hi/lo), same pattern as homelab-camera.sh, publishing
        # straight into the homelab's mediamtx over Tailscale (IP-gated there, no creds needed).
        cmd = [
            "ffmpeg", "-nostdin", "-loglevel", "error",
            # This camera only advertises 30fps (no 15fps option) and needs an explicit vcodec to
            # pick the mjpeg pin over dshow (dshow's equivalent of Linux's -input_format).
            "-f", "dshow", "-vcodec", "mjpeg", "-video_size", "640x480", "-framerate", "30", "-i", CAMERA_DEVICE,
            "-filter_complex", "[0:v]split=2[vhi][vlo];[vlo]scale=320:240[vlo_s]",
            "-map", "[vhi]", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "800k", "-maxrate", "800k", "-bufsize", "1600k", "-g", "30",
            "-f", "rtsp", f"rtsp://{MEDIAMTX_HOST}:8554/victus_hi",
            "-map", "[vlo_s]", "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", "200k", "-maxrate", "200k", "-bufsize", "400k", "-g", "30",
            "-f", "rtsp", f"rtsp://{MEDIAMTX_HOST}:8554/victus_lo",
        ]
        _proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        _on_since = time.time()
    notify("Victus camera turned ON", f"Live view started ({time.strftime('%H:%M')}). "
           "Remember to turn it off when done.")
    return "started"


def stop_camera():
    global _proc, _on_since
    with _proc_lock:
        if _proc is None or _proc.poll() is not None:
            _proc = None
            return "already stopped"
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _proc.kill()
        dur = int(time.time() - _on_since) if _on_since else 0
        _proc, _on_since = None, None
    notify("Victus camera turned OFF", f"Live view stopped ({time.strftime('%H:%M')}, "
           f"was on for {dur // 60}m {dur % 60}s).")
    return "stopped"


def reminder_loop():
    """Same reasoning as the homelab agent's reminder_loop: no auto-stop by design, so a periodic
    nudge is the safety net instead. Seeded to "now" on turn-on so it doesn't fire immediately."""
    last_reminder = None
    while True:
        time.sleep(30)
        if camera_on():
            now = time.time()
            if last_reminder is None:
                last_reminder = now
            elif now - last_reminder >= REMINDER_EVERY_S:
                notify("Victus camera is still on", "Live view has been running a while -- "
                       "turn it off from the camera page if you're done.", tags="warning")
                last_reminder = now
        else:
            last_reminder = None


class H(BaseHTTPRequestHandler):
    def _cors(self):
        # The phone's browser calls this cross-origin (page origin is the homelab's camera_app.py,
        # this agent is a different host:port entirely) -- no cookies/credentials involved, so a
        # permissive ACAO is fine; nothing here is reachable off-Tailscale regardless.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        u = urlparse(self.path)
        prefix = "/" + TOKEN + "/"
        if u.path == "/health":
            return "health", ""
        if len(u.path) >= len(prefix) and hmac.compare_digest(u.path[:len(prefix)], prefix):
            return "ok", u.path[len(prefix):]
        return None, None

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self):
        kind, rest = self._auth()
        if kind == "health":
            return self._send(200, b"ok")
        if kind != "ok":
            return self._send(404)
        if rest == "api/status":
            return self._send(200, json.dumps({"on": camera_on()}).encode(), "application/json")
        self._send(404)

    def do_POST(self):
        kind, rest = self._auth()
        if kind != "ok":
            return self._send(404)
        if rest == "api/start":
            return self._send(200, json.dumps({"result": start_camera()}).encode(), "application/json")
        if rest == "api/stop":
            return self._send(200, json.dumps({"result": stop_camera()}).encode(), "application/json")
        self._send(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"Victus camera agent listening on {BIND[0]}:{BIND[1]}, token in {TOKEN_FILE}")
    threading.Thread(target=reminder_loop, daemon=True).start()
    ThreadingHTTPServer(BIND, H).serve_forever()
