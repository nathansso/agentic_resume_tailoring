"""Tests for the PostgreSQL UUID-column repair migration.

Regression coverage for jobs 500ing on Supabase Postgres: the issue-35
migration added jobdescription.user_id as TEXT, so `user_id = <uuid param>`
had no operator (`text = uuid`) and every job list/lookup failed.
"""
from database.db import _uuid_column_fix_statements


def test_text_uuid_column_gets_alter_statement():
    stmts = _uuid_column_fix_statements({("jobdescription", "user_id")})
    assert stmts == [
        'ALTER TABLE "jobdescription" ALTER COLUMN "user_id" '
        "TYPE uuid USING NULLIF(\"user_id\", '')::uuid"
    ]


def test_legitimate_text_columns_untouched():
    # These are str in the models — must never be retyped even though they are
    # text in the DB and id-like in name.
    text_cols = {
        ("user", "supabase_uid"),
        ("user", "github_access_token"),
        ("jobdescription", "description"),
        ("aiusage", "kind"),
    }
    assert _uuid_column_fix_statements(text_cols) == []


def test_all_uuid_model_columns_covered_when_text():
    # If every UUID column in the schema were text, each gets exactly one fix.
    from sqlmodel import SQLModel
    import sqlalchemy as sa

    uuid_cols = {
        (t.name, c.name)
        for t in SQLModel.metadata.tables.values()
        for c in t.columns
        if isinstance(c.type, sa.Uuid)
    }
    stmts = _uuid_column_fix_statements(uuid_cols)
    assert len(stmts) == len(uuid_cols)
    # Sanity: the known-affected column is in the set.
    assert ("jobdescription", "user_id") in uuid_cols
