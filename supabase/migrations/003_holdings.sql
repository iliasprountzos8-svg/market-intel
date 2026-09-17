-- Moves real portfolio numbers (share counts, cost basis) out of the git
-- repo and into Supabase, ahead of making the repo public for free GitHub
-- Actions minutes. Same public-read pattern as articles/digests -- the
-- dashboard itself is already unauthenticated, so this only removes the
-- numbers from git history/source, not from the live site.
--
-- Run once in the Supabase SQL editor.

create table if not exists holdings (
  symbol text primary key,
  name text not null,
  shares numeric not null,
  keywords text[] not null default '{}',
  display_order int not null default 0
);

alter table holdings enable row level security;
create policy "public read holdings" on holdings for select using (true);

create table if not exists portfolio_meta (
  id int primary key default 1,
  cost_basis_eur numeric not null,
  as_of date not null,
  constraint portfolio_meta_singleton check (id = 1)
);

alter table portfolio_meta enable row level security;
create policy "public read portfolio_meta" on portfolio_meta for select using (true);

-- Deliberately no seed INSERTs here: this file gets committed to a public
-- repo. Real share counts / cost basis are inserted directly against
-- Supabase (via the service role key, never through git) after this schema
-- is applied -- see README "Live deployment" for how.
