-- 009: money-intel -- personal transaction ledger, recurring-charge detection, forecasts.
-- Own tables, no coupling to the news pipeline. Idempotent; run with psql against marketintel.

create table if not exists money_transactions (
  id bigserial primary key,
  txn_date date not null,
  description text not null,
  merchant_norm text not null,
  amount numeric(12,2) not null,
  category text,
  source_file text,
  dedup_key text not null unique,
  imported_at timestamptz not null default now()
);

create index if not exists idx_money_txn_date on money_transactions (txn_date desc);
create index if not exists idx_money_txn_merchant on money_transactions (merchant_norm);
create index if not exists idx_money_txn_category on money_transactions (category);

create table if not exists money_recurring (
  merchant_norm text primary key,
  typical_amount numeric(12,2) not null,
  typical_interval_days real not null,
  last_seen date not null,
  n_occurrences int not null,
  status text not null default 'active',  -- active | lapsed
  first_detected timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'hq_ro') then
    grant select on money_transactions, money_recurring to hq_ro;
  end if;
end $$;

notify pgrst, 'reload schema';
