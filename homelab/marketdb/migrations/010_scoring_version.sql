-- 010: record which scoring rules produced a stored outcome (2 = trading-day anchored window,
-- stocks judged vs VT, yields in bp, re-logged views de-duplicated). Idempotent.
alter table ai_calls_log add column if not exists scoring_version smallint;
notify pgrst, 'reload schema';
