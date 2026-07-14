"""Manual edit & delete for ingested knowledge-graph rows (issue #92).

Covers the service layer (edit/delete + self-heal/re-ingest protection + caller
scoping) and the router's HTTP status mapping for cross-user access.
"""
from uuid import UUID

import pytest
from sqlmodel import Session, select

import services
from database.models import User, Experience, Education, Project, DeletedEntry


def _make_user(engine, email):
    with Session(engine) as s:
        u = User(name="U", email=email)
        s.add(u); s.commit(); s.refresh(u)
        return u


def _make_parser(engine, monkeypatch, user):
    import agents.parser as parser_module
    monkeypatch.setattr(parser_module, "engine", engine)
    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user = user
    agent.llm = None
    return agent


def _add_experience(engine, uid, **kw):
    with Session(engine) as s:
        row = Experience(user_id=uid, **kw)
        s.add(row); s.commit(); s.refresh(row)
        return str(row.experience_id)


# ── edit ────────────────────────────────────────────────────────────────────────

def test_update_experience_persists_and_flags(isolated_engine):
    user = _make_user(isolated_engine, "e1@example.com")
    eid = _add_experience(isolated_engine, user.user_id,
                          title="Data Science Intern", company="IDX Exchange")

    row = services.update_experience(
        user.user_id, eid,
        {"description": "Owned the ranking model.", "bullets": ["a", "b"]},
    )
    assert row["description"] == "Owned the ranking model."
    with Session(isolated_engine) as s:
        e = s.get(Experience, UUID(eid))
        assert e.manually_edited is True
        assert e.bullets == ["a", "b"]


def test_update_experience_empty_title_raises(isolated_engine):
    user = _make_user(isolated_engine, "e2@example.com")
    eid = _add_experience(isolated_engine, user.user_id, title="X", company="Y")
    with pytest.raises(ValueError):
        services.update_experience(user.user_id, eid, {"title": "   "})


def test_update_missing_row_returns_none(isolated_engine):
    user = _make_user(isolated_engine, "e3@example.com")
    import uuid
    assert services.update_experience(user.user_id, str(uuid.uuid4()), {"title": "x"}) is None
    assert services.update_experience(user.user_id, "not-a-uuid", {"title": "x"}) is None


# ── self-heal / re-ingest protection ─────────────────────────────────────────────

def test_edited_experience_survives_reingest(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, "e4@example.com")
    eid = _add_experience(isolated_engine, user.user_id,
                          title="Data Science Intern", company="IDX Exchange",
                          description="original")
    services.update_experience(
        user.user_id, eid,
        {"description": "My corrected description", "bullets": ["kept"]},
    )

    agent = _make_parser(isolated_engine, monkeypatch, user)
    # Re-ingest the same experience with different content.
    agent._save_experiences(
        [{"title": "Data Science Intern", "company": "IDX Exchange",
          "description": "ingested overwrite", "bullets": ["x", "y"]}],
        "resume",
    )
    from sqlmodel import Session as S
    with S(isolated_engine) as s:
        agent._heal_experiences(s, user.user_id)
        s.commit()

    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].description == "My corrected description"  # not reverted
    assert rows[0].bullets == ["kept"]


def test_edited_experience_survives_heal_against_duplicate(isolated_engine, monkeypatch):
    """Even when an un-edited fuzzy-duplicate exists, heal keeps the edited row."""
    user = _make_user(isolated_engine, "e5@example.com")
    eid = _add_experience(isolated_engine, user.user_id,
                          title="Data Science Intern", company="IDX Exchange")
    services.update_experience(user.user_id, eid, {"description": "edited"})
    # A richer-looking duplicate arrives via ingest.
    _add_experience(isolated_engine, user.user_id,
                    title="Data Science Intern", company="IDXExchange",
                    description="ingested", bullets=["1", "2", "3"])

    agent = _make_parser(isolated_engine, monkeypatch, user)
    from sqlmodel import Session as S
    with S(isolated_engine) as s:
        agent._heal_experiences(s, user.user_id)
        s.commit()

    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].manually_edited is True
    assert rows[0].description == "edited"  # user edit preserved, not backfilled over


def test_deleted_experience_not_resurrected(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, "e6@example.com")
    eid = _add_experience(isolated_engine, user.user_id,
                          title="Data Science Intern", company="IDX Exchange")

    assert services.delete_experience(user.user_id, eid) is True

    agent = _make_parser(isolated_engine, monkeypatch, user)
    agent._save_experiences(
        [{"title": "Data Science Intern", "company": "IDXExchange",  # variant
          "description": "back again"}],
        "resume",
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        tombs = s.exec(select(DeletedEntry).where(DeletedEntry.user_id == user.user_id)).all()
    assert rows == []            # not resurrected, even via a name variant
    assert len(tombs) == 1


# ── caller scoping / isolation ───────────────────────────────────────────────────

def test_cannot_edit_or_delete_another_users_experience(isolated_engine):
    a = _make_user(isolated_engine, "owner@example.com")
    b = _make_user(isolated_engine, "attacker@example.com")
    eid = _add_experience(isolated_engine, a.user_id, title="Secret", company="Acme")

    assert services.update_experience(b.user_id, eid, {"title": "hacked"}) is None
    assert services.delete_experience(b.user_id, eid) is False

    with Session(isolated_engine) as s:
        e = s.get(Experience, UUID(eid))
    assert e is not None and e.title == "Secret"  # untouched


def test_router_edit_cross_user_is_404(isolated_engine):
    import web.routers.profile_router as pr
    a = _make_user(isolated_engine, "owner2@example.com")
    b = _make_user(isolated_engine, "attacker2@example.com")
    eid = _add_experience(isolated_engine, a.user_id, title="Secret", company="Acme")

    from fastapi import HTTPException
    body = pr.ExperienceUpdate(title="hacked")
    with pytest.raises(HTTPException) as exc:
        pr.edit_experience(eid, body, user=b)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException) as exc:
        pr.remove_experience(eid, user=b)
    assert exc.value.status_code == 404


# ── education + project parity ────────────────────────────────────────────────────

def test_education_edit_delete_and_tombstone(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, "ed1@example.com")
    with Session(isolated_engine) as s:
        row = Education(user_id=user.user_id, institution="UC San Diego", degree="B.S. CS")
        s.add(row); s.commit(); s.refresh(row)
        edu_id = str(row.education_id)

    updated = services.update_education(user.user_id, edu_id, {"gpa": "3.9"})
    assert updated["gpa"] == "3.9"
    assert services.delete_education(user.user_id, edu_id) is True

    agent = _make_parser(isolated_engine, monkeypatch, user)
    agent._save_education(
        [{"institution": "UC San Diego", "degree": "B.S. CS"}], "resume"
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert rows == []  # tombstone blocked resurrection


def test_project_edit_delete_and_tombstone(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, "pr1@example.com")
    with Session(isolated_engine) as s:
        row = Project(user_id=user.user_id, name="Recipe Analysis",
                      repo_url="https://github.com/u/recipe")
        s.add(row); s.commit(); s.refresh(row)
        pid = str(row.project_id)

    updated = services.update_project(user.user_id, pid, {"description": "New blurb."})
    assert updated["description"] == "New blurb."
    assert services.delete_project(user.user_id, pid) is True

    agent = _make_parser(isolated_engine, monkeypatch, user)
    # Same repo_url — the strongest match signal — must still be tombstone-blocked.
    agent._save_projects(
        [{"name": "Totally Different Name",
          "repo_url": "https://github.com/u/recipe"}], "github"
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert rows == []
