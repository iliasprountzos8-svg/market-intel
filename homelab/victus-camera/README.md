# Victus camera agent

A second, independent camera source for the same Home Camera phone page (`homelab/camera/`),
this time from Victus's own webcam. Unlike the homelab, Victus isn't always-on, so this only
works while Victus is powered on -- and even then, capture is strictly on-demand, same as the
homelab camera: nothing touches the webcam until `/api/start` is called.

## Why this is a separate, smaller agent

The homelab's `camera_app.py` handles viewing, the HLS master playlist, snapshots and session
history for both cameras -- once Victus is publishing into the homelab's mediamtx, watching it
needs nothing Victus-specific at all. The **only** thing that has to run on Victus is starting and
stopping its own webcam capture, so that's the only thing this agent does. There is deliberately
no trust relationship between the two machines' servers: the phone's browser calls this agent
directly over Tailscale for start/stop, and calls the homelab for everything else (see
`homelab/camera/index.html`'s `SOURCES` object).

## Setup (one-time)

Needs `ffmpeg` and Python 3 on PATH (both already present via winget/python.org on this machine).

```
python victus_camera_agent.py
```

First run creates `.control-token` (not in this repo) next to the script. Copy that token's
value into `~/services/camera/victus-endpoint.txt` on the **homelab** as
`http://<victus-tailscale-ip>:8097/<token>/` -- that's how the phone page's Victus tab knows
where to send start/stop, and is the only piece of shared config between the two machines (it's
page config the browser reads, not a server-to-server credential).

Auto-start at logon (so the toggle works any time Victus is on, without capture itself running
continuously):

```powershell
$py = "C:\Users\<you>\AppData\Local\Programs\Python\Python311\pythonw.exe"
$script = "<repo>\homelab\victus-camera\victus_camera_agent.py"
$action = New-ScheduledTaskAction -Execute $py -Argument "`"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "VictusCameraAgent" -Action $action -Trigger $trigger -Settings $settings -Force
```

`pythonw.exe`, not `python.exe`, so no console window appears at logon.

## mediamtx side (homelab)

`victus_hi`/`victus_lo` are published with no credentials at all -- gated purely by source IP
(Victus's Tailscale IP, in `authInternalUsers`), the same way the homelab's own capture is gated
to `127.0.0.1`. The `viewer` read user's path pattern covers both `cam_(hi|lo)` and
`victus_(hi|lo)` with the one shared password.

## Known gaps (unlike the homelab camera)

- No snapshot endpoint, no session log -- the phone page hides those for the Victus tab.
- No hardening pass (no Windows equivalent of the systemd sandboxing on the homelab side) --
  this agent binds to the Tailscale IP only and nothing else listens on that port, which is the
  load-bearing protection; there's no Windows firewall rule added beyond what's already default.
- If Victus is asleep/off, the Victus tab just shows "unreachable" -- there's no wake-on-LAN or
  similar, by design (this isn't meant to be an always-available camera).
