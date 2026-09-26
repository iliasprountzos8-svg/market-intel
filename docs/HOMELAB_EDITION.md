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
