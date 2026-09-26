-- 007: free embedding merge of stories that report one event in different words (analysis/merge_stories.py). Idempotent.
create table if not exists story_emb (
  story_id bigint primary key,
  emb bytea not null
);

-- Audit trail: every merge, so precision can be reviewed and a wrong merge can be reasoned about.
create table if not exists story_merges (
  merged_at timestamptz not null default now(),
  canon_id bigint not null,
  absorbed_id bigint not null,
  cos real,
  canon_title text,
  absorbed_title text
);
create index if not exists idx_story_merges_at on story_merges (merged_at desc);

do $$ begin
  if exists (select 1 from pg_roles where rolname = 'hq_ro') then
    grant select on story_emb, story_merges to hq_ro;
  end if;
end $$;
notify pgrst, 'reload schema';
