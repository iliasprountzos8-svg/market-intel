#!/usr/bin/env python3
"""Camera control: a standalone phone page to toggle the home webcam live view on/off and watch
it. Tailscale-only bind, secret-path token, same pattern as services/hq/hq_app.py but decoupled
from it entirely (own token, own port, own page) since this is a more sensitive capability and
deliberately kept separate rather than folded into the market-intel dashboard.

No auto-stop by design (explicit choice): instead, a background thread sends a periodic ntfy
reminder while the camera is live, so it can never be silently forgotten. No motion detection,
no recording -- live-view only, also by explicit choice (see homelab/camera/README.md).

Run with: python3 camera_app.py   (needs only the stdlib + curl/ffmpeg on PATH)
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import urllib.error
import urllib.request

# joblock.py sits next to camera_app.py in the deployed layout (~/services/camera/) and one
# level up in the repo layout (homelab/joblock.py, shared with hq_app.py/sync_daemon.py) -- try both.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from joblock import is_running as _is_running  # noqa: E402

HERE = Path(__file__).resolve().parent
TOKEN = (HERE / ".control-token").read_text().strip()
# Victus's own control agent has its own token and origin entirely (see ../victus-camera/); this
# is just page config for the user's browser to call it directly, not a trust relationship
# between the two servers. Blank if not set up -- the page then just hides the Victus tab.
VICTUS_ENDPOINT_FILE = HERE / "victus-endpoint.txt"
VICTUS_ENDPOINT = VICTUS_ENDPOINT_FILE.read_text().strip() if VICTUS_ENDPOINT_FILE.exists() else ""
BIND = ("100.83.128.73", 8096)
LOCK = "/tmp/mi-camera.lock"
PIDFILE = Path("/tmp/mi-camera.pid")
SESSIONS_LOG = HERE / "sessions.jsonl"
ACCESS_LOG = HERE / "access.log"
# Same dual-location story as joblock.py above: next to this file when deployed, homelab/bin/
# in the repo checkout.
CAM_SCRIPT = str(HERE / "homelab-camera.sh") if (HERE / "homelab-camera.sh").exists() \
    else str(HERE.parent / "bin" / "homelab-camera.sh")
NTFY_TOPIC_FILE = Path("/home/ilias/services/ntfy/topic.txt")
NTFY_URL = "http://100.83.128.73:8090"
REMINDER_EVERY_S = 20 * 60
VIDEO_DEVICE = "/dev/video0"

# mediamtx serves HLS on a different port (8888), which makes it a different browser origin from
# this app (8096): hls.js's cross-origin XHR then can't carry mediamtx's cookie-based session auth,
# so the stream silently fails to load in any real browser. Fix: proxy it through this same origin.
# A single opener with a cookiejar mirrors what a real browser tab does (follow mediamtx's one-time
# auth redirect, keep the cookie for subsequent segment requests).
# mediamtx's hlsAddress is bound to the Tailscale IP specifically (not 127.0.0.1), so the proxy
# has to reach it there too even though both processes run on the same host.
_MEDIAMTX_HOST = f"http://{BIND[0]}:8888"
_BASIC_AUTH = base64.b64encode(b"viewer:" + TOKEN.encode()).decode()
_cookiejar = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cookiejar))

# Two renditions published by homelab-camera.sh (see its comments): "hi" for good connections,
# "lo" for cellular/weak links. hls.js and native HLS both auto-switch from a master playlist
# based on measured bandwidth -- no manual quality picker needed on the phone.
#
# mediamtx's own /cam_hi/index.m3u8 (etc.) is NOT a flat media playlist -- it's itself a
# single-variant master containing one #EXT-X-STREAM-INF pointing at the real media playlist
# (e.g. "video1_stream.m3u8?session=..."), with a session id that changes per request. A master
# playlist's variants must point directly at real media playlists, not at other master playlists,
# so a hand-written master listing "hi/index.m3u8"/"lo/index.m3u8" is invalid HLS that every
# player silently chokes on. build_master() resolves one level through mediamtx's own playlist for
# both renditions, on every request (the session id isn't stable), and rewrites each real media
# playlist's URI to route back through this app's stream/hi/ or stream/lo/ proxy prefix.
# Victus (a second, separate laptop, on-demand only) publishes into this SAME mediamtx as its
# own "victus_hi"/"victus_lo" paths -- see ../victus-camera/README.md. Viewing it needs nothing
# Victus-specific: mediamtx already holds the stream centrally once it's publishing, so this app
# just proxies a different path prefix. Starting/stopping capture on Victus is the one thing that
# does need to reach Victus directly -- the phone's browser does that itself (see index.html),
# not this app, since there's no reason for the two machines to trust each other server-to-server.
SOURCES = {"cam": ("cam_hi", "cam_lo"), "victus": ("victus_hi", "victus_lo")}


def build_master(source="cam"):
    hi_path, lo_path = SOURCES.get(source, SOURCES["cam"])
    variants = []
    for label, mediamtx_path in (("hi", hi_path), ("lo", lo_path)):
        code, _, body = proxy_stream(mediamtx_path, "index.m3u8")
        if code != 200:
            continue
        lines = body.decode().splitlines()
        for i, line in enumerate(lines):
            if line.startswith("#EXT-X-STREAM-INF:") and i + 1 < len(lines):
                variants.append(f"{line}\n{label}/{lines[i + 1]}\n")
                break
    if not variants:
        return None
    return ("#EXTM3U\n" + "".join(variants)).encode()


def proxy_stream(mediamtx_path: str, path_and_query: str):
    """Fetch one HLS file (playlist or segment) from mediamtx and return (status, content_type, body)."""
    req = urllib.request.Request(f"{_MEDIAMTX_HOST}/{mediamtx_path}/{path_and_query}",
                                  headers={"Authorization": f"Basic {_BASIC_AUTH}"})
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


def take_snapshot():
    """One JPEG frame, right now. If the camera is already streaming, grab it from the live
    rendition (the device is exclusively held by that ffmpeg process, so a second -f v4l2 open
    would fail); otherwise do a quick one-shot open of the device itself."""
    if camera_on():
        # mediamtx enforces the same read auth on RTSP as HLS -- needs the viewer credentials too.
        rtsp_url = f"rtsp://viewer:{TOKEN}@127.0.0.1:8554/cam_hi"
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
               "-i", rtsp_url, "-frames:v", "1", "-f", "image2", "pipe:1"]
    else:
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "v4l2", "-input_format", "mjpeg",
               "-video_size", "640x480", "-i", VIDEO_DEVICE, "-frames:v", "1", "-f", "image2", "pipe:1"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=8)
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except subprocess.TimeoutExpired:
        pass
    return None


def log_access(remote_addr, path, ok):
    """Append every request's auth outcome -- cheap brute-force visibility. The token has ~144
    bits of entropy so guessing it is not a realistic risk, but a growing pile of "denied" lines
    from an unexpected address is worth knowing about regardless."""
    try:
        with open(ACCESS_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {remote_addr} {'OK' if ok else 'DENIED'} {path}\n")
    except OSError:
        pass


def read_sessions(limit=20):
    """Pair up on/off lines from the session log into (start, end, duration_s) records, most
    recent first. An unmatched trailing "on" (camera currently live, or killed uncleanly) shows
    as an open-ended entry rather than being dropped."""
    if not SESSIONS_LOG.exists():
        return []
    lines = SESSIONS_LOG.read_text().splitlines()[-500:]
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    sessions, pending_on = [], None
    for e in events:
        if e.get("event") == "on":
            pending_on = e.get("ts")
        elif e.get("event") == "off" and pending_on:
            sessions.append({"start": pending_on, "end": e.get("ts")})
            pending_on = None
    if pending_on:
        sessions.append({"start": pending_on, "end": None})
    return list(reversed(sessions))[:limit]


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
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        u = urlparse(self.path)
        prefix = "/" + TOKEN + "/"
        if u.path == "/health":
            return "health", ""
        ok = len(u.path) >= len(prefix) and hmac.compare_digest(u.path[:len(prefix)], prefix)
        log_access(self.client_address[0], u.path, ok)
        if ok:
            return "ok", u.path[len(prefix):]
        return None, None

    def do_GET(self):
        kind, rest = self._auth()
        if kind == "health":
            return self._send(200, b"ok")
        if kind != "ok":
            return self._send(404)
        if rest in ("", "index.html"):
            html = (HERE / "index.html").read_bytes().replace(b"__TOKEN__", TOKEN.encode()) \
                .replace(b"__VICTUS_ENDPOINT__", VICTUS_ENDPOINT.encode())
            return self._send(200, html, "text/html; charset=utf-8")
        if rest == "hls.js":
            return self._send(200, (HERE / "hls.js").read_bytes(), "application/javascript")
        if rest == "manifest.json":
            manifest = {"name": "Home Camera", "short_name": "Camera", "start_url": "./",
                        "display": "standalone", "background_color": "#0f1216", "theme_color": "#0f1216"}
            return self._send(200, json.dumps(manifest).encode(), "application/manifest+json")
        if rest == "api/status":
            return self._send(200, json.dumps({"on": camera_on()}).encode(), "application/json")
        if rest == "api/sessions":
            return self._send(200, json.dumps(read_sessions()).encode(), "application/json")
        if rest == "api/snapshot.jpg":
            jpg = take_snapshot()
            if jpg is None:
                return self._send(503, b"snapshot failed (camera busy or unavailable)")
            return self._send(200, jpg, "image/jpeg")
        if rest == "stream/master.m3u8" or rest == "stream/victus/master.m3u8":
            source = "victus" if rest.startswith("stream/victus/") else "cam"
            master = build_master(source)
            if master is None:
                return self._send(503, b"camera is off or not yet publishing")
            return self._send(200, master, "application/vnd.apple.mpegurl")
        if (rest.startswith("stream/hi/") or rest.startswith("stream/lo/")
                or rest.startswith("stream/victus/hi/") or rest.startswith("stream/victus/lo/")):
            is_victus = rest.startswith("stream/victus/")
            tail = rest[len("stream/victus/"):] if is_victus else rest[len("stream/"):]
            hi_path, lo_path = SOURCES["victus" if is_victus else "cam"]
            rendition = hi_path if tail.startswith("hi/") else lo_path
            path_and_query = tail.split("/", 1)[1]
            if "?" in self.path:
                path_and_query += "?" + self.path.split("?", 1)[1]
            code, ctype, body = proxy_stream(rendition, path_and_query)
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
