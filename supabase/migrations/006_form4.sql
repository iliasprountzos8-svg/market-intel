-- Form 4 insider buy/sell transactions, feeding analysis/form4_signal.py.
-- Local homelab Postgres only (not mirrored to the real Supabase): structured numeric data for
-- signal derivation, not reader-facing article content. Applied by hand, see docs/HOMELAB_EDITION.md.
create table if not exists form4_transactions (
    id bigserial primary key,
    symbol text not null,
    cik text not null,
    accession text not null,
    insider_name text,
    is_officer boolean,
    is_director boolean,
    is_ten_pct_owner boolean,
    officer_title text,
    transaction_code text,          -- P=open market purchase, S=sale, A=grant/award, etc.
    transaction_date date,
    shares numeric,
    price_per_share numeric,
    shares_owned_after numeric,
    filed_at timestamptz,
    ingested_at timestamptz not null default now(),
    unique (accession, insider_name, transaction_code, transaction_date, shares)
);
create index if not exists idx_form4_symbol_date on form4_transactions(symbol, transaction_date);
