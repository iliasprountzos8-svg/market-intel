# Market Intel: homelab edition (changelog, 2026-09-26)

Market Intel moved from "GitHub Actions + Supabase free tier" to a self-hosted pipeline on an
always-on homelab (Debian 13, Ryzen 3 2200U, 8 GB), and gained a scoring engine and a prediction lab.
GitHub Actions and the Vercel dashboard keep working; the homelab is now the primary system.

> Research tooling only. Nothing here is investment advice. See "Honest findings" below.

## Before / after

| | Before | After |
|---|---|---|
| Where it runs | GitHub Actions cron (every 30 min) | Homelab systemd timer (every 30 min), Actions now redundant |
| Database | Supabase free tier (500 MB cap) | Local Postgres 16 + PostgREST (same REST API, scripts unchanged); Supabase = dashboard mirror |
| Sources | 12 RSS feeds + 4 SEC watchlist companies | 1,636 feeds: 503 S&P 500 companies x 3 providers, SEC current filings, 114 validated outlets / central banks / regulators |
| Articles | ~4.5k | ~27k (steady state ~1.5-2.5k new per day) |
| Scoring | VADER rule-based (64% "bullish": biased) | + finance lexicon, + local FinBERT on CPU, per-source standardisation, duplicate clustering, per-ticker sentiment index (S&P 500) |
| Track record | 12 calls, 62% hit "rate" (mixed windows, duplicates) | Fair re-score: fixed horizon, benchmark, confidence interval, baseline; calls must be falsifiable |
| Predictions | none | Walk-forward-validated strategies + forward paper-trading ledger (8 books) |
| Digest | on demand with Claude on the laptop | headless Claude on the homelab, price context, disciplined calls, no account connectors |
| Alerts | Web push / email (disabled) | ntfy phone alerts: signal spikes, digests, failures (quality-gated) |
| Control | dashboard only | + private phone control page (pull, digest, lab, status) |

## The site: fed by the homelab (the Vercel dashboard you already use)
The homelab is the primary system; the site is its window, on every device. The site can't reach the homelab, so the homelab talks OUTBOUND to Supabase:

```
homelab pulls 24/7 -> local Postgres -> local AI fills the cells -> sync daemon -> Supabase -> site (realtime) on any device
                                                     ^                                            |
                                                     +---- commands queue (Sync / Digest) <-------+ buttons on the site
```
* `analysis/populate_cells.py`: AI fills the cells the site reads (`ai_sentiment`, `ai_relevance_score`, `ai_affected_tickers`, `ai_confidence`) plus new `fb_score`, `event`, `lang`,
  from FinBERT + lexicon + entity matching + event tags + source weights + duplicate clustering. Claude-written rows keep Claude's analysis. Effect: "high relevance" (60+) fell from ~936 to ~55 articles in 3 days, and the top items are the ones that matter.
* `sync_daemon.py` (`market-intel-sync.service`, 24/7): every 30 s pushes worthy articles (relevance >= 40, naming a holding, or Claude-analysed), digests and calls; every 15 s a heartbeat/status row (`pipeline_status`);
  every ~2 min S&P 500 sentiment (`ticker_signals`) and the lab report (`lab_report`); every 5 min pulls dashboard-owned settings; every 5 s polls `commands`.
* Buttons on the site: `Sync Now` -> `commands(kind='sync')` -> `run_sync.sh` (poll key sources, classify, FinBERT, fill cells, ~90 s). `Request Digest` -> `commands(kind='digest')` -> `run_digest.sh` (headless Claude writes a real digest + falsifiable calls). Rate-limited (digest max 8/day), allow-listed kinds only.
  If the homelab is silent for 3+ minutes, Sync falls back to the old GitHub Actions dispatch.
* New site pages: **Signals** (S&P 500 sentiment leaderboard), **Lab** (strategies, paper ledger, walk-forward tests), **System** (heartbeat, sources, throughput, commands); FinBERT/event badges on articles; sentiment chips on the Watchlist; Mission Control shows the homelab live.
* Supabase side: `supabase/migrations/005_homelab.sql` (new columns + `commands`, `pipeline_status`, `ticker_signals`, `lab_report`, RLS, realtime). Until it is applied the daemon logs what is missing and keeps pushing what it can.
* Tested end to end on a private copy first: Sync (+68 new articles, ~4 min, now trimmed to ~90 s) and Digest (real digest written and pushed) both work from the site's buttons.

