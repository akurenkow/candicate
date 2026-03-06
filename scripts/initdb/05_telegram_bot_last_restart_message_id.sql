-- Store last restart notice message_id per user so next restart edits instead of sending duplicate.
-- Idempotent: safe when column exists or when users table is created later.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users') THEN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS last_restart_message_id INTEGER;
  END IF;
END $$;
