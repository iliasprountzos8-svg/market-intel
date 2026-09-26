-- 005: homelab edition. Paste the whole thing into the Supabase SQL editor and run it once.
-- Safe to re-run. Nothing is deleted or changed in existing data.

-- 1. Track-record fields on calls (same as 004)
alter table ai_calls_log add column if not exists confidence int;
alter table ai_calls_log add column if not exists horizon_days int;
alter table ai_calls_log add column if not exists symbol text;
alter table ai_calls_log add column if not exists invalidation text;
alter table ai_calls_log add column if not exists entry_price numeric;
alter table ai_calls_log add column if not exists entry_at timestamptz;
alter table ai_calls_log add column if not exists exit_price numeric;
alter table ai_calls_log add column if not exists asset_return_pct numeric;
alter table ai_calls_log add column if not exists benchmark_return_pct numeric;
alter table ai_calls_log add column if not exists excess_return_pct numeric;
alter table ai_calls_log add column if not exists resolved_at timestamptz;

-- 2. New AI cells on articles (filled by the homelab)
alter table articles add column if not exists fb_score real;   -- FinBERT: -1 (negative) .. +1 (positive)
alter table articles add column if not exists event text;      -- earnings, guidance, m&a, filing, analyst, legal_reg, macro, supply, product, other
alter table articles add column if not exists lang text;       -- en / el

-- 3. Commands queue: the site drops 'sync' / 'digest' requests, the homelab picks them up
create table if not exists commands (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('sync', 'digest')),
  status text not null default 'pending',   -- pending, running, done, failed, rejected, expired
  requested_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  result text,
  requested_by text default 'site'
);
create index if not exists idx_commands_status on commands (status, requested_at);

-- 4. Homelab heartbeat and live status (one row)
create table if not exists pipeline_status (
  id int primary key default 1,
  updated_at timestamptz not null default now(),
  data jsonb not null default '{}'::jsonb
);

-- 5. S&P 500 sentiment signals (one row per ticker, refreshed by the homelab)
create table if not exists ticker_signals (
  symbol text primary key,
  name text,
  sector text,
  updated_at timestamptz,
  ens real, ens_z real, finbert real, lex real, old real,
  stories_24h int, attention_z real,
  series jsonb, perf jsonb
);

-- 6. Prediction lab report (one row per day)
create table if not exists lab_report (
  as_of date primary key,
  updated_at timestamptz not null default now(),
  report jsonb not null
);

-- 7. Row level security: everyone can read (like your other tables); only the homelab / server routes write.
alter table commands enable row level security;
alter table pipeline_status enable row level security;
alter table ticker_signals enable row level security;
alter table lab_report enable row level security;

drop policy if exists "public read commands" on commands;
create policy "public read commands" on commands for select using (true);
drop policy if exists "site can request sync or digest" on commands;
create policy "site can request sync or digest" on commands for insert
  with check (status = 'pending' and kind in ('sync', 'digest') and result is null and started_at is null and finished_at is null);
drop policy if exists "public read pipeline_status" on pipeline_status;
create policy "public read pipeline_status" on pipeline_status for select using (true);
drop policy if exists "public read ticker_signals" on ticker_signals;
create policy "public read ticker_signals" on ticker_signals for select using (true);
drop policy if exists "public read lab_report" on lab_report;
create policy "public read lab_report" on lab_report for select using (true);

-- 8. Live updates for the new tables (ignore "already member" errors)
do $$ begin alter publication supabase_realtime add table pipeline_status; exception when others then null; end $$;
do $$ begin alter publication supabase_realtime add table ticker_signals; exception when others then null; end $$;
do $$ begin alter publication supabase_realtime add table lab_report; exception when others then null; end $$;
do $$ begin alter publication supabase_realtime add table commands; exception when others then null; end $$;
