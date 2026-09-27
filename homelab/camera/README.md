# Home camera (live view, phone-toggled)

Toggle the laptop's webcam on/off from a phone and watch it live, Tailscale-only. No recording,
no auto-stop (by design) -- see camera_app.py's docstring for the reasoning.

## Architecture

- `mediamtx` (vendored binary, not in this repo -- see Deploy below) relays RTSP-in to HLS-out.
  Idle and cheap when nothing is publishing; runs always-on as `mediamtx.service`.
- `homelab-camera.sh` captures `/dev/video0` with ffmpeg and publishes to mediamtx over RTSP.
  Only runs while the camera is toggled on -- this is the part that actually touches the
  hardware, and it exits (and gets ntfy'd as OFF) the moment it's stopped.
- `camera_app.py` is the control server: toggle API (`api/start`/`api/stop`/`api/status`) plus a
  same-origin proxy for the HLS stream itself. The proxy exists because mediamtx's HLS auth is
  cookie-based, which breaks across a cross-origin request (its own port, 8888, is a different
  browser origin from this app's port, 8096) -- every real browser's XHR/hls.js request would
  silently fail without it. Runs always-on as `camera-app.service`.
- `index.html` is the phone page: toggle button + `<video>` player (native HLS on Safari, hls.js
  vendored locally on Chrome/Android).

## Security

- Tailscale-only: mediamtx and camera_app.py both bind to the Tailscale IP specifically, and the
  homelab's ufw default-deny (see homelab-watchdog.sh's context) means nothing else can reach
  either port.
- Secret-path token (`~/services/camera/.control-token` on the server, not in this repo) gates
  every control-app route, and doubles as mediamtx's HLS read password.
- ffmpeg can only *publish* to mediamtx from `127.0.0.1` (mediamtx auth config) -- nothing
  remote can inject a fake stream.

## Deploy (server-side, one-time)

```bash
# mediamtx (single binary, not vendored in git)
V=$(curl -s https://api.github.com/repos/bluenviron/mediamtx/releases/latest | grep -oP '"tag_name": "\K[^"]+')
curl -sL -o /tmp/mediamtx.tar.gz "https://github.com/bluenviron/mediamtx/releases/download/${V}/mediamtx_${V}_linux_amd64.tar.gz"
sudo mkdir -p /opt/mediamtx && sudo chown -R ilias:ilias /opt/mediamtx
tar xzf /tmp/mediamtx.tar.gz -C /opt/mediamtx

# hls.js (vendored locally so the phone page doesn't depend on an external CDN)
curl -sL https://cdn.jsdelivr.net/npm/hls.js@1/dist/hls.min.js -o ~/services/camera/hls.js

# secret token (also becomes mediamtx's HLS read password, see camera_app.py)
python3 -c "import secrets; print(secrets.token_urlsafe(24))" > ~/services/camera/.control-token
chmod 600 ~/services/camera/.control-token
```

Then edit `/opt/mediamtx/mediamtx.yml`: disable `rtmp`/`webrtc`/`srt`, bind `rtspAddress` to
`127.0.0.1:8554` and `hlsAddress` to the Tailscale IP, and restrict `authInternalUsers` so
`publish` on path `cam` is `127.0.0.1`-only and `read` on path `cam` requires a `viewer` user
whose password is the token above. Copy `camera_app.py`, `index.html`, `hls.js`,
`homelab-camera.sh` and `../joblock.py` into `~/services/camera/`, then install both systemd
units from `../systemd/`.

URL to open on the phone: `http://<tailscale-ip>:8096/<token>/`
