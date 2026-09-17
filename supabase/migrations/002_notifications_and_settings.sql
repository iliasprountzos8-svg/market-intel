-- Adds: configurable app settings, web-push subscriptions, and the
-- "already notified" tracking needed so the automated pipeline doesn't
-- re-notify on the same article/digest every cycle.
--
-- Run once in the Supabase SQL editor (same as schema.sql originally).

-- Single-row table of user-configurable settings, edited from the
-- dashboard's /settings page.
create table if not exists app_settings (
  id int primary key default 1,
  push_enabled boolean not null default false,
  email_enabled boolean not null default false,
  notify_email text,
  relevance_threshold int not null default 70,  -- min ai_relevance_score to push/email on
  updated_at timestamptz not null default now(),
  constraint app_settings_singleton check (id = 1)
);

insert into app_settings (id) values (1) on conflict (id) do nothing;

alter table app_settings enable row level security;
create policy "public read app_settings" on app_settings for select using (true);
-- Writes go through the service_role key via a server-side Next.js route
-- (never exposed to the browser), so no anon insert/update policy needed.

-- Web Push subscriptions (one per browser/device that opted in).
create table if not exists push_subscriptions (
  id uuid primary key default uuid_generate_v4(),
  endpoint text not null unique,
  p256dh text not null,
  auth text not null,
  created_at timestamptz not null default now()
);

alter table push_subscriptions enable row level security;
-- No public read/write policy: only the service_role key (server-side
-- routes + the GitHub Actions pipeline) touches this table.

-- Tracking columns so the pipeline notifies once per article/digest, not
-- every 30-min cycle.
alter table articles add column if not exists notified_at timestamptz;
alter table digests add column if not exists notified_at timestamptz;

create index if not exists idx_articles_notify_pending
  on articles (ai_relevance_score)
  where notified_at is null and ai_processed = true;

-- Per-source enabled/disabled overrides for the admin page. The scraper
-- (scraper/scrape.py, run by GitHub Actions -- which clones fresh from git
-- each run) merges this with the static list in dashboard/config.json.
-- Previously the admin page tried to toggle sources by writing to
-- config.json from a Vercel serverless function -- that write only touched
-- Vercel's ephemeral filesystem and GitHub Actions never saw it, so the
-- toggle silently did nothing in production.
create table if not exists source_overrides (
  name text primary key,
  enabled boolean not null,
  updated_at timestamptz not null default now()
);

alter table source_overrides enable row level security;
create policy "public read source_overrides" on source_overrides for select using (true);
-- Writes go through the service_role key via a server-side Next.js route.
