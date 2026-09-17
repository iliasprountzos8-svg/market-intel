# Prompt for Antigravity: Fortify Market Intel

Paste everything below into Antigravity as the initial task. It's written to
be self-contained — Antigravity has no memory of how this project was built,
so read it fully before touching code.

---

## Project location

```
C:\Users\ilias\Desktop\Portfolio Strategy Tests\market-intel
```

Read `README.md` in that folder first — it documents the full architecture,
setup, and every design decision made so far. Do not skip it.

## What this project is

A personal market-intelligence system for a retail investor (real Trading212
account, holdings: VWCE 75%+ / GOOGL / MSFT / NVDA / ASML sleeve). Pipeline:

```
scraper/  -> RSS feeds + SEC EDGAR filings -> Supabase (Postgres)
analysis/ -> CLI for reading unprocessed articles + writing AI verdicts back
dashboard/ -> Next.js 16 site, WSJ-newspaper visual style (white bg, black
              serif headlines, dark index ticker strip, hairline rules)
run_cycle.py -> orchestrates scrape -> fulltext -> correlate -> outcome-check
```

Live data flows through Supabase; the dashboard reads it via realtime
subscriptions. `app/api/quotes/route.ts` proxies Yahoo Finance for live
prices (no API key, no CORS issue since it's server-side).

## Non-negotiable constraints — respect these

1. **Free-tier only.** No paid APIs anywhere in this stack. Supabase free
   tier, GitHub Actions free tier, Yahoo Finance's public unauthenticated
   endpoints, free RSS feeds. Do not introduce a service that requires a
   credit card.
2. **AI analysis stays on-demand, not a scheduled paid API call.** This was
   a deliberate choice to avoid recurring LLM API cost. The `analysis/cli.py`
   pattern (fetch-unprocessed -> a human/agent reads and reasons -> write
   verdict back via mark-processed) is the intended design. If you build
   "improved sentiment analysis," it should either (a) be a better
   *rule-based/lexicon* pass usable as a bulk-triage fallback, exactly like
   `scraper` has no equivalent today but `analysis/` historically used ad hoc
   keyword rules, or (b) stay in the on-demand-read pattern. Do not wire in
   a scheduled OpenAI/Anthropic/Gemini API call that bills per run unless you
   explicitly flag this tradeoff to the user first and get confirmation —
   don't just add it.
3. **Don't break the visual design.** The dashboard's WSJ-newspaper aesthetic
   (see `dashboard/app/globals.css`) was iterated on deliberately: white
   background, black ink, `Playfair Display` masthead, `Source Serif 4` body
   copy, `Fira Code` for numbers, thin hairline borders, amber (`--accent`)
   for category tags, a dark `index-strip` ticker bar at the very top. Green
   (`--bull`) / red (`--bear`) are reserved strictly for financial
   gains/losses, never decorative. New pages/components must match this
   system, not introduce a different style.
4. **Nothing is deployed yet.** This all runs locally. If your task includes
   deployment, that's a distinct, explicit step — don't assume Vercel/GitHub
   Actions secrets already exist.
5. **Real money is involved.** `dashboard/lib/holdings.ts` has real portfolio
   share counts (manually entered, no live Trading212 API exists). Don't
   fabricate or "helpfully" adjust these numbers — if something about them
   seems wrong, ask, don't guess.

## What already exists (don't rebuild, extend)

- `scraper/sources.py` — 12 RSS feeds, tuned over several iterations (dead
  feeds already swapped out, spam-heavy feeds already tested and rejected —
  see README's "Sources" section for what was tried and why it was
  rejected, so you don't repeat that work).
- `scraper/sec_filings.py` — SEC EDGAR 8-K/6-K filings for the 4 watchlist
  tickers, official and zero-noise, item codes decoded to plain English.
- `scraper/fetch_fulltext.py` — full-article-text backfill via trafilatura,
  with a `BLOCKED_DOMAINS` skip-list (Investing.com is a confirmed dead end
  — Cloudflare-blocked even with browser headers).
- `analysis/correlate.py` — mechanical ticker-clustering, no AI call needed.
- `analysis/check_outcomes.py` — checks logged directional calls against
  real price moves via yfinance, marks correct/incorrect/unclear.
- `dashboard/` — 7 pages: Overview (front-page grid), Portfolio (live value
  from real share counts × live prices, FX-converted), Markets, News
  (searchable/filterable), Watchlist (per-ticker correlated news), Track
  Record, Archive (digest history). Article detail pages at `/news/[id]`.
- Database schema in `supabase/schema.sql` — `articles`, `digests`,
  `ai_calls_log` tables, already has AI-verdict columns
  (`ai_sentiment`, `ai_relevance_score`, `ai_suggested_action`,
  `ai_risk_flag`, `ai_confidence`, `ai_affected_tickers`,
  `ai_correlated_article_ids`).

## What to build — in priority order

### 1. Data quality & reliability fortification

- **Retry/backoff on all network calls.** `scrape.py`, `fetch_fulltext.py`,
  and `sec_filings.py` currently fail silently (print + skip) on a single
  request error. Add exponential backoff (2-3 retries) for transient
  failures (timeouts, 5xx) before giving up on a source.
- **Per-source health tracking.** Add a small `source_health` table (or a
  JSON log) that records, per run: source name, entries fetched, errors. Show
  this somewhere in the dashboard (a new "System" or "Sources" admin page —
  see dashboard features below) so a dead/degraded feed is visible instead of
  silently returning 0 entries forever.
- **Deduplicate near-identical stories across sources.** Right now the same
  underlying story (e.g. "Fed hikes rates") appears as 5+ separate DB rows
  from different feeds. Add a lightweight near-duplicate detector (e.g.
  title similarity via difflib/rapidfuzz above some threshold within a
  ~2-hour window) that links duplicates instead of treating them as distinct
  articles — this directly improves signal-to-noise on the News/Overview
  pages.
- **Data validation on ingest.** Reject/flag rows with obviously broken data
  (empty title, `published_at` implausibly far in the past/future — the DB
  already has a few 2024-dated rows from feed bugs, worth a cleanup pass and
  a going-forward guard).
- **Full-text hit-rate monitoring.** Track and surface the % of articles
  getting real full text vs falling back to RSS summary, per source, so a
  source silently degrading (e.g. a site adding new bot-blocking) is
  noticed.

### 2. Sentiment / relevance analysis quality

- **Build a real rule-based sentiment/relevance scorer** as
  `analysis/classify.py`, meaningfully better than the ad hoc keyword
  matching used historically. Use a proper finance-tuned approach:
  - A weighted lexicon (not just word presence) — e.g. VADER-finance,
    Loughran-McDonald financial sentiment word lists (both free/public),
    scored against title + summary/full_text.
  - Ticker-proximity weighting: a bullish word near a watchlist ticker
    mention should count more than one in an unrelated sentence.
  - Numeric-magnitude parsing: extract %/$ figures ("+34%", "-$500M") and
    let their sign/size influence the sentiment score, not just word choice.
  - Category-aware relevance: SEC filings, watchlist-ticker mentions, and
    Fed/macro items should score relevance differently than generic
    small-cap analyst-rating blurbs (there's precedent for this in the old
    manual classification patterns in git history / README if you look, but
    build it as real code, not one-off scripts).
  - This should be usable as a genuine **first-pass classifier** that runs
    automatically in `run_cycle.py` (no AI/API call, so it respects
    constraint #2), with `ai_confidence` reflecting that it's a
    rule-based/low-confidence pass — leaving room for a human/on-demand-AI
    pass to override high-stakes items later, exactly like the existing
    workflow already distinguishes bulk-triage confidence (~40) from
    hand-verified confidence (~55-65).
- **Confidence calibration against `check_outcomes.py` results.** Once
  there's enough decided-call history, add a script that checks whether
  higher-stated-confidence calls are actually more often correct — if not,
  confidence is miscalibrated and should be adjusted.

### 3. Dashboard — new pages/features for easier control

- **`/admin` or `/system` page**: source health (see above), last cycle run
  time/status, unprocessed-article count, full-text hit rate, a manual
  "trigger scrape" button if feasible (calling a Next.js API route that
  shells out to the Python scripts, or at minimum shows the exact CLI
  commands to run).
- **Bulk actions on the News page**: multi-select articles, bulk-tag
  sentiment/relevance, bulk-dismiss low-relevance items — right now every
  correction requires a direct DB write via a Python script.
- **Inline editing on article detail pages**: let a human override
  `ai_sentiment`/`ai_relevance_score`/`ai_suggested_action` directly from the
  UI instead of needing `cli.py mark-processed`.
- **Source management UI**: view/enable/disable sources from
  `scraper/sources.py` without editing Python directly (could read/write a
  JSON config the Python scraper also reads, instead of hardcoded Python).
- **Digest editor**: a form to write a new digest (summary, themes,
  guidance, watchlist notes) from the dashboard instead of the CLI's
  `write-digest` command with escaped JSON strings.

### 4. Backend — easier control

- **Consolidate scattered one-off scripts.** Over the course of building
  this, several `apply_analysisN.py` scratch scripts were written directly
  in the session's temp scratchpad to bulk-apply manual classifications —
  those are gone (session-scoped), but the *pattern* (ad hoc Python for bulk
  DB edits) should be replaced with a proper reusable CLI subcommand in
  `analysis/cli.py`, e.g. `cli.py bulk-classify --rule-based` wired to the
  new `classify.py` from section 2.
- **Config file instead of hardcoded Python constants** for things like
  `scraper/sources.py`'s SOURCES list, `dashboard/lib/holdings.ts`'s
  HOLDINGS, `app/api/quotes/route.ts`'s SYMBOLS — consider a single
  `config.json` at the project root that both the Python scraper and the
  Next.js dashboard read, so there's one place to edit instead of three
  languages' worth of hardcoded arrays. (Only do this if it doesn't add
  more complexity than it removes — a plain JSON file both sides can parse
  is fine; don't build a whole config-management abstraction.)
- **Logging.** Scripts currently `print()` to stdout. Add structured logging
  (even just consistent `[timestamp] [level] message` formatting) so
  GitHub Actions logs and local runs are easier to scan for problems.

## Constraints on how you work

- Test everything against the real local dev server before calling it done
  (`npm run dev` in `dashboard/`, or `python run_cycle.py` at the project
  root) — this project's whole history is full of "looks right in the code,
  broke on actual render" lessons (Turbopack cache staleness after adding
  new files needed `.next` deleted + server restart more than once — if you
  hit inexplicable "module not found" errors for files that definitely
  exist, that's almost certainly it).
- Keep the free-tier framing intact in any README updates you make.
- If you genuinely think a constraint above should be relaxed (e.g. "the
  sentiment analysis would be dramatically better with a real LLM call"),
  say so explicitly and ask, backed by a concrete cost estimate — don't
  silently work around it.
