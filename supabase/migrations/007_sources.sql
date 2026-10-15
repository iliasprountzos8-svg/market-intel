-- Source registry for the cloud ingest (ingest/ingest.py). Mirrors homelab/marketdb/init/02-schema.sql.
-- RLS on, no policies: only the service key (GitHub Actions) can read/write it.
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
  poll_minutes int not null default 30,
  created_at timestamptz not null default now()
);
create index if not exists idx_sources_enabled on sources (enabled, last_fetch);
alter table sources enable row level security;