## Market Intel HQ: the live phone app (added 2026-09-26)
`homelab/hq/` (`hq_app.py` + `index.html`, no build step, no external libraries) is served from the homelab on the Tailscale IP at the
same secret link as the old control page, and reads the LOCAL database directly through a read-only Postgres role (`hq_ro`).

| Tab | What you see |
|---|---|
| Home | live market strip, latest digest, news-sentiment signals for your holdings and the biggest S&P 500 moves, signal alerts, headlines that name your holdings, lab picks, controls (pull data, generate digest) |
| News | searchable feed of all ~27k articles with filters (holdings, positive, negative, Claude-analysed, SEC filings), FinBERT scores, duplicate stories collapsed |
| Tickers | any S&P 500 company: price, 14-day sentiment index per model, current signals, lab ranks and paper positions, strict news |
| Lab | each strategy's top picks / avoid list, paper-ledger results, walk-forward tables, signal test verdict, research-only banner |
| System | pipeline status, articles per day, ingest runs, source health, DB size, track record of calls |

Live-ness: the header refreshes every 20 s; a **fast lane** (`run_fast.sh`, every 5 min) pulls the feeds that are due, classifies and FinBERT-scores new
articles, and top sources poll faster (outlets 10 min, SEC 8-K 5 min, Yahoo tier A 15 min). The full 30-minute cycle still does full text, the sentiment
index, outcome checks, alerts and the Supabase mirror. Quotes come from Yahoo (cached 90 s) with the nightly file as fallback.

## Pipeline (every 30 minutes, `homelab/scripts/run_pull.sh`)
`scrape (legacy) -> ingest -> fetch_fulltext -> classify -> finbert -> signals -> correlate -> check_outcomes -> notify -> mirror`
Steps that only make sense on the homelab are gated by `MI_ENABLE_SIGNALS=1`, so the GitHub Actions run is unchanged.

## What is where
| Path | Purpose |
|---|---|
| `ingest/build_sources.py`, `ingest/ingest.py` | source registry (1,636 feeds, validated) and the parallel, polite, conditional-GET ingester with per-host breakers |
| `nlp/score_finbert.py` | local FinBERT scoring into `article_scores` (time-boxed, drains backlog) |
| `analysis/signals.py`, `analysis/signal_eval.py` | multi-model sentiment index (lex / finbert / ens / old) and the honest "does it predict anything" test |
| `analysis/scoring.py`, `analysis/track_record.py`, `analysis/check_outcomes.py` | fair call scoring (fixed horizon, entry before the news could be acted on, benchmark, Wilson CI) |
| `analysis/cli.py`, `analysis/notify.py` | new `market-snapshot`, `signals`, `lab` commands, structured `log-call`; ntfy channel with quality gate |
| `lab/` | prices + features (`lab_data.py`), weekly walk-forward (`lab_wf.py`), daily ranked ideas + paper ledger (`lab_daily.py`) |
| `mirror_to_supabase.py` | keeps the Vercel dashboard fed; pulls dashboard-owned settings back |
| `homelab/` | infrastructure as code: Postgres/PostgREST compose + schema, systemd units, wrapper scripts, phone control page |
| `supabase/migrations/004_track_record.sql` | call fields (only needed on the real Supabase for the mirror to carry them) |
| `data/sp500.json` | S&P 500 constituents (symbol, name, sector, CIK) used by ingest, signals and the lab |

## Honest findings so far
* The old "62% hit rate" was 8 calls with inconsistent windows. Fair re-score: 4 decided calls, 75%, 95% CI 30-95%: anecdote.
* The old sentiment labels were 64% bullish; per-source standardisation removes that bias.
* News-signal test (481 tickers, ~2,000 observations, 4 models x 3 horizons x raw/vs S&P): 3 of 48 tests p<0.05, ~2 expected by chance: no edge beyond chance yet. FinBERT leans positive, weakly (rho ~ +0.06): a hypothesis for the forward ledger.
* Walk-forward (5-day, 503 stocks, 867 test days): momentum IC +0.033 (t 2.1), +0.14% net per period; reversal and low-vol nothing; ML about zero net.
  CAVEAT: the universe is TODAY's S&P 500 members (survivorship bias flatters momentum-type strategies).
