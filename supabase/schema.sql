-- Market Intelligence DB schema (Supabase / Postgres)
-- Run this in the Supabase SQL editor once, on a fresh project.

create extension if not exists "uuid-ossp";

-- Raw scraped articles
create table if not exists articles (
  id uuid primary key default uuid_generate_v4(),
  source text not null,
  url text not null unique,
  title text not null,
  summary_raw text,          -- feed-provided summary/snippet, if any
  full_text text,            -- scraped full article body, if fetched
  author text,
  published_at timestamptz,
  scraped_at timestamptz not null default now(),
  category text,             -- e.g. 'macro', 'equities', 'crypto', 'energy'
  tickers_raw text[],        -- naive regex-extracted tickers, pre-AI

  -- AI-filled columns (populated on-demand by the analysis pass)
  ai_processed boolean not null default false,
  ai_processed_at timestamptz,
  ai_sentiment text,             -- 'bullish' | 'bearish' | 'neutral' | 'mixed'
  ai_relevance_score int,        -- 0-100, how market-moving/important
  ai_affected_tickers text[],    -- AI-confirmed tickers/sectors
  ai_summary text,               -- 1-3 sentence AI summary
  ai_suggested_action text,      -- e.g. "watch", "no action", "review position in X"
  ai_risk_flag text,             -- e.g. "none", "watch", "elevated"
  ai_confidence int,             -- 0-100
  ai_correlated_article_ids uuid[],  -- links to related articles this pass found

  -- Full-text backfill tracking (scraper/fetch_fulltext.py)
  full_text_fetch_attempted boolean not null default false,
  full_text_fetched_at timestamptz
);

create index if not exists idx_articles_published_at on articles (published_at desc);
create index if not exists idx_articles_ai_processed on articles (ai_processed);
create index if not exists idx_articles_source on articles (source);

-- Daily / on-demand digest written by Claude after an analysis pass
create table if not exists digests (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  period_start timestamptz not null,
  period_end timestamptz not null,
  articles_covered int not null,
  summary text not null,           -- the narrative market summary
  key_themes text[],
  guidance text,                   -- suggestions/precautions/checks
  watchlist_notes jsonb            -- e.g. {"MSFT": "...", "NVDA": "..."}
);

-- Track AI call bullish/bearish calls for a real hit-rate over time
create table if not exists ai_calls_log (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  digest_id uuid references digests(id),
  ticker_or_theme text not null,
  call text not null,          -- 'bullish' | 'bearish' | 'neutral'
  rationale text,
  confidence int,              -- 0-100
  outcome text,                -- filled in later: 'correct' | 'incorrect' | 'unclear'
  outcome_checked_at timestamptz
);

-- Row Level Security: keep it simple for a single-user personal project.
-- Enable RLS and allow the service role full access; anon key gets read-only.
alter table articles enable row level security;
alter table digests enable row level security;
alter table ai_calls_log enable row level security;

create policy "public read articles" on articles for select using (true);
create policy "public read digests" on digests for select using (true);
create policy "public read ai_calls_log" on ai_calls_log for select using (true);

-- Writes happen via the service_role key from the scraper/analysis scripts,
-- which bypasses RLS by default, so no insert/update policy is needed for anon.

-- Track per-source scraping health and hit rates
create table if not exists source_health (
  id uuid primary key default uuid_generate_v4(),
  created_at timestamptz not null default now(),
  source text not null,
  entries_fetched int not null default 0,
  errors int not null default 0,
  skipped_domains int not null default 0,
  full_text_fetched int not null default 0,
  full_text_failed int not null default 0
);

alter table source_health enable row level security;
create policy "public read source_health" on source_health for select using (true);
