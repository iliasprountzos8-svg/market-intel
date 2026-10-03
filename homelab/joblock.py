"""Shared flock-based job runner: launches an allow-listed shell command exactly once at a time.

Used by hq_app.py and sync_daemon.py so the "is this job already running" / "start it" logic
(and the lockfile paths it depends on) lives in one place instead of being copy-pasted per app.
"""
import fcntl
import subprocess


def is_running(lock_path):
    try:
        f = open(lock_path, "a+")
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


def start_job(cmd, lock_path, cwd):
    if is_running(lock_path):
        return "already running"
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                     start_new_session=True, cwd=str(cwd))
    return "started"
