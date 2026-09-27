#!/usr/bin/env python3
"""Camera control: a standalone phone page to toggle the home webcam live view on/off and watch
it. Tailscale-only bind, secret-path token, same pattern as services/hq/hq_app.py but decoupled
from it entirely (own token, own port, own page) since this is a more sensitive capability and
deliberately kept separate rather than folded into the market-intel dashboard.

No auto-stop by design (explicit choice): instead, a background thread sends a periodic ntfy
reminder while the camera is live, so it can never be silently forgotten.

Run with: python3 camera_app.py   (needs only the stdlib + curl on PATH for ntfy)
"""
import base64
import hmac
import http.cookiejar
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# joblock.py sits next to camera_app.py in the deployed layout (~/services/camera/) and one
# level up in the repo layout (homelab/joblock.py, shared with hq_app.py/sync_daemon.py) -- try both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from joblock import is_running as _is_running  # noqa: E402

HERE = Path(__file__).resolve().parent
TOKEN = (HERE / ".control-token").read_text().strip()
BIND = ("100.83.128.73", 8096)
LOCK = "/tmp/mi-camera.lock"
PIDFILE = Path("/tmp/mi-camera.pid")
# Same dual-location story as joblock.py above: next to this file when deployed, homelab/bin/
# in the repo checkout.
CAM_SCRIPT = str(HERE / "homelab-camera.sh") if (HERE / "homelab-camera.sh").exists() \
    else str(HERE.parent / "bin" / "homelab-camera.sh")
NTFY_TOPIC_FILE = Path("/home/ilias/services/ntfy/topic.txt")
NTFY_URL = "http://100.83.128.73:8090"
REMINDER_EVERY_S = 20 * 60

# mediamtx serves HLS on a different port (8888), which makes it a different browser origin from
# this app (8096): hls.js's cross-origin XHR then can't carry mediamtx's cookie-based session auth,
# so the stream silently fails to load in any real browser. Fix: proxy it through this same origin.
# A single opener with a cookiejar mirrors what a real browser tab does (follow mediamtx's one-time
# auth redirect, keep the cookie for subsequent segment requests).
# mediamtx's hlsAddress is bound to the Tailscale IP specifically (not 127.0.0.1), so the proxy
# has to reach it there too even though both processes run on the same host.
_STREAM_BASE = f"http://{BIND[0]}:8888/cam/"
_BASIC_AUTH = base64.b64encode(b"viewer:" + TOKEN.encode()).decode()
_cookiejar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookiejar))


def proxy_stream(path_and_query: str):
    """Fetch one HLS file (playlist or segment) from mediamtx and return (status, content_type, body)."""
    req = urllib.request.Request(_STREAM_BASE + path_and_query, headers={"Authorization": f"Basic {_BASIC_AUTH}"})
    try:
        with _opener.open(req, timeout=10) as resp:
            return 200, resp.headers.get("Content-Type", "application/octet-stream"), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, "text/plain", b""
    except urllib.error.URLError:
        return 502, "text/plain", b""


def notify(title, msg, tags="video_camera"):
    if not NTFY_TOPIC_FILE.exists():
        return
    topic = NTFY_TOPIC_FILE.read_text().strip()
    try:
        subprocess.run(["curl", "-sf", "-m", "10", "-H", f"Title: {title}", "-H", "Priority: default",
                         "-H", f"Tags: {tags}", "-d", msg, f"{NTFY_URL}/{topic}"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
    except Exception:
        pass


def camera_on():
    return _is_running(LOCK)


def start_camera():
    if camera_on():
        return "already running"
    subprocess.Popen(["/bin/bash", CAM_SCRIPT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL, start_new_session=True)
    return "started"


def stop_camera():
    if not camera_on():
        return "already stopped"
    try:
        pid = int(PIDFILE.read_text().strip())
        os.killpg(pid, signal.SIGTERM)
        return "stopped"
    except (OSError, ValueError, FileNotFoundError):
        return "could not find process to stop (may have already exited)"


def reminder_loop():
    """While the camera is on, nudge every REMINDER_EVERY_S so it's never silently forgotten
    (there is no auto-stop by design -- this is the safety net instead). last_reminder is seeded
    to "now" the moment it turns on, not 0 -- otherwise the first check sees a huge elapsed time
    since the epoch and fires immediately instead of waiting a full interval."""
    last_reminder = None
    while True:
        time.sleep(30)
        if camera_on():
            now = time.time()
            if last_reminder is None:
                last_reminder = now  # just turned on: start the clock, don't nudge yet
            elif now - last_reminder >= REMINDER_EVERY_S:
                notify("Camera is still on", "Home camera live view has been running for a while. "
                       "Open the camera page to turn it off if you're done.", tags="warning")
                last_reminder = now
        else:
            last_reminder = None


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
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

    def do_GET(self):
        kind, rest = self._auth()
        if kind == "health":
            return self._send(200, b"ok")
        if kind != "ok":
            return self._send(404)
        if rest in ("", "index.html"):
            html = (HERE / "index.html").read_bytes().replace(b"__TOKEN__", TOKEN.encode())
            return self._send(200, html, "text/html; charset=utf-8")
        if rest == "hls.js":
            return self._send(200, (HERE / "hls.js").read_bytes(), "application/javascript")
        if rest == "api/status":
            return self._send(200, json.dumps({"on": camera_on()}).encode(), "application/json")
        if rest.startswith("stream/"):
            path_and_query = rest[len("stream/"):]
            if self.path.count("?"):
                path_and_query += "?" + self.path.split("?", 1)[1]
            code, ctype, body = proxy_stream(path_and_query)
            return self._send(code, body, ctype)
        self._send(404)

    def do_POST(self):
        kind, rest = self._auth()
        if kind != "ok":
            return self._send(404)
        if rest == "api/start":
            result = start_camera()
            return self._send(200, json.dumps({"result": result}).encode(), "application/json")
        if rest == "api/stop":
            result = stop_camera()
            return self._send(200, json.dumps({"result": result}).encode(), "application/json")
        self._send(404)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    threading.Thread(target=reminder_loop, daemon=True).start()
    ThreadingHTTPServer(BIND, H).serve_forever()
