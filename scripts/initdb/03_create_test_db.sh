#!/bin/bash
# Create test DB and apply same schema as main DB (idempotent on rerun: CREATE DATABASE may fail if exists).
set -e
if [ -z "${POSTGRES_TEST_DB}" ]; then exit 0; fi
psql -v ON_ERROR_STOP=0 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "CREATE DATABASE ${POSTGRES_TEST_DB};" || true
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_TEST_DB" -f /docker-entrypoint-initdb.d/01_ingestion_schema.sql
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_TEST_DB" -f /docker-entrypoint-initdb.d/02_add_chunks_metadata.sql
