#!/usr/bin/env bash
# =====================================================================================
# Kairox DB bootstrap - idempotent. Run as the Postgres superuser BEFORE `kx migrate` (D48, docs/DATABASE.md).
#
# For every company prefix ("pack_" today; more projects later, docs/MULTI_PROJECT.md) it ensures:
#   roles      <p><db>_app for every logical DB + <p>reporting_ro
#              (LOGIN, password = $KX_PW_<ROLE_UPPER> when the role is created; an existing role's password
#              is changed only with KX_BOOTSTRAP_ROTATE=1, M6: every ALTER ... PASSWORD stores a new SCRAM salt,
#              which breaks PgBouncer's userlist.txt until it is regenerated, runbooks/rotate-secrets.md)
#   databases  <p>cms <p>crm <p>automation <p>agents (KX_BOOTSTRAP_LOGICALS), each OWNED by its app role.
#              Phoenix has its own Postgres container (Telemetry tier) and is not created here by default.
#   per role   search_path = <db>,public and statement_timeout (KX_STATEMENT_TIMEOUT, default 15s): the app
#              connects through PgBouncer in transaction mode, which does not pass startup parameters
#   connect    REVOKE CONNECT/TEMP FROM PUBLIC; each role may connect only to its own DB;
#              reporting_ro may connect to <p>crm
#   extensions crm: citext, pg_trgm, btree_gist | automation: citext | agents: vector, pg_trgm
#   reporting  schema crm pre-created (owner crm_app) + USAGE; table grants come from migration crm_0013 (D5)
#
# Environment:
#   PGHOST PGPORT PGUSER PGPASSWORD PGDATABASE=postgres                       (libpq; direct, not PgBouncer)
#   KX_BOOTSTRAP_PREFIXES   comma-separated prefixes (e.g. "pack_"); empty string = no prefix (tests)
#   KX_BOOTSTRAP_LOGICALS   space-separated logical DBs (default "cms crm automation agents")
#   KX_PW_<ROLE>            e.g. KX_PW_PACK_CRM_APP, KX_PW_PACK_REPORTING_RO
#   KX_BOOTSTRAP_ROTATE     1 = also set the password of roles that already exist (then regenerate userlist.txt)
# Server: docker compose -f infra/server/stacks/compose.data.yml exec -e KX_BOOTSTRAP_PREFIXES=pack_ -e KX_PW_... \
#           postgres /kx/db_bootstrap.sh
#   (inside the container PGHOST / PGUSER are the local socket and `postgres`: the superuser may log in over the
#   socket only, infra/server/postgres/pg_hba.conf)
# =====================================================================================
set -euo pipefail

read -r -a LOGICALS <<<"${KX_BOOTSTRAP_LOGICALS:-cms crm automation agents}"
STATEMENT_TIMEOUT="${KX_STATEMENT_TIMEOUT:-15s}"
declare -A EXTENSIONS=(
  [cms]=""
  [crm]="citext pg_trgm btree_gist"
  [automation]="citext"
  [agents]="vector pg_trgm"
  [phoenix]=""
)

PSQL=(psql -X -v ON_ERROR_STOP=1 --quiet --no-psqlrc)
ROTATE="${KX_BOOTSTRAP_ROTATE:-0}"

log() { printf '%s db-bootstrap: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }

if [[ -z "${KX_BOOTSTRAP_PREFIXES:-}" ]]; then
  PREFIXES=("")
else
  IFS=',' read -r -a PREFIXES <<<"${KX_BOOTSTRAP_PREFIXES}"
fi

password_for() {
  local role="$1" var
  var="KX_PW_$(tr '[:lower:]' '[:upper:]' <<<"$role")"
  if [[ -z "${!var:-}" ]]; then
    log "missing password env ${var} for role ${role}"
    exit 2
  fi
  printf '%s' "${!var}"
}

# psql variables (:'name') are interpolated outside dollar-quoted bodies only, so statements are
# built with format(%I, %L) and executed with \gexec.
ensure_role() {
  local role="$1" pw
  pw="$(password_for "$role")"
  [[ "$ROTATE" != "1" ]] || log "role ${role}: password rotated (regenerate its userlist.txt line)"
  "${PSQL[@]}" -d postgres -v role="$role" -v pw="$pw" -v rotate="$ROTATE" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'role', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN NOCREATEDB NOCREATEROLE', :'role') \gexec
-- M6: same password, new salt; so only on request (KX_BOOTSTRAP_ROTATE=1)
SELECT format('ALTER ROLE %I PASSWORD %L', :'role', :'pw') WHERE :'rotate' = '1' \gexec
-- PG16+: a non-superuser bootstrap user needs SET to create databases owned by the role and
-- INHERIT to pass owner checks (GRANT/REVOKE on the database, CREATE on its public schema).
SELECT format('GRANT %I TO CURRENT_USER WITH INHERIT TRUE, SET TRUE', :'role')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = current_user AND rolsuper) \gexec
SQL
}

