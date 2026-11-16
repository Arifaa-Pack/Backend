#!/usr/bin/env bash
# Must stay executable (git mode 100755): a non-executable initdb.d *.sh is *sourced* by the entrypoint.
# Local dev only: runs once when the compose postgres volume is first created
# (/docker-entrypoint-initdb.d). Same bootstrap as the server (infra/server/postgres/db_bootstrap.sh: roles,
# databases with the company prefix, extensions, per-role search_path / statement_timeout), then - unless
# KX_INIT_APPLY_DDL=0 - applies db/ddl as each owning role and stamps the Alembic baseline so a later
# `kx migrate` starts from revision 0001 instead of re-running it.
set -euo pipefail

PREFIX="${KX_COMPANY_PREFIX:-pack_}"
export PGHOST=/var/run/postgresql PGUSER="${POSTGRES_USER:-postgres}" PGDATABASE=postgres
export KX_BOOTSTRAP_PREFIXES="$PREFIX"
for role in cms_app crm_app automation_app agents_app reporting_ro; do
  role_var="KX_PW_${PREFIX^^}${role^^}"
  export "${role_var}=${KX_DEV_DB_PASSWORD:-dev}"
done

bash /kx/db_bootstrap.sh

if [[ "${KX_INIT_APPLY_DDL:-1}" != "1" ]]; then
  exit 0
fi

declare -A DDL=([crm]=01_crm.sql [automation]=02_automation.sql [agents]=03_agents.sql)
for db in crm automation agents; do
  echo "initdb: applying ${DDL[$db]} to ${PREFIX}${db} as ${PREFIX}${db}_app"
  psql -X -v ON_ERROR_STOP=1 --quiet -d "${PREFIX}${db}" \
    -c "SET ROLE ${PREFIX}${db}_app" \
    -f "/kx/ddl/${DDL[$db]}" \
    -c "CREATE TABLE IF NOT EXISTS public.alembic_version (version_num varchar(32) NOT NULL PRIMARY KEY)" \
    -c "INSERT INTO public.alembic_version VALUES ('${db}_0001') ON CONFLICT DO NOTHING"
done
