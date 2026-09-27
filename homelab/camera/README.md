# Home camera (live view, phone-toggled)

Toggle the laptop's webcam on/off from a phone and watch it live, Tailscale-only. No recording,
no motion detection, no auto-stop (all by explicit design choice) -- see camera_app.py's
docstring for the reasoning.

A second, independent camera source (Victus's own webcam) can publish into this same mediamtx and
show up as a second tab on the phone page -- see `../victus-camera/README.md`. This app doesn't
need to know anything Victus-specific to serve it: viewing/proxying/master-playlist logic is
already source-parameterized (`SOURCES` in camera_app.py), and the phone's browser talks to
Victus's own tiny agent directly for start/stop.

## Features

- Live view with adaptive quality (hi/lo HLS renditions, auto-switches on a bad connection)
- Snapshot: one JPEG right now, without waiting for the stream to spin up
- Session log: on/off history with duration, visible on the page
- Home-screen mode (PWA manifest) + a `?go=on`/`?go=off` quick action for a one-tap
  Shortcuts/bookmark flow instead of opening the page and tapping the button
- Periodic ntfy reminder while live (substitutes for auto-stop, which was deliberately not built)
- Per-request access logging for cheap brute-force visibility

## Architecture

- `mediamtx` (vendored binary, not in this repo -- see Deploy below) relays RTSP-in to HLS-out.
  Idle and cheap when nothing is publishing; runs always-on as `mediamtx.service`, hardened
  (`ProtectSystem=strict`, dropped capabilities, `PrivateDevices=true` since it never touches
  camera hardware).
- `homelab-camera.sh` captures `/dev/video0` **once** and publishes **two** encodes (`cam_hi`
  640x480/~800kbps, `cam_lo` 320x240/~200kbps) to mediamtx over RTSP, so quality can adapt without
  a second device open. Only runs while the camera is toggled on -- this is the part that actually
  touches the hardware, and it exits (and gets ntfy'd as OFF, with the session logged) the moment
  it's stopped.
- `camera_app.py` is the control server: toggle/snapshot/sessions API plus a same-origin proxy for
  the HLS stream itself. The proxy exists because mediamtx's HLS auth is cookie-based, which
  breaks across a cross-origin request (its own port, 8888, is a different browser origin from
  this app's port, 8096) -- every real browser's XHR/hls.js request would silently fail without
  it. It also resolves mediamtx's own per-rendition `index.m3u8` (itself a single-variant master
  with a per-request session id, not a flat media playlist) into one real multi-variant master
  playlist on every request -- a hand-written master pointing at those URLs directly is invalid
  HLS that no player handles. Runs always-on as `camera-app.service`; hardened, but deliberately
  *not* `PrivateDevices`/`DeviceAllow`-restricted since the spawned ffmpeg needs real device
  access and getting a device allow-list wrong would silently break capture.
- `index.html` is the phone page: toggle + snapshot buttons, session list, `<video>` player
  (native HLS on Safari, hls.js vendored locally on Chrome/Android).

## Security

- Tailscale-only: mediamtx and camera_app.py both bind to the Tailscale IP specifically, and the
  homelab's ufw default-deny (see homelab-watchdog.sh's context) means nothing else can reach
  either port. mediamtx's own auth config also restricts the `viewer` read user to the Tailscale
  CIDR (100.64.0.0/10) plus localhost, as defense in depth independent of ufw.
- Secret-path token (`~/services/camera/.control-token` on the server, not in this repo) gates
  every control-app route, and doubles as mediamtx's HLS/RTSP read password.
- ffmpeg can only *publish* to mediamtx from `127.0.0.1` (mediamtx auth config) -- nothing
  remote can inject a fake stream.
- `camera-app.service` uses `KillMode=process`, not the systemd default `control-group`: the
  latter would kill the detached ffmpeg capture on every service restart (e.g. a code deploy),
  silently ending an active session with no cleanup. This was a real bug caught during testing.
- Both systemd units run with `ProtectSystem=strict`, dropped capabilities, and kernel/namespace
  restrictions (see the unit files in `../systemd/` for the full list and the device-access
  caveat above).

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
`publish` on path `~^cam_(hi|lo)$` is `127.0.0.1`-only and `read` on that same path pattern
requires a `viewer` user (ips restricted to the Tailscale CIDR + localhost) whose password is the
token above. Copy `camera_app.py`, `index.html`, `hls.js`, `homelab-camera.sh` and
`../joblock.py` into `~/services/camera/`, then install both systemd units from `../systemd/`.

URL to open on the phone: `http://<tailscale-ip>:8096/<token>/`
Quick toggle (Shortcuts/bookmark): `http://<tailscale-ip>:8096/<token>/?go=on` or `?go=off`
