# How to Apply Migrations

## Using Alembic (Recommended)

### Apply all pending migrations

```bash
# From project root
cd migrations
alembic upgrade head
```

### Apply specific migration

```bash
alembic upgrade 004_add_original_content
```

### Rollback last migration

```bash
alembic downgrade -1
```

### Rollback to specific revision

```bash
alembic downgrade 003_chunks_metadata
```

### Check current revision

```bash
alembic current
```

### View migration history

```bash
alembic history
```

## Manual Application (if needed)

If Alembic is not available or you need to apply migration manually:

### For Migration 004 (Add original_content)

```sql
-- Connect to your PostgreSQL database
psql -U your_user -d your_database

-- Apply migration
ALTER TABLE documents ADD COLUMN original_content TEXT;

-- Verify
\d documents
```

### Rollback Migration 004

```sql
ALTER TABLE documents DROP COLUMN original_content;
```

## Docker Environment

If running in Docker Compose:

```bash
# Enter the ingestion-service container
docker compose exec ingestion-service bash

# Run migrations
cd /app/migrations
alembic upgrade head
```

Or run directly:

```bash
docker compose exec ingestion-service alembic -c /app/migrations/alembic.ini upgrade head
```

## New Database Setup

For fresh database initialization, migrations are not needed. The schema in `scripts/initdb/01_ingestion_schema.sql` already includes all fields from migrations.

This script runs automatically when PostgreSQL container starts for the first time.

## Troubleshooting

### "Can't locate revision identified by '004_add_original_content'"

Make sure you're in the correct directory and the migration file exists:

```bash
ls migrations/versions/004_add_original_content.py
```

### "Target database is not up to date"

Check current revision and apply pending migrations:

```bash
alembic current
alembic upgrade head
```

### "Column already exists"

The migration is idempotent-safe. If column exists, you can mark migration as applied:

```bash
alembic stamp 004_add_original_content
```
