"""
Tests for ingestion hygiene (issue #72, PR 3).

Covers the parser save-path date coercion + fuzzy dedup and the self-heal that
merges pre-existing duplicate experience/project rows.
"""
from types import SimpleNamespace

from sqlmodel import Session, select

import agents.parser as parser_module
from agents.parser import ResumeParserAgent, _clean_date
from conftest import _seed_user_and_skill
from database.models import Experience, Project


def _agent(isolated_engine, monkeypatch, user):
    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    agent = ResumeParserAgent.__new__(ResumeParserAgent)
    agent.user = SimpleNamespace(user_id=user.user_id)
    return agent


# ── _clean_date ───────────────────────────────────────────────────────────────

def test_clean_date_coerces_placeholders():
    assert _clean_date("Not specified") is None
    assert _clean_date("unknown") is None
    assert _clean_date("") is None
    assert _clean_date("  ") is None
    # Real dates pass through untouched.
    assert _clean_date("2024-06") == "2024-06"
    assert _clean_date("Present") == "Present"


# ── _save_experiences ─────────────────────────────────────────────────────────

def test_save_experiences_coerces_placeholder_dates(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    agent._save_experiences(
        [{"title": "Analyst", "company": "Acme", "start_date": "Not specified",
          "end_date": "Present", "bullets": ["b1"]}],
        "resume",
    )
    with Session(isolated_engine) as s:
        row = s.exec(select(Experience).where(Experience.title == "Analyst")).first()
        assert row.start_date is None
        assert row.end_date == "Present"


def test_save_experiences_fuzzy_merges_instead_of_duplicating(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    # First ingest: the rich row.
    agent._save_experiences(
        [{"title": "Data Science Intern", "company": "IDX Exchange",
          "start_date": "2026-01", "end_date": "Present",
          "bullets": ["b1", "b2", "b3"]}],
        "resume",
    )
    # Second ingest: a sparse near-duplicate with a spacing difference in company.
    agent._save_experiences(
        [{"title": "Data Science Intern", "company": "IDXExchange",
          "start_date": "Not specified", "end_date": "Present", "bullets": []}],
        "resume",
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        assert len(rows) == 1  # merged, not duplicated
        assert len(rows[0].bullets) == 3


def test_save_experiences_enriches_missing_fields_on_merge(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    agent._save_experiences(
        [{"title": "Engineer", "company": "Acme", "start_date": None,
          "end_date": None, "bullets": []}],
        "resume",
    )
    # Later source supplies the dates + bullets the first lacked.
    agent._save_experiences(
        [{"title": "Engineer", "company": "Acme", "start_date": "2022-01",
          "end_date": "2024-06", "bullets": ["did a thing"]}],
        "resume",
    )
    with Session(isolated_engine) as s:
        row = s.exec(select(Experience).where(Experience.title == "Engineer")).first()
        assert row.start_date == "2022-01"
        assert row.bullets == ["did a thing"]


# ── _save_projects ────────────────────────────────────────────────────────────

def test_save_projects_fuzzy_merges_punctuation_variants(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    agent._save_projects(
        [{"name": "Price Prediction (Stacked Ensemble Model)", "description": "full desc"}],
        "resume",
    )
    agent._save_projects(
        [{"name": "Price Prediction(Stacked Ensemble Model)", "description": ""}],
        "resume",
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
        assert len(rows) == 1


def test_save_projects_coerces_placeholder_dates(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    agent._save_projects(
        [{"name": "Solo", "description": "d", "start_date": "Not specified",
          "end_date": "unknown"}],
        "resume",
    )
    with Session(isolated_engine) as s:
        row = s.exec(select(Project).where(Project.name == "Solo")).first()
        assert row.start_date is None
        assert row.end_date is None


# ── self-heal ─────────────────────────────────────────────────────────────────

def test_heal_experiences_merges_existing_duplicates(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    # Seed the exact messy shape observed in the real DB: a rich row and a
    # 0-bullet placeholder-date near-duplicate.
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Data Science Intern",
                         company="IDX Exchange", start_date="2026-01",
                         end_date="Present", bullets=["b1", "b2", "b3"]))
        s.add(Experience(user_id=user.user_id, title="Data Science Intern",
                         company="IDXExchange", start_date="Not specified",
                         end_date="Present", bullets=[]))
        s.commit()

    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_experiences(s, user.user_id)
        s.commit()
    assert removed == 1
    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        assert len(rows) == 1
        assert len(rows[0].bullets) == 3  # the richer row survived


def test_heal_experiences_coerces_dates_and_is_idempotent(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Research Assistant",
                         company="UCSD", start_date="Not specified",
                         end_date="Present", bullets=["b1", "b2"]))
        s.commit()

    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_experiences(s, user.user_id)
        s.commit()
    assert removed == 0  # nothing to merge
    with Session(isolated_engine) as s:
        row = s.exec(select(Experience).where(Experience.title == "Research Assistant")).first()
        assert row.start_date is None  # placeholder coerced

    # Running again changes nothing (idempotent).
    with Session(isolated_engine) as s:
        assert ResumeParserAgent._heal_experiences(s, user.user_id) == 0
        s.commit()


def test_heal_projects_merges_and_backfills(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id, name="California Real Estate (Ensemble)",
                      description="A thorough writeup of the model.",
                      repo_url=None))
        s.add(Project(user_id=user.user_id, name="California Real Estate(Ensemble)",
                      description="", repo_url="https://github.com/x/y"))
        s.commit()

    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_projects(s, user.user_id)
        s.commit()
    assert removed == 1
    with Session(isolated_engine) as s:
        rows = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
        assert len(rows) == 1
        # Survivor keeps the real description and backfills the repo_url.
        assert rows[0].description == "A thorough writeup of the model."
        assert rows[0].repo_url == "https://github.com/x/y"


def test_heal_keeps_distinct_rows(isolated_engine, monkeypatch):
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Data Scientist", company="IDX",
                         bullets=["b1"]))
        s.add(Experience(user_id=user.user_id, title="Barista", company="Cafe",
                         bullets=["b2"]))
        s.commit()
    with Session(isolated_engine) as s:
        assert ResumeParserAgent._heal_experiences(s, user.user_id) == 0
        s.commit()
    with Session(isolated_engine) as s:
        assert len(s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()) == 2


def test_clean_date_coerces_question_mark():
    # The malformed 'Unknown Position' row carried a literal '?' start date.
    assert _clean_date("?") is None


def test_heal_experiences_merges_placeholder_title_duplicate(isolated_engine, monkeypatch):
    """The real bug: a junk 'Unknown Position @ IDXExchange / ?' row shares only
    the company with the real 'Data Science Intern @ IDX Exchange' row. It must
    fold into the real one, keeping the real title and dropping the '?' date."""
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Data Science Intern",
                         company="IDX Exchange", start_date="2026-01",
                         end_date="Present", bullets=["b1", "b2"]))
        s.add(Experience(user_id=user.user_id, title="Unknown Position",
                         company="IDXExchange", start_date="?",
                         end_date="Present", bullets=[]))
        s.commit()

    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_experiences(s, user.user_id)
        s.commit()
    assert removed == 1
    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        assert len(rows) == 1
        assert rows[0].title == "Data Science Intern"  # real title survived
        assert rows[0].start_date == "2026-01"


