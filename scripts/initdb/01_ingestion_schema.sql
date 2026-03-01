-- FLOW-02: documents, chunks, ingestion_jobs (idempotent)
-- Runs automatically on first Postgres start via /docker-entrypoint-initdb.d/
-- If tables already exist (e.g. after alembic), statements are no-ops.

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_status') THEN
    CREATE TYPE document_status AS ENUM ('pending', 'indexed', 'indexing_failed', 'superseded');
  END IF;
END
$$;

CREATE TABLE IF NOT EXISTS documents (
  id UUID PRIMARY KEY,
  external_id VARCHAR(255),
  owner_id VARCHAR(255) NOT NULL,
  filename VARCHAR(512) NOT NULL,
  content_type VARCHAR(128) NOT NULL,
  content TEXT NOT NULL,
  original_content TEXT,
  version INTEGER NOT NULL DEFAULT 1,
  status document_status NOT NULL DEFAULT 'pending',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  error_message TEXT
);

CREATE INDEX IF NOT EXISTS ix_documents_owner_id ON documents (owner_id);
CREATE INDEX IF NOT EXISTS ix_documents_external_id ON documents (external_id);
CREATE INDEX IF NOT EXISTS ix_documents_owner_external ON documents (owner_id, external_id);

CREATE TABLE IF NOT EXISTS chunks (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chunks_document_id ON chunks (document_id);

CREATE TABLE IF NOT EXISTS ingestion_jobs (
  job_id VARCHAR(36) PRIMARY KEY,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  document_id VARCHAR(36),
  error_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
