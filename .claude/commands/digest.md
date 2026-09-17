---
description: Pull unprocessed market-intel articles, read/reason about them, write today's digest
---

Give me today's market-intel read.

Context: personal market-intelligence pipeline for a retail investor. Holdings:
VWCE (broad index, ~89% of portfolio) + a small direct sleeve in
GOOGL/MSFT/NVDA/ASML (exact share counts live in Supabase's `holdings` table,
not needed for this task -- just know these are the tracked tickers, plus
broad-market/macro themes relevant to VWCE).

Steps:
1. `cd analysis && pip install -q -r requirements.txt` (env vars come from
   `analysis/.env` / `scraper/.env`, already present locally -- no need to
   set them manually).
2. `python cli.py fetch-unprocessed --limit 50`
3. Read the results yourself and reason about them like a real market
   analyst -- don't just repeat headlines. Prioritize anything touching
   NVDA, MSFT, GOOGL, ASML, the Fed/rates, or broad macro/energy themes that
   move a global index fund. Per the project's own README: "don't trust
   keyword-based auto-triage for anything you'd actually act on --
   hand-verify the high-relevance subset (relevance >= 60) by actually
   reading it."
4. For each genuinely important article, write your real conclusions back:
   `python cli.py mark-processed <article_id> --sentiment bullish|bearish|neutral|mixed --relevance 0-100 --tickers TICK1,TICK2 --summary "..." --action "watch|hold|no action|..." --risk none|low|medium|elevated --confidence 0-100`
5. Finish with one digest summarizing the whole window:
   `python cli.py write-digest --hours 24 --summary "..." --themes "theme1,theme2" --guidance "..." --watchlist '{"NVDA": "...", "GOOGL": "..."}'`
6. Run `python notify.py` -- this is the step that actually updates the
   live app for me: it checks `app_settings`, and if push/email are
   enabled, notifies on this new digest and any newly-marked
   high-relevance articles (idempotent via `notified_at`, so re-running
   `/digest` never double-notifies). The dashboard itself updates live via
   Supabase realtime regardless of this step -- `notify.py` is what reaches
   me outside the dashboard (phone push, email).
7. This is a data-only task via the Supabase REST API through `cli.py` --
   don't commit, push, or modify any files in the repo.
8. End with a plain-text summary of the digest (2-4 short paragraphs) so I
   don't have to open the dashboard to see what you found.

Gotchas from past runs, avoid repeating these:
- Passing `--summary`/watchlist text through bash double-quotes: a literal
  `$` followed by digits (e.g. `$341.43`) gets interpreted as a shell
  variable and silently vanishes. Use single quotes for any string
  containing `$`, or write the payload to a small Python script instead of
  inline bash.
- `--themes` splits on every comma in the string, including commas inside
  a single theme's own parenthetical aside -- keep each theme phrase
  comma-free, or it silently becomes two array entries.
- Don't double `%` signs (no `%%` escaping needed here, this isn't
  printf) -- write `70%`, not `70%%`.
- After writing, it's worth reading the row back (or checking the
  dashboard) once to catch exactly this class of formatting bug before
  calling it done.
