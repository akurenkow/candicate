-- Max message_id seen in chat; restart notice is edited only when still last.
-- Idempotent: safe when column exists or when users table is created later.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'users') THEN
    ALTER TABLE users ADD COLUMN IF NOT EXISTS last_telegram_message_id INTEGER;
  END IF;
END $$;
