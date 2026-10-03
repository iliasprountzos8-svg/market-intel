"""Repo <-> homelab drift check. The server code in ~/market-intel is NOT a git repo, so server-only edits
silently diverge from the repo (this already happened: four files plus an empty fdr.py). Run before editing
anything that is deployed, and after any hotfix on the server:

    python homelab/drift_check.py            # report; exit 1 if anything differs
    python homelab/drift_check.py --show f   # unified diff (repo -> server) for one file

Compares the committed (HEAD) content with the server file, ignoring CRLF/LF. Reports:
  DIFFERS      both exist, content differs (decide: mirror the server edit into the repo, or redeploy)
  SERVER-ONLY  exists on the server inside a deployed directory but not in the repo
  MISSING      tracked in the repo but not on the server
Never prints file contents except with --show, and never touches either side.
"""
import argparse
import difflib
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOST = "ilias@homelab"
REMOTE_ROOT = "~/market-intel"
DEPLOYED_DIRS = ("analysis/", "ingest/", "lab/", "nlp/", "scraper/")
DEPLOYED_FILES = {"run_cycle.py", "joblock.py", "fdr.py", "logger.py", "mirror_to_supabase.py", "sync_daemon.py", "fdr.py"}
SKIP_PARTS = ("/.venv/", "/__pycache__/", "/node_modules/", ".pyc", "requirements.txt", "/eval/", "/data/")
SKIP_NAMES = {"__init__.py"}


def server_path(repo_path):
    """Where a tracked repo file lives on the server, or None if it is not deployed there."""
    p = repo_path.replace("\\", "/")
    if any(s in "/" + p for s in SKIP_PARTS):
        return None
    if p in DEPLOYED_FILES or p.startswith(DEPLOYED_DIRS):
        return p
    if p.startswith("homelab/scripts/run_") and p.endswith(".sh"):
        return p.split("/")[-1]
    return None


def norm_hash(data):
    return hashlib.md5(data.replace(b"\r\n", b"\n")).hexdigest()


def git_files():
    out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [f for f in out.splitlines() if f]


def head_bytes(path):
    return subprocess.run(["git", "show", f"HEAD:{path}"], cwd=ROOT, capture_output=True, check=True).stdout


def ssh(cmd, stdin=None):
    return subprocess.run(["ssh", HOST, cmd], input=stdin, capture_output=True, check=True).stdout


def remote_hashes(paths):
    lines = [f"cd {REMOTE_ROOT}", "while read f; do",
             '  if [ -f "$f" ]; then h=$(tr -d "\r" < "$f" | md5sum | cut -c1-32); echo "$h $f";',
             '  else echo "MISSING $f"; fi', "done <<'LIST'", *paths, "LIST", ""]
    script = chr(10).join(lines)
    out = ssh("bash -s", stdin=script.encode()).decode().splitlines()
    return {line.split(" ", 1)[1]: line.split(" ", 1)[0] for line in out if " " in line}


def remote_listing():
    """Python/shell files that exist on the server inside deployed directories (to spot SERVER-ONLY files)."""
    cmd = (f"cd {REMOTE_ROOT} && find analysis ingest lab nlp scraper -type f \\( -name '*.py' -o -name '*.sh' \\) "
           "-not -path '*/.venv/*' -not -path '*/__pycache__/*' 2>/dev/null")
    return set(ssh(cmd).decode().split())


def compare(local, remote):
    """local: {repo_path: (server_path, md5)}, remote: {server_path: md5|'MISSING'} -> (differs, missing)."""
    differs, missing = [], []
    for repo_path, (sp, h) in local.items():
        r = remote.get(sp, "MISSING")
        if r == "MISSING":
            missing.append(repo_path)
        elif r != h:
            differs.append(repo_path)
    return sorted(differs), sorted(missing)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", help="print a diff (repo HEAD -> server) for this repo path")
    a = ap.parse_args()
    if a.show:
        sp = server_path(a.show) or a.show
        srv = ssh(f"cd {REMOTE_ROOT} && cat {sp}").decode(errors="ignore").replace("\r\n", "\n").splitlines()
        loc = head_bytes(a.show).decode(errors="ignore").replace("\r\n", "\n").splitlines()
        print("\n".join(difflib.unified_diff(loc, srv, "repo", "server", lineterm="", n=2)))
        return 0
    tracked = [f for f in git_files() if server_path(f)]
    local = {f: (server_path(f), norm_hash(head_bytes(f))) for f in tracked}
    remote = remote_hashes([sp for sp, _ in local.values()])
    differs, missing = compare(local, remote)
    known = {sp for sp, _ in local.values()}
    server_only = sorted(p for p in remote_listing() - known if not any(s in "/" + p for s in SKIP_PARTS)
                         and p.split("/")[-1] not in SKIP_NAMES and p not in {sp for sp in known})
    for f in differs:
        print(f"DIFFERS      {f}")
    for f in server_only:
        print(f"SERVER-ONLY  {f}")
    for f in missing:
        print(f"MISSING      {f}")
    print(f"\nchecked {len(local)} deployed files: {len(differs)} differ, {len(server_only)} server-only, {len(missing)} missing on server")
    return 1 if (differs or server_only or missing) else 0


if __name__ == "__main__":
    sys.exit(main())
