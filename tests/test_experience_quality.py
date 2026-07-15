"""Experience ingestion quality (issue #85).

Two behaviors:
  1. An 'essentially empty' experience stub is not auto-added to the graph.
  2. A stored-but-incomplete experience is surfaced with a `missing` list so the
     Data Explorer can flag it for the user to complete or delete.
"""
from sqlmodel import Session, select

import services
from database.models import User, Experience


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


def _exps(engine, uid):
    with Session(engine) as s:
        return s.exec(select(Experience).where(Experience.user_id == uid)).all()


# ── ingestion guard ──────────────────────────────────────────────────────────────

def test_linkedin_empty_company_grouping_not_added(isolated_engine, monkeypatch):
    """A company grouping with no role title, dates, or detail is a stub — skip it."""
    user = _make_user(isolated_engine, "q1@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured({"experience": [{"company": "SomeCo"}]})

    assert _exps(isolated_engine, user.user_id) == []


def test_linkedin_real_title_company_minimal_is_kept(isolated_engine, monkeypatch):
    """A legit minimal role (real title + company, no detail yet) is still kept —
    the user can flesh it out later."""
    user = _make_user(isolated_engine, "q2@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(
        {"experience": [{"title": "ML Engineer", "company": "Acme"}]}
    )

    rows = _exps(isolated_engine, user.user_id)
    assert len(rows) == 1 and rows[0].title == "ML Engineer"


def test_linkedin_content_without_title_is_kept(isolated_engine, monkeypatch):
    """A role with real content (a date) but a missing title is substantive —
    keep it (it becomes 'Unknown Position' the user can rename)."""
    user = _make_user(isolated_engine, "q3@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(
        {"experience": [{"company": "Acme", "start_date": "2020"}]}
    )

    assert len(_exps(isolated_engine, user.user_id)) == 1


def test_resume_empty_experience_not_added(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine, "q4@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_experiences([{"title": "", "company": ""}], "resume")

    assert _exps(isolated_engine, user.user_id) == []


# ── surfacing incompleteness ─────────────────────────────────────────────────────

def test_get_experiences_flags_incomplete(isolated_engine):
    user = _make_user(isolated_engine, "q5@example.com")
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Analyst", company="Acme"))  # no dates/details
        s.add(Experience(user_id=user.user_id, title="Engineer", company="Beta",
                         start_date="2020", end_date="2021", bullets=["shipped X"]))
        s.commit()

    rows = {r["title"]: r for r in services.get_experiences(user.user_id)}
    assert rows["Analyst"]["incomplete"] is True
    assert set(rows["Analyst"]["missing"]) == {"dates", "details"}
    assert rows["Engineer"]["incomplete"] is False
    assert rows["Engineer"]["missing"] == []
