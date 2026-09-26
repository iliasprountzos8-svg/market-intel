#!/usr/bin/env python3
"""Market Intel control page: private phone UI to pull data / generate a digest.
Tailscale-only, secret-path token, no sudo, no third-party deps."""
import fcntl, hmac, json, subprocess, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MI = Path.home() / "market-intel"
LOGS = MI / "logs"
ENV = {}
for line in (MI / ".env").read_text().splitlines():
    if "=" in line and not line.startswith("#"):
        k, v = line.split("=", 1)
        ENV[k.strip()] = v.strip()
TOKEN = (MI / ".control-token").read_text().strip()
SB_URL = ENV["SUPABASE_URL"].rstrip("/")
SB_KEY = ENV["SUPABASE_SERVICE_KEY"]
DASH = "https://market-intel-chi-wheat.vercel.app"
BIND = ("100.83.128.73", 8095)
JOBS = {"pull": ["/bin/bash", str(MI / "run_pull.sh")],
        "digest": ["/bin/bash", str(MI / "run_digest.sh")]}
LOCKS = {"pull": "/tmp/mi-cycle.lock", "digest": "/tmp/mi-digest.lock"}
STATUS = {"pull": LOGS / "cycle-status.txt", "digest": LOGS / "digest-status.txt"}


def is_running(job):
    try:
        f = open(LOCKS[job], "a+")
    except OSError:
        return False
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        f.close()


def start_job(job):
    if is_running(job):
        return "already running"
    subprocess.Popen(JOBS[job], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(MI))
    return "started"


