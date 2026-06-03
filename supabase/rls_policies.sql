-- ============================================================
-- ART — Row-Level Security Policies (Phase 2)
-- ============================================================
-- Applied via: python cli.py supabase-setup
--
-- All policies assume:
--   - auth.uid() returns the UUID of the authenticated Supabase user
--   - The `user` table has a `supabase_uid TEXT UNIQUE` column
--     that maps auth.uid() to our internal user_id
--   - supabase_uid is stored as a UUID string; cast to ::uuid for comparison
-- ============================================================

-- Helper: resolve the internal user_id for the current session
CREATE OR REPLACE FUNCTION art_current_user_id()
RETURNS UUID
LANGUAGE sql STABLE
AS $$
  SELECT user_id FROM "user" WHERE supabase_uid::uuid = auth.uid() LIMIT 1;
$$;

-- ── user table ──────────────────────────────────────────────
ALTER TABLE "user" ENABLE ROW LEVEL SECURITY;

CREATE POLICY "users: own row only"
  ON "user"
  FOR ALL
  USING (supabase_uid::uuid = auth.uid());

-- ── userskill ───────────────────────────────────────────────
ALTER TABLE userskill ENABLE ROW LEVEL SECURITY;

CREATE POLICY "userskill: own rows only"
  ON userskill
  FOR ALL
  USING (user_id = art_current_user_id());

-- ── experience ──────────────────────────────────────────────
ALTER TABLE experience ENABLE ROW LEVEL SECURITY;

CREATE POLICY "experience: own rows only"
  ON experience
  FOR ALL
  USING (user_id = art_current_user_id());

-- ── project ─────────────────────────────────────────────────
ALTER TABLE project ENABLE ROW LEVEL SECURITY;

CREATE POLICY "project: own rows only"
  ON project
  FOR ALL
  USING (user_id = art_current_user_id());

-- ── projectblurb ────────────────────────────────────────────
ALTER TABLE projectblurb ENABLE ROW LEVEL SECURITY;

CREATE POLICY "projectblurb: own rows only"
  ON projectblurb
  FOR ALL
  USING (
    project_id IN (
      SELECT project_id FROM project WHERE user_id = art_current_user_id()
    )
  );

-- ── jobdescription ──────────────────────────────────────────
ALTER TABLE jobdescription ENABLE ROW LEVEL SECURITY;

CREATE POLICY "jobdescription: own rows only"
  ON jobdescription
  FOR ALL
  USING (user_id = art_current_user_id());

-- ── jobskill ────────────────────────────────────────────────
ALTER TABLE jobskill ENABLE ROW LEVEL SECURITY;

CREATE POLICY "jobskill: own rows only"
  ON jobskill
  FOR ALL
  USING (
    job_id IN (
      SELECT job_id FROM jobdescription WHERE user_id = art_current_user_id()
    )
  );

-- ── userjobresult ───────────────────────────────────────────
ALTER TABLE userjobresult ENABLE ROW LEVEL SECURITY;

CREATE POLICY "userjobresult: own rows only"
  ON userjobresult
  FOR ALL
  USING (user_id = art_current_user_id());

-- ── chatmessage ─────────────────────────────────────────────
ALTER TABLE chatmessage ENABLE ROW LEVEL SECURITY;

CREATE POLICY "chatmessage: own rows only"
  ON chatmessage
  FOR ALL
  USING (
    job_id IN (
      SELECT job_id FROM jobdescription WHERE user_id = art_current_user_id()
    )
  );

-- ── skill (shared dictionary — read-only for all users) ─────
ALTER TABLE skill ENABLE ROW LEVEL SECURITY;

CREATE POLICY "skill: all users can read"
  ON skill
  FOR SELECT
  USING (true);

CREATE POLICY "skill: service role write"
  ON skill
  FOR ALL
  USING (auth.role() = 'service_role');