ensure_database() {
  local db="$1" owner="$2"
  "${PSQL[@]}" -d postgres -v db="$db" -v owner="$owner" <<'SQL'
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8'' TEMPLATE template0', :'db', :'owner')
 WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db', :'owner') \gexec
SELECT format('REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', :'db') \gexec
SELECT format('GRANT CONNECT, TEMPORARY ON DATABASE %I TO %I', :'db', :'owner') \gexec
SQL
}

# Extensions are created by the superuser: the app roles could not create `vector` themselves.
ensure_extensions() {
  local db="$1" exts="$2" ext
  for ext in $exts; do
    "${PSQL[@]}" -d "$db" -v ext="$ext" <<'SQL'
SELECT format('CREATE EXTENSION IF NOT EXISTS %I', :'ext') \gexec
SQL
  done
}

ensure_reporting() {
  local db="$1" owner="$2" ro="$3"
  "${PSQL[@]}" -d postgres -v db="$db" -v ro="$ro" <<'SQL'
SELECT format('GRANT CONNECT ON DATABASE %I TO %I', :'db', :'ro') \gexec
SQL
  "${PSQL[@]}" -d "$db" -v owner="$owner" -v ro="$ro" <<'SQL'
-- as the owner: a non-superuser bootstrap user has no CREATE privilege on a database it does not own
SELECT format('SET ROLE %I', :'owner') \gexec
CREATE SCHEMA IF NOT EXISTS crm;
SELECT format('GRANT USAGE ON SCHEMA crm TO %I', :'ro') \gexec
-- D5: table grants are owned by the migrations (crm_0013: no credentials, cost or minimum prices). Never grant
-- ALL TABLES here: this script is re-run on every deploy and would undo that.
RESET ROLE;
SQL
}

# D48: PgBouncer (transaction mode) drops startup parameters, so the settings live on the role itself.
ensure_role_settings() {
  local role="$1" schema="$2"
  "${PSQL[@]}" -d postgres -v role="$role" -v schema="$schema" -v timeout="$STATEMENT_TIMEOUT" <<'SQL'
SELECT format('ALTER ROLE %I SET search_path = %I, public', :'role', :'schema') \gexec
SELECT format('ALTER ROLE %I SET statement_timeout = %L', :'role', :'timeout') \gexec
SQL
}

log "server: $("${PSQL[@]}" -d postgres -Atc 'SHOW server_version') as $("${PSQL[@]}" -d postgres -Atc 'SELECT current_user')"

for p in "${PREFIXES[@]}"; do
  label="${p:-<none>}"
  log "tenant prefix ${label}"
  for l in "${LOGICALS[@]}"; do
    ensure_role "${p}${l}_app"
  done
  ensure_role "${p}reporting_ro"

  for l in "${LOGICALS[@]}"; do
    ensure_database "${p}${l}" "${p}${l}_app"
    ensure_extensions "${p}${l}" "${EXTENSIONS[$l]:-}"
    ensure_role_settings "${p}${l}_app" "$l"  # D54: cms is a platform package now (schema cms)
  done
  [[ " ${LOGICALS[*]} " == *" crm "* ]] && ensure_reporting "${p}crm" "${p}crm_app" "${p}reporting_ro"
  log "tenant prefix ${label}: ok"
done

log "done"
