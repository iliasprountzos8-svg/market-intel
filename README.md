# Market Intel

A free-tier pipeline: RSS/news scraping &rarr; Postgres (Supabase) &rarr; on-demand
Claude analysis &rarr; live dashboard.

```
run_cycle.py  -- orchestrator: chains everything below into one command
scraper/     Python scripts:
               scrape.py         -- pulls RSS feeds, writes headline+summary rows
               fetch_fulltext.py -- backfills full article body text (separate,
                                     slower pass -- see "Full article text" below)
supabase/    schema.sql -- run once in the Supabase SQL editor
analysis/    CLI Claude uses on-demand to read unprocessed articles and write AI columns,
             plus correlate.py and check_outcomes.py (see "The improvement loop")
dashboard/   Next.js live dashboard (deploy free on Vercel)
```

## Running the cycle

Everything that's safe to fully automate (scrape, full-text backfill, ticker
correlation, call-outcome checking) runs in one command:
```bash
python run_cycle.py
```
It runs every 30 minutes via GitHub Actions (`.github/workflows/cycle.yml`),
or trigger it manually from the Actions tab. Each of the four steps runs as
its own subprocess, so one flaky step (e.g. a dead feed) doesn't take the
others down -- the script prints a per-step OK/FAILED summary at the end.
This does **not** include the AI analysis pass (`cli.py mark-processed` /
`write-digest`) -- that stays deliberately on-demand, see below.

## Full article text

RSS feeds only give a headline + short snippet -- not the actual article body,
which is what an AI analysis pass actually needs. `scraper/fetch_fulltext.py`
fetches each article's real URL and extracts clean article text with
`trafilatura`, storing it in `articles.full_text`. It runs as its own GitHub
Actions job (`fetch-fulltext.yml`) offset 15 minutes from the scrape job, and
only processes rows that haven't been attempted yet
(`full_text_fetch_attempted`), so it never wastes time re-hitting a URL twice.

Not every source is scrapable this way -- some (Investing.com, some
MarketWatch pages) block non-browser requests outright (Cloudflare/401), and
Google News RSS links are unusable redirect wrappers (dropped as a source for
that reason). Investing.com is permanently skipped via `BLOCKED_DOMAINS` in
`fetch_fulltext.py` -- it's a confirmed dead end (see "Known, accepted
limitations"), so skipping it outright saves a network round-trip per article
instead of failing after a timeout. In practice, expect roughly 60-90% of the
*attempted* (non-skipped) articles to get real full text, varying by batch;
the rest fall back to the RSS summary, which the analysis CLI (`text` field
in `fetch-unprocessed`/`fetch-recent`) already handles automatically -- it
prefers `full_text` and falls back to `summary_raw` transparently.

## 1. Supabase (database, free tier)

1. Create a project at https://supabase.com (free tier: 500MB DB, plenty for this).
2. Open the SQL editor, paste the contents of `supabase/schema.sql`, run it.
3. Get your keys from Project Settings -> API:
   - `Project URL` -> `SUPABASE_URL`
   - `service_role` key -> `SUPABASE_SERVICE_KEY` (server-side only, never expose in dashboard)
   - `anon public` key -> `NEXT_PUBLIC_SUPABASE_ANON_KEY` (safe for the dashboard, read-only via RLS)
