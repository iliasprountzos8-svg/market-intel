-- generated from the live Supabase schema (OpenAPI) + local additions
create extension if not exists pgcrypto;

create table if not exists "ai_calls_log" (
  "id" uuid default gen_random_uuid() not null,
  "created_at" timestamp with time zone default now() not null,
  "digest_id" uuid,
  "ticker_or_theme" text not null,
  "call" text not null,
  "rationale" text,
  "outcome" text,
  "outcome_checked_at" timestamp with time zone,
  primary key ("id")
);

create table if not exists "app_settings" (
  "id" integer default 1 not null,
  "push_enabled" boolean default False not null,
  "email_enabled" boolean default False not null,
  "notify_email" text,
  "relevance_threshold" integer default 70 not null,
  "updated_at" timestamp with time zone default now() not null,
  primary key ("id")
);

create table if not exists "articles" (
  "id" uuid default gen_random_uuid() not null,
  "source" text not null,
  "url" text not null,
  "title" text not null,
  "summary_raw" text,
  "full_text" text,
  "author" text,
  "published_at" timestamp with time zone,
  "scraped_at" timestamp with time zone default now() not null,
  "category" text,
  "tickers_raw" text[],
  "ai_processed" boolean default False not null,
  "ai_processed_at" timestamp with time zone,
  "ai_sentiment" text,
  "ai_relevance_score" integer,
  "ai_affected_tickers" text[],
  "ai_summary" text,
  "ai_suggested_action" text,
  "ai_risk_flag" text,
  "ai_confidence" integer,
  "ai_correlated_article_ids" uuid[],
  "full_text_fetch_attempted" boolean default False not null,
  "full_text_fetched_at" timestamp with time zone,
  "notified_at" timestamp with time zone,
  primary key ("id")
);

create table if not exists "digests" (
  "id" uuid default gen_random_uuid() not null,
  "created_at" timestamp with time zone default now() not null,
  "period_start" timestamp with time zone not null,
  "period_end" timestamp with time zone not null,
  "articles_covered" integer not null,
  "summary" text not null,
  "key_themes" text[],
  "guidance" text,
  "watchlist_notes" jsonb,
  "notified_at" timestamp with time zone,
  primary key ("id")
);

create table if not exists "holdings" (
  "symbol" text not null,
  "name" text not null,
  "shares" numeric not null,
  "keywords" text[] not null,
  "display_order" integer default 0 not null,
  primary key ("symbol")
);

create table if not exists "portfolio_meta" (
  "id" integer default 1 not null,
  "cost_basis_eur" numeric not null,
  "as_of" date not null,
  primary key ("id")
);

create table if not exists "push_subscriptions" (
  "id" uuid default gen_random_uuid() not null,
  "endpoint" text not null,
  "p256dh" text not null,
  "auth" text not null,
  "created_at" timestamp with time zone default now() not null,
  primary key ("id")
);

create table if not exists "source_overrides" (
  "name" text not null,
  "enabled" boolean not null,
  "updated_at" timestamp with time zone default now() not null,
  primary key ("name")
);

-- constraints/indexes that PostgREST upserts and the pipeline rely on
create unique index if not exists articles_url_key on articles (url);
create unique index if not exists push_subscriptions_endpoint_key on push_subscriptions (endpoint);
create index if not exists idx_articles_published_at on articles (published_at desc);
create index if not exists idx_articles_ai_processed on articles (ai_processed);
create index if not exists idx_articles_source on articles (source);
create index if not exists idx_articles_notify_pending on articles (notified_at) where notified_at is null;
create index if not exists idx_articles_relevance on articles (ai_relevance_score desc) where ai_relevance_score is not null;
create index if not exists idx_calls_created on ai_calls_log (created_at desc);
create index if not exists idx_digests_created on digests (created_at desc);

create table if not exists source_health (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  source text not null,
  entries_fetched int not null default 0,
  errors int not null default 0,
  skipped_domains int not null default 0,
  full_text_fetched int not null default 0,
  full_text_failed int not null default 0
);
create index if not exists idx_source_health_created on source_health (created_at desc);

-- feed registry for the large-scale ingester
create table if not exists sources (
  id serial primary key,
  name text not null,
  url text not null unique,
  kind text not null default 'rss',          -- rss | sec | yahoo_ticker | central_bank | macro
  category text not null default 'equities',
  ticker text,
  weight real not null default 0.8,
  enabled boolean not null default true,
  etag text,
  last_modified text,
  last_status int,
  last_fetch timestamptz,
  last_success timestamptz,
  fail_count int not null default 0,
  items_total int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists idx_sources_enabled on sources (enabled, last_fetch);

-- per-article scores from each local model, so we can measure which one predicts best
create table if not exists article_scores (
  article_id uuid not null,
  model text not null,
  score real not null,
  p_pos real,
  p_neg real,
  scored_at timestamptz not null default now(),
  primary key (article_id, model)
);
create index if not exists idx_article_scores_model on article_scores (model, scored_at desc);
