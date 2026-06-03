-- ============================================================
-- ART Phase 2 — Auth schema migration
-- Run this against your Supabase project ONCE before applying
-- rls_policies.sql.
-- ============================================================

-- Add auth columns to user table
ALTER TABLE "user"
  ADD COLUMN IF NOT EXISTS username TEXT UNIQUE,
  ADD COLUMN IF NOT EXISTS password_hash TEXT,
  ADD COLUMN IF NOT EXISTS supabase_uid TEXT UNIQUE;

-- Add user ownership column to job descriptions
ALTER TABLE jobdescription
  ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES "user"(user_id);

-- Index for fast username lookups (auth hot path)
CREATE INDEX IF NOT EXISTS ix_user_username ON "user"(username);

-- Index for job ownership queries
CREATE INDEX IF NOT EXISTS ix_jobdescription_user_id ON jobdescription(user_id);
