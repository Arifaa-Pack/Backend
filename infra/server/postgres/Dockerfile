# PostgreSQL 18 + pgvector (same image as local and CI) with WAL-G for continuous backups to Cloudflare R2 (D48).
#   docker build -t kairox/postgres:18 infra/server/postgres
FROM pgvector/pgvector:pg18

ARG WALG_VERSION=v3.0.7
ARG WALG_SHA256=""
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL -o /tmp/walg.tar.gz \
      "https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}/wal-g-pg-ubuntu-22.04-amd64.tar.gz" \
 && if [ -n "$WALG_SHA256" ]; then echo "${WALG_SHA256}  /tmp/walg.tar.gz" | sha256sum -c -; fi \
 && tar -xzf /tmp/walg.tar.gz -C /usr/local/bin \
 && mv /usr/local/bin/wal-g-pg-ubuntu-22.04-amd64 /usr/local/bin/wal-g \
 && chmod 0755 /usr/local/bin/wal-g \
 && rm -f /tmp/walg.tar.gz \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --chmod=0644 postgresql.conf pg_hba.conf pg_ident.conf /etc/postgresql/
COPY --chmod=0755 db_bootstrap.sh /kx/db_bootstrap.sh
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
