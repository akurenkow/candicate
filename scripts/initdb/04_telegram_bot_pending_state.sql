-- Persist telegram-bot "waiting" state so restart does not lose context (e.g. WAITING_COVER_LETTER_VACANCY).
-- Idempotent: safe when column exists or when users table is created later.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users') THEN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_state TEXT;
  END IF;
END $$;
