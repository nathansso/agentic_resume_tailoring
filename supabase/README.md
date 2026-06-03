# Supabase Setup Guide

## Phase 1 — PostgreSQL database (current)

Connect ART to a hosted Supabase PostgreSQL database instead of local SQLite.

### 1. Create a Supabase project

Go to [supabase.com](https://supabase.com), create a new project, and note your project ref.

### 2. Get the connection string

In your Supabase dashboard: **Settings → Database → Connection string → URI**

Direct connection (development / long-lived connections):
```
postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
```

Connection pooler (production / short-lived connections — use port 6543):
```
postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
```

### 3. Set DATABASE_URL

Add to your `.env`:
```
DATABASE_URL=postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
```

### 4. Initialize the schema

On first run, `init_db()` calls `SQLModel.metadata.create_all(engine)` which creates all tables automatically. Just launch the app:

```bash
python -m tui.app
# or via Docker:
docker compose run --rm art
```

Tables created: `user`, `skill`, `userskill`, `experience`, `project`, `projectblurb`, `jobdescription`, `jobskill`, `userjobresult`, `chatmessage`.

### 5. Migrate existing local data (optional)

If you have data in `~/.art/art.db` that you want to move to Supabase, use a tool like [pgloader](https://pgloader.io/) or export/import via the Supabase dashboard.

---

## Phase 2 — Auth + Row-Level Security (upcoming, see rls_policies.sql)

Phase 2 adds Supabase Auth so multiple users can each have their own isolated data. The SQL in `rls_policies.sql` documents the planned RLS policies. Do not apply them until Phase 2 is implemented — the schema needs a `supabase_uid` column on the `user` table first.

---

## Supabase environment variables (Phase 2)

```
SUPABASE_URL=https://[PROJECT-REF].supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_SERVICE_ROLE_KEY=eyJ...   # backend only, never expose to clients
```
