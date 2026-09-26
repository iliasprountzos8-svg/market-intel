-- 004: make calls scoreable and calibratable.
-- Paste into the Supabase SQL editor once. Safe to re-run. The code works without it
-- (it drops unknown columns with a warning), but calibration and benchmark-relative
-- scoring need these fields.

alter table ai_calls_log add column if not exists confidence int;              -- 0-100 stated confidence
alter table ai_calls_log add column if not exists horizon_days int;            -- how long until the call is scored
alter table ai_calls_log add column if not exists symbol text;                 -- resolved price symbol (e.g. NVDA, ^TNX)
alter table ai_calls_log add column if not exists invalidation text;           -- what would prove the call wrong
alter table ai_calls_log add column if not exists entry_price numeric;         -- price when the call was logged
alter table ai_calls_log add column if not exists entry_at timestamptz;
alter table ai_calls_log add column if not exists exit_price numeric;          -- price at the end of the horizon
alter table ai_calls_log add column if not exists asset_return_pct numeric;
alter table ai_calls_log add column if not exists benchmark_return_pct numeric; -- VT over the same window (stocks only)
alter table ai_calls_log add column if not exists excess_return_pct numeric;
alter table ai_calls_log add column if not exists resolved_at timestamptz;
