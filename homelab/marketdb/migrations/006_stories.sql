-- 006: read the news better. Stories (deduplicated events), freshness gate, priority, event tags v2,
-- Claude reader output, data-quality metrics. Idempotent; run with psql against the local marketintel DB.

alter table articles add column if not exists story_id bigint;
alter table articles add column if not exists title_hash text;
alter table articles add column if not exists is_backfill boolean not null default false;
alter table articles add column if not exists priority real;
alter table articles add column if not exists event2 text;

create table if not exists stories (
  story_id bigserial primary key,
  title text not null,
  title_hash text,
  first_article_id uuid,
  first_source text,
  first_published timestamptz,
  first_seen timestamptz,
  last_published timestamptz,
  last_seen timestamptz,
  n_articles int not null default 1,
  n_sources int not null default 1,
  tickers text[] not null default '{}',
  sources text[] not null default '{}',
  event text,
  is_backfill boolean not null default false,
  priority real not null default 0,
  updated_at timestamptz not null default now()
);

create index if not exists idx_articles_story on articles (story_id);
create index if not exists idx_articles_unstoried on articles (scraped_at) where story_id is null;
create index if not exists idx_articles_prio_fulltext on articles (priority desc nulls last, published_at desc) where full_text_fetch_attempted = false;
create index if not exists idx_stories_first_seen on stories (first_seen desc);
create index if not exists idx_stories_priority on stories (priority desc) where not is_backfill;
create index if not exists idx_stories_tickers on stories using gin (tickers);
create index if not exists idx_stories_hash on stories (title_hash);

-- Structured reading of the top stories by an LLM (see analysis/reader.py). One row per story and model.
create table if not exists story_reads (
  story_id bigint not null,
  model text not null,
  read_at timestamptz not null default now(),
  result jsonb not null,
  input_tokens int,
  output_tokens int,
  cost_usd real,
  primary key (story_id, model)
);

-- Daily data-quality numbers so improvements can be measured, not claimed (see analysis/dq_metrics.py).
create table if not exists dq_metrics (
  ts timestamptz not null default now(),
  name text not null,
  value double precision,
  primary key (ts, name)
);

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'hq_ro') then
    grant select on stories, story_reads, dq_metrics to hq_ro;
  end if;
end $$;

notify pgrst, 'reload schema';
