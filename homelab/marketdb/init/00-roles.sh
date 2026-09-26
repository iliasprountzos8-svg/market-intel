#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username postgres --dbname marketintel <<EOSQL
create role anon nologin;
create role service_role nologin bypassrls;
create role authenticator login noinherit password '${POSTGRES_PASSWORD}';
grant anon, service_role to authenticator;
EOSQL
