# PostgreSQL 18 + pgvector (same image as local and CI) with WAL-G for continuous backups to Cloudflare R2 (D48).
#   docker build -t kairox/postgres:18 infra/server/postgres
# M11/M16: the base image is pinned to a pgvector release and its digest (pg18 = 0.8.7-pg18-bookworm on 18 Nov,
# Debian 12, glibc 2.36: runs the WAL-G Ubuntu 22.04 build, glibc 2.35). Dependabot proposes digest updates.
FROM pgvector/pgvector:0.8.7-pg18-bookworm@sha256:38681dc6eaf55308e2a4c6de392d131652dcfac70e1263e35cd9b5ea3fe39782

# M11: the download is verified against a pinned SHA-256 and the build fails without one. The value is the
# release's own wal-g-pg-ubuntu-22.04-amd64.tar.gz.sha256, checked against a fresh download on 18 Nov.
# Upgrading WAL-G = change both build arguments together.
ARG WALG_VERSION=v3.0.7
ARG WALG_SHA256=76d51ed915165d45314bc947300b9d1776adb2d875d857f580a730fd6f66900e
# hadolint ignore=DL3008
RUN test -n "$WALG_SHA256" || { echo "WALG_SHA256 is required" >&2; exit 1; } \
 && apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL -o /tmp/walg.tar.gz \
      "https://github.com/wal-g/wal-g/releases/download/${WALG_VERSION}/wal-g-pg-ubuntu-22.04-amd64.tar.gz" \
 && printf '%s  %s\n' "$WALG_SHA256" /tmp/walg.tar.gz > /tmp/walg.sha256 \
 && sha256sum -c /tmp/walg.sha256 \
 && tar -xzf /tmp/walg.tar.gz -C /usr/local/bin \
 && mv /usr/local/bin/wal-g-pg-ubuntu-22.04-amd64 /usr/local/bin/wal-g \
 && chmod 0755 /usr/local/bin/wal-g \
 && rm -f /tmp/walg.tar.gz /tmp/walg.sha256 \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY --chmod=0644 postgresql.conf pg_hba.conf pg_ident.conf /etc/postgresql/
COPY --chmod=0755 db_bootstrap.sh /kx/db_bootstrap.sh
CMD ["postgres", "-c", "config_file=/etc/postgresql/postgresql.conf"]
