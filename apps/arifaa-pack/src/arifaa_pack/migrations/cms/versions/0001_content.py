"""D54: the CMS package's own database (schema `cms`): documents, versions, media, audit, outbox.

Revision ID: cms_0001
Revises:
Create Date: 2026-11-19

Source: the SRS §7 CMS flow (draft -> preview -> publish, every version kept, no prices) and the 19 Nov decision
that the CMS is a platform package (`kx.cms`) instead of Payload. The `cms` database and its `cms_app` role
already exist (infra/server/postgres/db_bootstrap.sh, docker/postgres/initdb.sh); this revision creates the
schema inside it. There is no db/ddl baseline for this database: it starts here. Idempotent; forward-only.
"""

from __future__ import annotations

from arifaa_pack.migrations import execute_sql

revision = "cms_0001"
down_revision = None
branch_labels = None
depends_on = None

SQL = r"""
CREATE SCHEMA IF NOT EXISTS cms;
SET search_path = cms, public;

CREATE TABLE IF NOT EXISTS documents (
    id                 uuid PRIMARY KEY,
    collection         varchar(64)  NOT NULL CHECK (collection ~ '^[a-z][a-z0-9_]{1,63}$'),
    key                varchar(128) NOT NULL CHECK (key ~ '^[a-z0-9][a-z0-9_-]{0,127}$'),
    sort_order         integer      NOT NULL DEFAULT 0,
    published_version  integer,
    draft_version      integer,
    published_at       timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT uq_documents_collection UNIQUE (collection, key)
);
CREATE INDEX IF NOT EXISTS ix_documents_published ON documents (collection, sort_order)
    WHERE published_version IS NOT NULL;

CREATE TABLE IF NOT EXISTS versions (
    id            uuid PRIMARY KEY,
    document_id   uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    number        integer NOT NULL CHECK (number > 0),
    status        varchar(16) NOT NULL CHECK (status IN ('draft', 'published', 'archived')),
    revision      integer NOT NULL DEFAULT 1,
    data          jsonb NOT NULL,
    author        varchar(200) NOT NULL,
    note          text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    saved_at      timestamptz NOT NULL DEFAULT now(),
    published_at  timestamptz,
    published_by  varchar(200),
    CONSTRAINT uq_versions_document_id UNIQUE (document_id, number)
);
-- at most one open draft and one live version per document
CREATE UNIQUE INDEX IF NOT EXISTS ux_versions_one_draft ON versions (document_id) WHERE status = 'draft';
CREATE UNIQUE INDEX IF NOT EXISTS ux_versions_one_published ON versions (document_id) WHERE status = 'published';

CREATE TABLE IF NOT EXISTS media_assets (
    id            uuid PRIMARY KEY,
    object_key    varchar(300) NOT NULL UNIQUE,
    sha256        varchar(64)  NOT NULL UNIQUE CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    filename      varchar(300) NOT NULL,
    content_type  varchar(100) NOT NULL,
    size_bytes    bigint NOT NULL CHECK (size_bytes > 0),
    width         integer,
    height        integer,
    alt           jsonb NOT NULL DEFAULT '{}'::jsonb,
    confirmed     boolean NOT NULL DEFAULT false,
    created_by    varchar(200) NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS state (
    id          integer PRIMARY KEY CHECK (id = 1),
    revision    bigint NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO state (id, revision) VALUES (1, 0) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS audit_log (
    id          uuid PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       varchar(200) NOT NULL,
    action      varchar(64)  NOT NULL,
    collection  varchar(64),
    key         varchar(128),
    version     integer,
    detail      jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS ix_audit_log_doc ON audit_log (collection, key, at DESC);

-- the platform's transactional outbox / inbox (same shape in every database, kx.core.events)
CREATE TABLE IF NOT EXISTS outbox_events (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      text NOT NULL,
    event_version   smallint NOT NULL DEFAULT 1,
    correlation_id  uuid,
    sequence        bigint GENERATED ALWAYS AS IDENTITY,
    aggregate_type  text NOT NULL,
    aggregate_id    text NOT NULL,
    payload         jsonb NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    published_at    timestamptz,
    attempts        smallint NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_unpublished_idx ON outbox_events (created_at) WHERE published_at IS NULL;
CREATE TABLE IF NOT EXISTS inbox_events (
    event_id      uuid NOT NULL,
    handler       text NOT NULL,
    processed_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, handler)
);
"""


def upgrade() -> None:
    execute_sql(SQL)


def downgrade() -> None:
    raise NotImplementedError("forward-only; restore from snapshot")