* The forward paper ledger (`lab_positions`) is the real out-of-sample test. First settlements ~2026-10-01.

## Operating it
* Primary DB: `homelab/marketdb/` (bound to 127.0.0.1). Secrets (`POSTGRES_PASSWORD`, `JWT_SECRET`, service/anon JWTs) live only in the server's `.env` files.
* Rollback to Supabase-only: point `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` back at Supabase (the server keeps `.env.pre-local`).
* Rebuild guide for the whole server: `~/homelab-config/REBUILD.md` on the homelab.
* Disable the GitHub Actions workflows once the homelab is trusted; they still write to Supabase.

## Lessons from the first night
* Five cycles timed out because the FinBERT slot (15 min) exceeded the service time limit (20 min): limit is now 50 min, health report counts failures.
* Unattended `claude -p` loads every account connector unless `--strict-mcp-config` is passed (134k tokens, and a needless exposure): always pass it.
* Per-company feeds tag tangential stories with a ticker: phone alerts now require the headline to name the holding.
* Holiday and half-loaded days must be dropped from price panels or rolling features blank out for weeks.

## Reliability and security layers (2026-09-26 evening)
Files: `homelab/bin/`, `homelab/systemd/` (+ `dropins/`), `homelab/system-tuning/`, `lab/ledger_report.py`.

* **Watchdog** (`homelab-watchdog`, every 10 min): hung/stale cycle, fast lane, data flow, DB, services, containers, primary + USB backup age, disk, RAM, temperature, pending reboot, Tailscale key expiry, daily package integrity (debsums). ntfy alerts with cooldown and "resolved" notices; daily SSD wear CSV; per-step cycle timings (`homelab-watchdog.sh report`).
* **USB second backup** (`homelab-backup-usb`, 05:15): `restic copy` to a stick mounted only during the job; Sundays prune + verify 5% + test restore. The stick also holds unrelated files, so data lives under `/homelab-backup/`.
* **Resource fences**: batch jobs run at CPUWeight=30 with idle IO; cycle and fast lane MemoryHigh 2G / MemoryMax 3.5G. zram swap. Hardening drop-ins for SSH and kernel sysctl.
* **Full-text fetch fix** (`scraper/fetch_fulltext.py`, `run_cycle.py`): the step failed the whole cycle when slow or blocking hosts exhausted the 240 s limit. Now: no retries on 4xx, per-host circuit breaker (2 failures), 180 s budget, and an outer exit 124 is a designed time-box, not a failure. First cycle after the change: 126 s (was 240 s timeout), 69/100 articles fetched.
* **Ledger read** (`lab/ledger_report.py`, Sundays 10:30): per book/side, one observation per signal date, bootstrap 95% CI, hit rate, explicit multiple-testing caveat. Nothing has settled yet (first settlements about 2026-10-01).
* **Lesson**: never edit a running bash script in place (bash reads by byte offset); write a temp file and `mv`. `systemctl is-active` is non-zero while a unit is "activating".
* **signals 252 s -> 16 s**: profiling showed 91% of the step in `zscore()`, which ran ~1,800 SQLite lookups filtered by symbol and model against a table keyed by timestamp first, so each one scanned the whole 564k-row table. Added index `snapshots2(symbol, model, ts)` (created in `db()`); 400 old-plan vs new-plan queries returned identical data, the query itself got 135x faster, and the full cycle drops from about 7.7 to about 3.5 minutes.
* **Reboot test found a real bug**: after a reboot only 6 of 11 containers came back. The five that publish ports on the Tailscale address (ntfy, Nextcloud, Uptime Kuma, Homepage, Syncthing) failed with "cannot assign requested address" because Docker started before `tailscale0` had its IP, so after any power cut alerts (ntfy) would have stayed down. Fix: `homelab/systemd/dropins/docker.service.d/10-wait-tailscale.conf` (Docker starts after tailscaled and waits up to 90 s for the address). Re-tested with a second reboot: 11/11 containers up, no bind failures, SSH back in about 65 s, boot 18 s.

## News reading v2 (2026-09-27)
Goal: read the news better, not just score headlines. All of it runs on the homelab; the reader is the only part that uses Claude.

