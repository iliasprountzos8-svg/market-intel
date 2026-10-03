"""Rule-based homelab health summary. Reads the facts text from stdin (## sections written by health-report.sh) and prints
'STATUS: OK|WARN|FAIL' plus at most 6 bullets. No LLM, no cost: replaces the daily `claude -p` summary."""
import re
import sys


def sections(text):
    out, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            cur = line[3:].strip().lower()
            out[cur] = []
        elif cur is not None:
            out[cur].append(line)
    return {k: "\n".join(v).strip() for k, v in out.items()}


def summarize(text):
    s = sections(text)
    fail, warn, notes = [], [], []

    def get(name):
        return s.get(name, "")

    m = re.search(r"(\d+)%\s+/", get("disk"))
    if m:
        pct = int(m.group(1))
        (warn if pct > 80 else notes).append(f"disk {pct}% used")
    if get("restarting/unhealthy"):
        warn.append("containers restarting or unhealthy: " + ", ".join(get("restarting/unhealthy").split()))
    if get("failed services"):
        warn.append("failed services: " + ", ".join(l.split()[1] if len(l.split()) > 1 else l for l in get("failed services").splitlines()))
    bad = [(n.strip(), int(c)) for n, c in re.findall(r"^([\w-]+):\s*(\d+)\s*$", get("service failures last 24h"), re.M) if int(c) > 0]
    if bad:
        warn.append("service failures in the last 24h: " + ", ".join(f"{n} x{c}" for n, c in bad))
    if "Failed" in get("last backup result") or re.search(r"Result=(?!success)\w+", get("last backup result")):
        fail.append("last backup did not succeed")
    lc = get("market intel last cycle")
    if lc:
        rc = re.search(r"rc=(\d+)", lc)
        if rc and rc.group(1) != "0":
            warn.append(f"last Market Intel cycle exited with rc={rc.group(1)}")
    ssd = get("ssd health")
    if ssd:
        cw = re.search(r"critical_warning\s*:\s*(\S+)", ssd)
        if cw and cw.group(1).lower() not in ("0", "0x0", "0x00"):
            fail.append("SSD reports a critical warning")
        if "PASSED" not in ssd and "overall" in ssd:
            fail.append("SSD SMART health check did not pass")
        pu = re.search(r"percentage_used\s*:\s*(\d+)", ssd)
        if pu:
            (warn if int(pu.group(1)) > 60 else notes).append(f"SSD {pu.group(1)}% worn")
    pend = re.search(r"^(\d+)\s*$", get("pending updates"), re.M)
    if pend and int(pend.group(1)) > 20:
        warn.append(f"{pend.group(1)} package updates pending")
    usb = get("usb backup (second copy)")
    hrs = re.search(r"\((\d+) h ago\)", usb)
    if usb:
        if "never" in usb:
            warn.append("USB backup has never completed")
        elif hrs and int(hrs.group(1)) > 72:
            warn.append(f"USB backup last succeeded {int(hrs.group(1)) // 24} days ago")
    if get("reboot pending").strip() == "yes":
        warn.append("a reboot is pending")
    fb = re.search(r"finbert backlog[^|]*\|\s*(\d+)", get("market intel data"), re.I)
    if fb and int(fb.group(1)) > 30000:
        warn.append(f"FinBERT backlog {fb.group(1)}")
    down = re.search(r"sources failing[^|]*\|\s*(\d+)", get("market intel data"), re.I)
    if down and int(down.group(1)) > 40:
        warn.append(f"{down.group(1)} data sources failing")

    status = "FAIL" if fail else ("WARN" if warn else "OK")
    bullets = (fail + warn)[:6] or ["everything checked is fine"]
    up = get("uptime").replace("up ", "").strip()
    extra = ("; ".join(notes[:3]) + (f"; up {up}" if up else "")).strip("; ")
    lines = [f"STATUS: {status}", ""] + [f"- {b}" for b in bullets]
    if extra:
        lines.append(f"- numbers: {extra}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(summarize(sys.stdin.read()))