def test_save_experiences_placeholder_title_merges_on_company(isolated_engine, monkeypatch):
    """A later placeholder-title record enriches the real row at save time."""
    user = _seed_user_and_skill(isolated_engine)
    agent = _agent(isolated_engine, monkeypatch, user)
    agent._save_experiences(
        [{"title": "Data Science Intern", "company": "IDX Exchange",
          "start_date": "2026-01", "end_date": "Present", "bullets": ["b1"]}],
        "resume",
    )
    agent._save_experiences(
        [{"title": "Unknown Position", "company": "IDXExchange",
          "start_date": "?", "end_date": "Present", "bullets": []}],
        "resume",
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        assert len(rows) == 1
        assert rows[0].title == "Data Science Intern"


def test_heal_projects_merges_on_repo_url_across_sources(isolated_engine, monkeypatch):
    """A GitHub-ingested repo and its resume line have divergent names but the
    same repo_url; they must merge on the URL even though names don't fuzzy-match."""
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id, name="recipe-review-analysis",
                      description="", repo_url="https://github.com/me/recipe-review-analysis"))
        s.add(Project(user_id=user.user_id, name="Sentiment-Driven Recipe Recommender",
                      description="A rich resume writeup.",
                      repo_url="https://github.com/me/recipe-review-analysis/"))
        s.commit()

    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_projects(s, user.user_id)
        s.commit()
    assert removed == 1
    with Session(isolated_engine) as s:
        rows = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
        assert len(rows) == 1
        assert rows[0].description == "A rich resume writeup."
