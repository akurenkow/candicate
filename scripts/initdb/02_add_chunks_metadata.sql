-- Add metadata column to chunks table if it doesn't exist
-- This column stores structured metadata extracted during semantic parsing
-- (company_name, date_range, project_name, project_domain, role, section_kind, etc.)

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'chunks' AND column_name = 'metadata'
  ) THEN
    ALTER TABLE chunks ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb;
    CREATE INDEX IF NOT EXISTS ix_chunks_metadata_section_kind ON chunks USING gin ((metadata -> 'section_kind'));
    CREATE INDEX IF NOT EXISTS ix_chunks_metadata_company_name ON chunks USING gin ((metadata -> 'company_name'));
  END IF;
END
$$;