4. In Database -> Replication, enable realtime on the `articles` and `digests` tables
   (needed for the dashboard's live updates).

## 2. Scraper + cycle (GitHub Actions, free)

1. Push this repo to GitHub.
2. In repo Settings -> Secrets and variables -> Actions, add:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_KEY`
   - `NEWSAPI_KEY` (optional -- free tier at https://newsapi.org, 100 req/day; skip if you don't want it)
3. `.github/workflows/cycle.yml` runs `run_cycle.py` every 30 minutes
   automatically, or trigger it manually from the Actions tab ("Run workflow").
4. To test locally first:
   ```bash
   cd scraper && pip install -r requirements.txt && cd ../analysis && pip install -r requirements.txt && cd ..
   # create scraper/.env and analysis/.env with SUPABASE_URL and SUPABASE_SERVICE_KEY
   python run_cycle.py
   ```

## 3. On-demand analysis (you + Claude)

No second AI API key needed -- when you want a read, tell Claude (in a session with
access to this repo and the Supabase env vars) something like:

> "Pull unprocessed articles from market-intel and give me today's read."

Claude then runs, e.g.:
```bash
cd analysis
pip install -r requirements.txt
python cli.py fetch-unprocessed --limit 50
```
reads the results in-conversation, reasons about them, and writes conclusions back:
```bash
python cli.py mark-processed <article_id> \
  --sentiment bullish --relevance 75 --tickers NVDA,semiconductors \
  --summary "..." --action "watch" --risk none --confidence 70

python cli.py write-digest --hours 24 \
  --summary "Overall market read for the day..." \
  --themes "Fed rate path,AI capex,energy" \
  --guidance "Suggested checks/precautions..." \
  --watchlist '{"NVDA": "earnings beat, no action needed", "MSFT": "..."}'
```
The digest and per-article AI columns immediately show up on the live dashboard.

`cli.py log-call --ticker NVDA --call bullish --rationale "..."` optionally logs a
directional call for tracking hit-rate over time (same idea as the MiroFish Cloud
Brain daily brief already does).

## 5. The improvement loop

Three scripts close the loop from "the AI said something" to "was it actually
right":

- **`analysis/correlate.py`** -- purely mechanical (no AI call): links articles
  that mention the same ticker within a rolling time window
  (`--window-hours`, default 72). A ticker with 2+ articles clustered together
  is usually a real signal, not noise. Part of `run_cycle.py`.
- **`analysis/check_outcomes.py`** -- the accountability piece. Revisits every
  logged call in `ai_calls_log` that's at least `--min-age-days` old (default
  3, so the market has time to actually move), pulls real price data via
  `yfinance` for the mapped symbol (see `TICKER_MAP` in the script -- add an
  entry there the first time you log a call about a new ticker/theme), and
  marks the call `correct` / `incorrect` / `unclear` (moves smaller than
  `--move-threshold`%, default 0.5%, don't count either way). Also prints a
  running hit-rate. Part of `run_cycle.py`, and the result shows up as a
  "Track record" panel on the dashboard.
- **Analysis quality itself** stays a manual, on-demand thing by design (see
  above) -- no second paid API key. When doing a bulk pass over many
  articles at once, don't trust keyword-based auto-triage for anything you'd
  actually act on: hand-verify the high-relevance subset (relevance >= 60)
  by actually reading it, the way any real analyst pass should.

### Known, accepted limitations

- **Investing.com is unscrapable** -- its Cloudflare protection blocks even a
  browser-header `requests` call and `cloudscraper`; beating it needs a full
  headless browser (Playwright), which isn't worth the complexity for a
  free-tier project. Its articles fall back to RSS summaries only.
- **Full automation of the analysis step needs a paid API key.** Scraping,
  full-text backfill, correlation, and outcome-checking are all fully
  automated (GitHub Actions, free). The actual "read this and judge it" step
  stays on-demand through Claude, by deliberate choice, to avoid recurring
  API cost -- so the dashboard's AI columns only update when you ask for a
  read, not continuously.

## 4. Dashboard (Vercel, free)

1. `cd dashboard && npm install`
2. Copy `.env.local.example` to `.env.local`, fill in your Supabase URL + anon key.
3. `npm run dev` to preview locally at http://localhost:3000
4. Deploy: push to GitHub, import the repo (root: `dashboard/`) on https://vercel.com,
   set the same two env vars in Vercel's project settings, deploy.

The dashboard shows a live ticker bar at the top (NVDA, MSFT, GOOGL, ASML,
WTI/Brent crude, 10Y Treasury yield, gold, VWCE -- edit `SYMBOLS` in
`app/api/quotes/route.ts` to change the list), refreshing every 60s from
Yahoo Finance's public chart endpoint via a server-side API route
(`app/api/quotes/route.ts` -> `components/TickerBar.tsx`). No API key needed,
same free data source `check_outcomes.py` uses (`yfinance`) -- the dashboard
just calls Yahoo directly server-side instead of going through the Python
lib, since a browser-side call would hit Yahoo's CORS restrictions.

Below that: the latest digest, a filterable live article feed (all / bullish
/ bearish / high relevance / unprocessed), and a "Track record" panel for
logged calls. It all updates automatically via Supabase realtime whenever the
scraper or an analysis pass
writes new rows -- no manual refresh needed.

## Sources

**News (RSS, `scraper/sources.py`)**: Business Insider, CNBC Markets + Top
News, MarketWatch (Top Stories + Market Pulse), Yahoo Finance, Seeking Alpha,
Investing.com (headlines/summary only, full text blocked), Fed press
releases, Financial Times Markets, The Guardian Business, OilPrice.com
(energy). Optional NewsAPI free tier adds broader keyword-based coverage.
(Reuters and ECB's RSS feeds were tried and confirmed dead -- replaced;
Google News RSS was tried and dropped; Business Insider's "Markets" RSS
feed was tried and rejected -- dominated by law-firm class-action-lawsuit
spam and wire-service press-release noise, worse signal than the general
feed. See "Known, accepted limitations".)

**Official filings (`scraper/sec_filings.py`)**: SEC EDGAR 8-K/6-K filings
for the 4 watchlist companies (NVDA, MSFT, GOOGL file 8-K; ASML, as a
foreign private issuer, files 6-K), pulled directly from EDGAR's atom feed
with a proper identifying User-Agent (SEC blocks anonymous requests). This
is the highest-quality source in the pipeline -- official, zero-noise,
directly tied to the actual portfolio, not filtered through a news
aggregator's editorial judgment. Item codes (e.g. "5.02") are decoded to
plain English ("Officer/Director Departure or Appointment") via
`ITEM_DESCRIPTIONS` in that file. Classified by rule (item type ->
relevance/risk) rather than read individually -- see `analysis/` workflow
notes -- since the filing category itself is a reliable, structured signal.

**Spam filtering**: `scrape.py`'s `SPAM_PATTERNS` regex drops promotional
junk ("last chance", "% off", "subscribe now" etc.) before it ever reaches
the DB -- seen leaking in from Investing.com specifically. The ticker-regex
`STOPWORDS` list is also tuned against real false positives observed in
production (ad copy fragments like "GO", "HOURS", "LAST" getting matched as
tickers).

## Live deployment (free tier)

The dashboard is self-sufficient -- it talks to GitHub Actions and Yahoo
Finance directly via its own `app/api/*` routes, no separate server needed:

1. Push this repo to GitHub, add the Actions secrets under Settings -> Secrets
   and variables -> Actions: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`,
   `NEWSAPI_KEY` (see "2. Scraper + cycle" above). `.github/workflows/cycle.yml`
   is the sole live scheduler -- there's no always-on server.
2. Deploy `dashboard/` to Vercel (root directory: `dashboard`). Set build-time
   env vars `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`, plus
   runtime env vars `SUPABASE_SERVICE_ROLE_KEY`, `GITHUB_TOKEN`,
   `GITHUB_OWNER`, `GITHUB_REPO` (a token with `repo` scope, used server-side
   by `app/api/pipeline/*` to dispatch/poll the `cycle.yml` workflow for the
   Mission Control panel -- see `dashboard/.env.local.example`).

## Mobile app (PWA) + notifications

The dashboard is an installable Progressive Web App -- open it on a phone and
use the browser's "Add to Home Screen" / "Install" option. It gets its own
icon, launches full-screen, and can receive push notifications
(`dashboard/public/manifest.json`, `dashboard/public/sw.js`).

Configure notifications from the dashboard's **Settings** page:
- **Push**: click "Enable Push" on each device you want notified (per-device,
  like any push subscription). Requires `NEXT_PUBLIC_VAPID_PUBLIC_KEY` (Vercel)
  and `VAPID_PRIVATE_KEY` / `VAPID_SUBJECT` (GitHub Actions secrets) -- generate
  a key pair once with `npx web-push generate-vapid-keys --json`.
- **Email**: toggle on and set an address. Sent via Gmail SMTP with an [app
  password](https://myaccount.google.com/apppasswords) (requires 2FA on the
  Gmail account) -- set `GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` as GitHub
  Actions secrets. This is a regular SMTP app password, not the interactive
  "send as Claude" connector: unattended automation needs a credential the
  pipeline can use on its own, without a live session.
- **Threshold**: only notify when `ai_relevance_score >= threshold` on a
  portfolio ticker (NVDA/MSFT/GOOGL/ASML).

`analysis/notify.py` runs as the last step of every cycle
(`run_cycle.py`/`cycle.yml`), checking for newly-relevant articles and
not-yet-notified digests via a `notified_at` column -- each one fires once,
never re-sent on the next cycle. Both channels silently no-op if their env
vars aren't set, so notifications stay off until you configure them.

Run `supabase/migrations/002_notifications_and_settings.sql` in the Supabase
SQL editor once (same as `schema.sql` originally) before using Settings --
it adds `app_settings`, `push_subscriptions`, `source_overrides`, and the
`notified_at` tracking columns.

## Next steps once this is proven

- Increase scrape frequency / add more sources once free-tier limits are understood
- Add a scheduled (not just on-demand) daily digest if you want it pushed automatically
- Feed high-relevance flags into the existing MiroFish Cloud Brain daily brief
- Once `check_outcomes.py` has enough decided calls to be statistically meaningful
  (dozens, not a handful), use the hit-rate to actually recalibrate how much weight
  to put on future AI guidance -- that's the whole point of tracking it
