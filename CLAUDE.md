# market-intel + homelab: working notes (read first, keep short)

## Access and deploy
- Server: `ssh ilias@homelab` (Tailscale, passwordless sudo). Server code lives in `~/market-intel` and is NOT a git repo: edit locally, `scp` to the server, mirror server-only edits back by hand.
- `run_cycle.py` is CRLF in the repo. Postgres is `marketdb-db-1` on port 5433. Never print secrets.
- Branches: `homelab-hardening` (PR #4), `news-intel-v2` (PR #5, base homelab-hardening). The user merges.

## Rules
- Everything must cost nothing. Anything calling Claude is opt-in and off by default (reader needs `MI_READER=1`).
- Report honestly (no proven edge; gold labels are LLM-made). Short list-style answers.
- Tell before risky changes. Verify ports/network after reboots, not just "Up".

## Layout
- `analysis/`: story_logic, event_rules, stories, embed_logic + merge_stories (nlp venv), briefing, calibration, reader (off).
- `ingest/`: sec8k. `nlp/`: FinBERT, embed. `homelab/`: systemd units, bin scripts, agents, hq app, migrations. `deal-radar/`: flip scoring.
- Tests: `python -m unittest discover -s tests` (83 pass).

## Gotchas
- In inline python heredocs, `\b`/`\n` escapes get collapsed (a `\b` became a backspace byte). Use the Edit/Write tools for regexes.
- `grep -v "^#"` hides output lines starting with '#'.
- Patch running bash scripts via temp file + atomic replace.

## Token-saving conventions
- Prefer scripts and timers over repeated manual checks. Use Sonnet; Opus only for hard design.
- One task per session, commit at the end, then start fresh.