* **Stories, not articles** (`analysis/story_logic.py`, `stories.py`, table `stories`): near-duplicate articles are clustered with rarity-weighted title similarity, an entity veto (headlines naming different companies never merge) and a ticker gate. A purity sample of 18 merged stories found 17 clean. First version merged templated headlines from different companies (RTX/McKesson/Starbucks "is attracting investor attention"): caught by inspection, fixed, regression-tested.
* **Freshness gate**: an item first seen more than 24 h after its publish time is flagged `is_backfill` and crushed in priority. 75% of the database is one-off backlog import.
* **Priority** (0-100) decides what gets scarce resources (full-text fetch order, LLM reading): holdings, event weight, primary sources, independent confirmation (per-ticker distributor feeds re-serving one article count once), opinion/clickbait discounted, market-wide roundups discounted.
* **Primary sources** (`ingest/sec8k.py`): SEC EDGAR submissions API for all 503 S&P 500 companies + ASML every cycle (about 60 s, polite, 0 errors): 8-K items decoded (2.02 earnings, 5.02 executive change, 3.01 delisting, 4.02 restatement ...).
* **Event tags v2** (`analysis/event_rules.py`, title only, word-start guarded) as the cheap first pass.
* **LLM reader** (`analysis/reader.py`, timer every 2 h, max 60 stories/day, kill switch `MI_READER=0`): batches of up to 20 top stories to a small Claude model: event, company, direction, magnitude, relevance, one-line takeaway; results in `story_reads`. Real cost measured: about $0.003 per story at list price.
* **Briefing** (`analysis/briefing.py`, 07:40 daily to the phone): per holding, matched by keyword, reader-identified company or a feed tag on at most 2 tickers (feed tags alone were noisy), with primary-source filings and thesis watch-items (`analysis/theses.json`: DRAFT theses, edit them).
* **Metrics**: `analysis/dq_metrics.py` hourly into `dq_metrics`, shown on the HQ System tab together with the day's top stories.
* **Calibration scorecard** (`analysis/calibration.py`): Brier score and reliability bins for the digest's calls. Currently 0 scoreable calls: every historical call lacks a confidence value.

### Measured (gold set: 132 + 71 items labeled by an LLM, so not ground truth)
| | Accuracy | Precision when it tags | Recall of real events |
|---|---|---|---|
| Old classifier (blind set) | 20% | 31% | 16% |
| Rules v2 (blind set) | 45% | 71% | 28% |
| LLM reader (blind set) | 65% | 63% | 75% |
The rules scored 86% on the set they were tuned on and 45% on a fresh blind set: overfitting, disclosed. FinBERT direction was right on 83% (set 1) and 100% (set 2, n=19) of directional items. Reader company match 35/38, direction sign-correct 21/21 when it took a side. The reader's agreement with LLM labels is likely inflated versus human labels.

### Reboot failure found later the same night (correction)
The earlier note above says a second reboot verified "11/11 containers up". That check looked at container STATUS only. After that reboot five containers were "Up" with no network attachment and no published ports (ntfy, Nextcloud, Uptime Kuma, Homepage, Syncthing unreachable; `docker restart` did not help, recreating did). Fix: `homelab/bin/homelab-docker-heal.sh` recreates any container whose configured ports or network are missing, at boot (after docker + tailscaled) and every 10 minutes from the watchdog; Uptime Kuma is now a compose project so it can be healed; the watchdog now logs undeliverable alerts (`unsent.log`) instead of dropping them and alerts when ntfy itself is down. The heal was proven by detaching ntfy's network on purpose. The root cause of the lost attachment at boot is NOT identified; a third reboot test with port checks is still owed.

### Cost policy (2026-09-27): the system costs nothing to run
Only electricity (a few euros a month at most) is spent. There is no API key anywhere on the server: Claude is reached through the claude.ai login, so nothing is billed per token, but anything that calls it draws on the subscription's usage allowance, so it is opt-in:
* LLM reader: **OFF** (timer disabled; `MI_READER=1` needed to run). Everything else in the news pipeline (stories, SEC 8-K, rules, FinBERT, briefing, metrics) is free and local.
* Daily health report: rule-based (`homelab/agents/health_summary.py`, tested); no Claude.
* Only remaining Claude use: the on-demand digest (Request Digest button / phone page, at most 8 a day) and the idle iPhone remote-control session, both user-triggered.
