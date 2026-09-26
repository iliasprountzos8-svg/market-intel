#!/bin/bash
# weekly housekeeping: keep the database lean (SSD is 30% worn) without losing analytical value
cd ~/services/marketdb || exit 1
docker compose exec -T db psql -U postgres -d marketintel <<'SQL' </dev/null
update articles set full_text = null, summary_raw = null where published_at < now() - interval '60 days' and (full_text is not null or summary_raw is not null);
delete from source_health where created_at < now() - interval '30 days';
vacuum (analyze);
SQL
docker compose exec -T db psql -U postgres -d marketintel -At -c "select 'db size: ' || pg_size_pretty(pg_database_size('marketintel'))" </dev/null >> ~/market-intel/logs/maintenance.log
date -Is >> ~/market-intel/logs/maintenance.log