def sb(path, count=False):
    headers = {"apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY}
    if count:
        headers.update({"Prefer": "count=exact", "Range": "0-0"})
    req = urllib.request.Request(SB_URL + "/rest/v1/" + path, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        if count:
            return int(r.headers.get("Content-Range", "*/0").split("/")[-1])
        return json.loads(r.read().decode("utf-8"))


def read_status(job):
    try:
        return STATUS[job].read_text().strip()
    except OSError:
        return "never run"


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return None


def status():
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    out = {"pull": {"running": is_running("pull"), "text": read_status("pull")},
           "digest": {"running": is_running("digest"), "text": read_status("digest")},
           "dashboard": DASH, "now": datetime.now(timezone.utc).isoformat()}
    try:
        out["articles_24h"] = sb("articles?select=id&published_at=gte." + since, count=True)
        out["high_24h"] = sb("articles?select=id&ai_relevance_score=gte.70&published_at=gte." + since, count=True)
        d = sb("digests?select=created_at,summary,guidance,key_themes&order=created_at.desc&limit=1")
        out["digest_row"] = d[0] if d else None
        out["data"] = {"sources_enabled": sb("sources?select=id&enabled=eq.true", count=True),
                       "sources_failing": sb("sources?select=id&fail_count=gte.3", count=True),
                       "articles_total": sb("articles?select=id", count=True),
                       "finbert": sb("article_scores?select=article_id&model=eq.finbert", count=True)}
    except Exception as e:
        out["db_error"] = str(e)[:120]
    out["lab"] = read_json(LOGS / "lab-daily.json")
    tr, se = read_json(LOGS / "track-record.json"), read_json(LOGS / "signal-eval.json")
    out["track"] = {"verdict": tr.get("verdict"), "n": tr.get("n_decided"), "hit": tr.get("hit_rate")} if tr else None
    out["sigeval"] = (se.get("report", "").splitlines() or [""])[-1] if se else None
    return out


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="apple-mobile-web-app-capable" content="yes"><title>Market Intel</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#14171c;--mut:#6a7280;--acc:#2563eb;--ok:#15803d;--bad:#b91c1c;--line:#e3e6ea}
@media(prefers-color-scheme:dark){:root{--bg:#0f1216;--card:#181c22;--fg:#eceff3;--mut:#9aa3b0;--acc:#5b8cff;--ok:#4ade80;--bad:#f87171;--line:#262c35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif;padding:16px;max-width:640px;margin:auto}
h1{font-size:1.3rem;margin:.2rem 0 1rem}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:12px}
.row{display:flex;gap:10px}.stat{flex:1;text-align:center}.stat b{display:block;font-size:1.6rem}.stat span{color:var(--mut);font-size:.8rem}
button{width:100%;padding:16px;border:0;border-radius:12px;font-size:1rem;font-weight:600;background:var(--acc);color:#fff;margin-bottom:10px}
button:disabled{opacity:.55}.sec{background:transparent;color:var(--acc);border:1px solid var(--acc)}
.mut{color:var(--mut);font-size:.85rem}.ok{color:var(--ok)}.bad{color:var(--bad)}pre{white-space:pre-wrap;font:inherit;margin:.4rem 0 0}
a{color:var(--acc)}
</style></head><body>
<h1>Market Intel</h1>
<div class="card row"><div class="stat"><b id="a24">-</b><span>articles, 24h</span></div>
<div class="stat"><b id="h24">-</b><span>high relevance</span></div></div>
<button id="bpull" onclick="run('pull')">Pull data now</button>
<button id="bdig" onclick="run('digest')">Generate digest</button>
<button class="sec" onclick="location.href=DASH">Open dashboard</button>
<div class="card"><div class="mut">Data pull</div><div id="spull">-</div>
<div class="mut" style="margin-top:8px">Digest job</div><div id="sdig">-</div></div>
<div class="card"><div class="mut">Latest digest <span id="dwhen"></span></div><pre id="dtxt">-</pre>
<pre id="dguid" class="mut"></pre></div>
<div class="card"><div class="mut">Data and prediction lab</div><div id="ldata">-</div>
<pre id="llab" class="mut"></pre><pre id="ltrack" class="mut"></pre></div>
<p class="mut" id="msg"></p>
<script>
var DASH="";
function ago(t){var s=(Date.now()-new Date(t).getTime())/1000;if(isNaN(s))return"";if(s<90)return"just now";if(s<5400)return Math.round(s/60)+" min ago";if(s<172800)return Math.round(s/3600)+" h ago";return Math.round(s/86400)+" d ago"}
function fmt(j,el){var t=j.text;var r=j.running;var e=document.getElementById(el);
 var m=/^(started|finished) (\\S+)(?: rc=(\\d+))?/.exec(t);
 if(r){e.innerHTML='<b>Running...</b> <span class="mut">'+(m?ago(m[2]):"")+'</span>'}
 else if(m&&m[1]=="finished"){var ok=m[3]=="0";e.innerHTML='<span class="'+(ok?"ok":"bad")+'">'+(ok?"OK":"FAILED")+'</span> <span class="mut">'+ago(m[2])+'</span>'}
 else e.textContent=t}
function fillLab(s){var d=s.data;if(d){document.getElementById("ldata").textContent=d.sources_enabled+" sources ("+d.sources_failing+" failing) | "+d.articles_total+" articles | "+d.finbert+" FinBERT-scored"}
 var l=s.lab,t="";if(l){t="Lab "+l.as_of+" | "+l.open_paper_positions+" open paper positions\n";for(var b in l.top5){t+=b+": "+l.top5[b].join(", ")+"\n"}
  for(var k in l.ledger){var v=l.ledger[k];t+="settled "+k+": n="+v.closed_positions+", net vs SPY "+v.mean_net_excess_pct.toFixed(2)+"%\n"}}
 document.getElementById("llab").textContent=t;
 var u="";if(s.track){u+="Calls: "+s.track.n+" decided. "+(s.track.verdict||"")+"\n"}if(s.sigeval){u+="Signal test: "+s.sigeval}
 document.getElementById("ltrack").textContent=u}
function load(){fetch("status").then(r=>r.json()).then(function(s){DASH=s.dashboard;
 document.getElementById("a24").textContent=s.articles_24h==null?"?":s.articles_24h;
 document.getElementById("h24").textContent=s.high_24h==null?"?":s.high_24h;
 fmt(s.pull,"spull");fmt(s.digest,"sdig");fillLab(s);
 document.getElementById("bpull").disabled=s.pull.running;document.getElementById("bdig").disabled=s.digest.running;
 var d=s.digest_row;if(d){document.getElementById("dwhen").textContent="("+ago(d.created_at)+")";
  document.getElementById("dtxt").textContent=d.summary||"";document.getElementById("dguid").textContent=d.guidance?"Guidance: "+d.guidance:""}
 if(s.db_error)document.getElementById("msg").textContent="Database: "+s.db_error}).catch(function(){document.getElementById("msg").textContent="Cannot reach server (is Tailscale on?)"})}
function run(j){document.getElementById("msg").textContent="";fetch("run/"+j,{method:"POST"}).then(r=>r.json()).then(function(x){document.getElementById("msg").textContent=j+": "+x.result;load()})}
load();setInterval(load,4000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _route(self):
        p = self.path.split("?")[0]
        if p == "/health":
            return ("health", None)
        prefix = "/" + TOKEN + "/"
        if len(p) >= len(prefix) and hmac.compare_digest(p[:len(prefix)], prefix):
            return ("ok", p[len(prefix):])
        return (None, None)

    def do_GET(self):
        kind, rest = self._route()
        if kind == "health":
            return self._send(200, b"ok")
        if kind != "ok":
            return self._send(404)
        if rest == "":
            return self._send(200, PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if rest == "status":
            return self._send(200, json.dumps(status()).encode(), "application/json")
        self._send(404)

    def do_POST(self):
        kind, rest = self._route()
        if kind != "ok" or not rest.startswith("run/") or rest[4:] not in JOBS:
            return self._send(404)
        result = start_job(rest[4:])
        self._send(200, json.dumps({"result": result}).encode(), "application/json")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(BIND, H).serve_forever()
