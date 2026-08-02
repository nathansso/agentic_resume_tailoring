# Supabase Setup Guide

ART runs on **Supabase Postgres with Supabase Auth** in production. Both are
shipped and in use — this guide is the setup runbook for a new environment, not
a plan.

Contents of this directory:

| File | Purpose |
|---|---|
| `rls_policies.sql` | Row-level security policies enforcing per-user isolation |
| `migrations/phase2_auth.sql` | The auth migration (adds `supabase_uid` and friends) |

Both are applied for you by `python cli.py supabase-setup`.

---

## 1. Create a Supabase project

Go to [supabase.com](https://supabase.com), create a project, and note the
project ref.

## 2. Get the connection string

**Settings → Database → Connection string → URI.**

Use the **connection pooler** (port 6543) for any hosted deploy — short-lived
serverless connections exhaust the direct pool:

```
postgresql://postgres.[PROJECT-REF]:[PASSWORD]@aws-0-[REGION].pooler.supabase.com:6543/postgres
```

The direct connection (port 5432) is fine for local, long-lived sessions:

```
postgresql://postgres:[PASSWORD]@db.[PROJECT-REF].supabase.co:5432/postgres
```

## 3. Set the environment variables

```bash
DATABASE_URL=postgresql://...            # from step 2
SUPABASE_URL=https://[PROJECT-REF].supabase.co
SUPABASE_ANON_KEY=eyJ...                 # safe to expose to the client
SUPABASE_SERVICE_ROLE_KEY=eyJ...         # backend only — never expose to clients
SUPABASE_JWT_SECRET=                     # Settings → API → JWT Secret
APP_BASE_URL=https://<your-domain>       # used to build password-reset links
```

`database/auth.py::supabase_configured()` is the single gate that selects the
auth mode, and it is **fail-closed**: whenever these are set, the local
password/cookie fallback can never run. See `web/CLAUDE.md` for the full auth
flow.

In **Authentication → URL Configuration**, add `<APP_BASE_URL>/reset-password`
to the allowed Redirect URLs, or password recovery will bounce.

## 4. Initialize the schema, RLS, and auth migration

```bash
python cli.py supabase-setup
```

This applies `migrations/phase2_auth.sql` and `rls_policies.sql`. Tables
themselves are created by `init_db()` on first boot
(`SQLModel.metadata.create_all`), followed by the idempotent `ALTER TABLE`
migration chain in `database/db.py` — including `_migrate_pg_uuid_columns` and
`_migrate_pg_vector_columns`, which are Postgres-only repairs with no SQLite
equivalent. Just launching the app is enough to create tables:

```bash
uvicorn web.app:app --port 8000
# or:  docker compose up --build
```

The table set is defined by `database/models.py` — read it there rather than
from a list here, which is what went stale last time.

## 5. Verify isolation

RLS enforces per-user isolation on every request. To confirm: register two
users and check that each sees only their own jobs and resume data.
`tests/test_user_isolation.py` covers this in the suite.

---

## Enabling pgvector

Indexed vector search (#60) needs the extension enabled on the project:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

The local `docker compose` Postgres uses the `pgvector/pgvector:pg16` image, so
it is already available there.

## Migrating existing local data (optional)

To move data from `~/.art/art.db` into Supabase, use
[pgloader](https://pgloader.io/) or export/import through the Supabase
dashboard. There is no in-repo migration path — SQLite is a development
fallback, not a source of production data.
