"""Tests for Bright Data LinkedIn ingestion (issue #13).

Covers:
  1. ingest_brightdata happy path (mocked synchronous /scrape call)
  2. ingest_brightdata requires the platform API key
  3. _brightdata_to_text flattening of a structured record
  4. services.ingest_linkedin records done/failed lifecycle on the user row
  5. profile update auto-schedules a background ingest when the URL changes
  6. POST /api/ingest/linkedin endpoint returns the service result
"""
import asyncio
import json

import pytest
import requests
from sqlmodel import Session, select

import ingestion.linkedin as linkedin_module
from ingestion.linkedin import LinkedInIngestor, LinkedInIngestionError
from database.models import User


_RECORD = {
    "name": "Jane Dev",
    "city": "Seattle",
    "country_code": "US",
    "position": "ML Engineer",
    "about": "I build models.",
    "current_company": {"name": "Acme"},
    "experience": [
        {"title": "ML Engineer", "company": "Acme",
         "start_date": "2020", "end_date": "2024", "description": "Built stuff"},
    ],
    "education": [{"title": "UCSD", "degree": "BS", "field": "CS"}],
    "skills": ["Python", "PyTorch"],
    "projects": [
        {"title": "Recipe Review Analysis - Classification Model",
         "start_date": "Nov 2024",
         "description": "Random Forest pipeline on Food.com reviews."},
    ],
    "courses": [{"subtitle": "DSC 106", "title": "Data Visualization"}],
    "honors_and_awards": [{"title": "Dean's List", "date": "2023"}],
    "bio_links": [{"title": "Portfolio", "link": "https://example.dev"}],
}


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _install_fake_http(monkeypatch, record=_RECORD):
    """Patch requests.post on the linkedin module to simulate Bright Data /scrape."""
    def fake_post(url, **kwargs):
        assert "/scrape" in url, f"unexpected URL: {url}"
        return _FakeResp([record])

    monkeypatch.setattr(linkedin_module.requests, "post", fake_post)
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", "test-key")


# ── ingest_brightdata ──────────────────────────────────────────────────────────

def test_ingest_brightdata_happy_path(monkeypatch):
    _install_fake_http(monkeypatch)
    result = LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/janedev")

    assert result["source_type"] == "linkedin"
    assert result["source_file"] == "linkedin:https://www.linkedin.com/in/janedev"
    text = result["full_text"]
    assert "Jane Dev" in text
    assert "ML Engineer" in text
    assert "Experience" in text
    assert "Python" in text


def test_ingest_brightdata_normalizes_username(monkeypatch):
    _install_fake_http(monkeypatch)
    result = LinkedInIngestor().ingest_brightdata("janedev")
    assert result["source_file"] == "linkedin:https://www.linkedin.com/in/janedev"


def test_ingest_brightdata_requires_key(monkeypatch):
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", None)
    with pytest.raises(LinkedInIngestionError) as exc:
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")
    assert "not configured" in str(exc.value)


def test_ingest_brightdata_error_record(monkeypatch):
    _install_fake_http(monkeypatch, record={"error": "profile is private"})
    with pytest.raises(LinkedInIngestionError):
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")


def test_ingest_brightdata_request_failure(monkeypatch):
    def boom(url, **kwargs):
        raise requests.RequestException("connection refused")
    monkeypatch.setattr(linkedin_module.requests, "post", boom)
    monkeypatch.setattr(linkedin_module, "BRIGHTDATA_API_KEY", "test-key")
    with pytest.raises(LinkedInIngestionError):
        LinkedInIngestor().ingest_brightdata("https://www.linkedin.com/in/x")


# ── _brightdata_to_text ─────────────────────────────────────────────────────────

def test_brightdata_to_text_flattening():
    text = LinkedInIngestor()._brightdata_to_text(_RECORD, "https://x")
    assert "Name: Jane Dev" in text
    assert "Headline: ML Engineer" in text
    assert "Current company: Acme" in text
    assert "Education:" in text
    assert "UCSD" in text


def test_brightdata_to_text_is_lossless():
    """Projects, courses, honors, and bio links must survive flattening —
    they feed LLM skill extraction (issue #68 follow-up)."""
    text = LinkedInIngestor()._brightdata_to_text(_RECORD, "https://x")
    assert "Projects:" in text
    assert "Recipe Review Analysis" in text
    assert "Random Forest pipeline" in text
    assert "Courses:" in text
    assert "Data Visualization" in text
    assert "Honors and awards:" in text
    assert "Dean's List" in text
    assert "https://example.dev" in text


def test_ingest_brightdata_returns_structured_record(monkeypatch):
    _install_fake_http(monkeypatch)
    result = LinkedInIngestor().ingest_brightdata("janedev")
    assert result["linkedin_record"]["name"] == "Jane Dev"


# ── Deterministic structured mapping (parser) ───────────────────────────────────

def _make_parser(isolated_engine, monkeypatch, user):
    """ResumeParserAgent wired to the test DB without touching the LLM or the
    default-user machinery (__init__ bypassed — structured saves need neither)."""
    import agents.parser as parser_module
    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user = user
    agent.llm = None
    return agent


def _make_user(isolated_engine, email):
    with Session(isolated_engine) as s:
        user = User(name="U", email=email)
        s.add(user); s.commit(); s.refresh(user)
        return user


def test_structured_projects_saved_verbatim(isolated_engine, monkeypatch):
    from database.models import Project
    from sqlmodel import select
    user = _make_user(isolated_engine, "sv1@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_RECORD)

    with Session(isolated_engine) as s:
        projs = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert len(projs) == 1
    assert projs[0].name == "Recipe Review Analysis - Classification Model"
    assert projs[0].description == "Random Forest pipeline on Food.com reviews."
    assert projs[0].start_date == "Nov 2024"


def test_structured_achievements_saved_and_merge_with_resume(isolated_engine, monkeypatch):
    """LinkedIn honors_and_awards become Achievement rows, folding into a
    resume-ingested achievement instead of duplicating it (cross-source dedup)."""
    from database.models import Achievement
    from sqlmodel import select
    user = _make_user(isolated_engine, "ach1@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    # Resume already had a bare "Deans List" (no issuer/date).
    with Session(isolated_engine) as s:
        s.add(Achievement(user_id=user.user_id, title="Deans List"))
        s.commit()

    agent._save_linkedin_structured(_RECORD)  # honors_and_awards: Dean's List, 2023

    with Session(isolated_engine) as s:
        rows = s.exec(select(Achievement).where(Achievement.user_id == user.user_id)).all()
    assert len(rows) == 1  # merged, not duplicated
    assert rows[0].date == "2023"  # blank backfilled from LinkedIn


def test_structured_project_merges_with_similar_existing(isolated_engine, monkeypatch):
    """A resume-ingested project with a shorter variant of the name is enriched,
    not duplicated."""
    from database.models import Project
    from sqlmodel import select
    user = _make_user(isolated_engine, "sv2@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id, name="Recipe Review Analysis"))
        s.commit()

    agent._save_linkedin_structured(_RECORD)

    with Session(isolated_engine) as s:
        projs = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert len(projs) == 1
    assert projs[0].name == "Recipe Review Analysis"  # original name kept
    assert projs[0].description == "Random Forest pipeline on Food.com reviews."
    assert projs[0].start_date == "Nov 2024"


def test_structured_merge_appends_new_description_idempotently(isolated_engine, monkeypatch):
    from database.models import Project
    from sqlmodel import select
    user = _make_user(isolated_engine, "sv3@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id,
                      name="Recipe Review Analysis",
                      description="Resume version of the description."))
        s.commit()

    agent._save_linkedin_structured(_RECORD)
    agent._save_linkedin_structured(_RECORD)  # re-ingest must not re-append

    with Session(isolated_engine) as s:
        projs = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert len(projs) == 1
    desc = projs[0].description
    assert desc.startswith("Resume version of the description.")
    assert desc.count("[LinkedIn] Random Forest pipeline") == 1


def test_structured_experience_merges_company_variants(isolated_engine, monkeypatch):
    """'IDXExchange' (LinkedIn) merges into 'IDX Exchange' (resume); a
    placeholder LinkedIn title doesn't block the merge."""
    from database.models import Experience
    from sqlmodel import select
    user = _make_user(isolated_engine, "sv4@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Data Science Intern",
                         company="IDX Exchange"))
        s.commit()

    record = {"experience": [
        {"title": "", "company": "IDXExchange",
         "start_date": "2026-01", "description": "Internship program."},
    ]}
    agent._save_linkedin_structured(record)

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(exps) == 1
    assert exps[0].title == "Data Science Intern"
    assert exps[0].start_date == "2026-01"
    assert exps[0].description == "Internship program."


def test_structured_experience_new_company_creates_row(isolated_engine, monkeypatch):
    from database.models import Experience
    from sqlmodel import select
    user = _make_user(isolated_engine, "sv5@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    record = {"experience": [
        {"title": "ML Engineer", "company": "Acme", "start_date": "2020"},
    ]}
    agent._save_linkedin_structured(record)

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(exps) == 1
    assert exps[0].title == "ML Engineer" and exps[0].company == "Acme"


# ── nested positions traversal + bullets (issue #96) ────────────────────────────

from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name):
    return json.loads((_FIXTURES / name).read_text())


def test_nested_positions_yield_one_row_per_role(isolated_engine, monkeypatch):
    """A Bright Data employer with roles nested under `positions` produces one
    Experience per role, not just the first (issue #96)."""
    from database.models import Experience
    user = _make_user(isolated_engine, "np1@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_load_fixture("linkedin_nested_positions.json"))

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    titles = {e.title for e in exps}
    # Both UCSD roles + the flat single-role employer.
    assert titles == {"Research Assistant", "Financial Assistant", "Data Science Intern"}
    ucsd = [e for e in exps if e.company == "UC San Diego"]
    assert len(ucsd) == 2  # both nested roles kept, company backfilled from parent


def test_nested_positions_capture_bullets(isolated_engine, monkeypatch):
    """A multi-line role description becomes bullets so the role isn't a
    content-empty stub the tailor would drop (issue #96)."""
    from database.models import Experience
    user = _make_user(isolated_engine, "np2@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_load_fixture("linkedin_nested_positions.json"))

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    ra = next(e for e in exps if e.title == "Research Assistant")
    assert len(ra.bullets) == 2  # two-line description split into bullets
    fa = next(e for e in exps if e.title == "Financial Assistant")
    assert fa.bullets == []       # single-line description stays as description
    assert fa.description


def test_nested_roles_survive_tailor_filter(isolated_engine, monkeypatch):
    """End-to-end: both UCSD roles survive the tailor's dedupe/empty-stub filter
    (issue #96 acceptance)."""
    from database.models import Experience
    from agents.tailor import ResumeTailorAgent
    user = _make_user(isolated_engine, "np3@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_load_fixture("linkedin_nested_positions.json"))

    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
        exp_dicts = [
            {"title": e.title, "company": e.company, "start_date": e.start_date,
             "end_date": e.end_date, "description": e.description, "bullets": e.bullets}
            for e in rows
        ]

    kept = ResumeTailorAgent._filter_and_dedupe_experiences(exp_dicts)
    kept_titles = {e["title"] for e in kept}
    assert "Research Assistant" in kept_titles
    assert "Financial Assistant" in kept_titles


def test_single_role_employer_unchanged(isolated_engine, monkeypatch):
    """A flat experience record (no `positions`) still maps to exactly one row —
    no regression to single-role employers (issue #96)."""
    from database.models import Experience
    user = _make_user(isolated_engine, "np4@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    record = {"experience": [
        {"title": "ML Engineer", "company": "Acme", "start_date": "2020",
         "description": "Owned the ranking service."},
    ]}
    agent._save_linkedin_structured(record)

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(exps) == 1
    assert exps[0].title == "ML Engineer" and exps[0].company == "Acme"


def test_flatten_linkedin_experiences_backfills_company():
    """Unit: nested positions inherit the parent company; flat records pass through."""
    import agents.parser as parser_module
    flat = parser_module.ResumeParserAgent._flatten_linkedin_experiences([
        {"company": "UC San Diego", "positions": [
            {"title": "Research Assistant"}, {"title": "Financial Assistant"},
        ]},
        {"title": "Intern", "company": "Acme"},
    ])
    assert len(flat) == 3
    assert all(r["company"] == "UC San Diego" for r in flat[:2])
    assert flat[2] == {"title": "Intern", "company": "Acme"}


def test_parse_and_save_linkedin_skips_llm_entity_extraction(isolated_engine, monkeypatch):
    """With a structured record present, the experience/project LLM chains must
    not run; skills extraction still does."""
    from database.models import Project
    from sqlmodel import select
    import agents.parser as parser_module

    user = _make_user(isolated_engine, "sv6@example.com")
    agent = _make_parser(isolated_engine, monkeypatch, user)

    def _llm_must_not_run(self, text):
        raise AssertionError("entity LLM extraction should be bypassed for LinkedIn")
    monkeypatch.setattr(parser_module.ResumeParserAgent, "_extract_experiences", _llm_must_not_run)
    monkeypatch.setattr(parser_module.ResumeParserAgent, "_extract_projects", _llm_must_not_run)
    skills_ran = {"n": 0}
    monkeypatch.setattr(
        parser_module.ResumeParserAgent, "_extract_skills",
        lambda self, text: skills_ran.__setitem__("n", skills_ran["n"] + 1) or [],
    )

    agent.parse_and_save({
        "source_type": "linkedin",
        "source_file": "linkedin:https://www.linkedin.com/in/janedev",
        "full_text": "flattened text",
        "linkedin_record": _RECORD,
    })

    assert skills_ran["n"] == 1
    with Session(isolated_engine) as s:
        projs = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert len(projs) == 1


# ── services.ingest_linkedin lifecycle ──────────────────────────────────────────

def test_service_ingest_linkedin_marks_done(isolated_engine, monkeypatch):
    import services as services
    with Session(isolated_engine) as s:
        user = User(name="U", email="u@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    monkeypatch.setattr(
        LinkedInIngestor, "ingest_brightdata",
        lambda self, url: {"source_type": "linkedin",
                           "source_file": f"linkedin:{url}", "full_text": "x"},
    )
    monkeypatch.setattr(
        "agents.parser.ResumeParserAgent.parse_and_save", lambda self, data: None
    )

    services.ingest_linkedin("https://www.linkedin.com/in/janedev", uid)

    with Session(isolated_engine) as s:
        u = s.get(User, uid)
        assert u.linkedin_ingest_status == "done"
        assert u.linkedin_ingested_url == "https://www.linkedin.com/in/janedev"
        assert u.linkedin_ingested_at is not None


def test_service_ingest_linkedin_marks_failed(isolated_engine, monkeypatch):
    import services as services
    with Session(isolated_engine) as s:
        user = User(name="U", email="u2@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    def _boom(self, url):
        raise LinkedInIngestionError("nope")
    monkeypatch.setattr(LinkedInIngestor, "ingest_brightdata", _boom)

    result = services.ingest_linkedin("https://www.linkedin.com/in/x", uid)
    assert "failed" in result.lower()
    with Session(isolated_engine) as s:
        u = s.get(User, uid)
        assert u.linkedin_ingest_status == "failed"
        assert u.linkedin_ingest_error == "nope"


# ── raw scrape persistence + replay (issue #69) ─────────────────────────────────

def test_ingest_linkedin_persists_raw_record(isolated_engine, monkeypatch):
    """A successful scrape stores its raw Bright Data record on the user row so
    it can be replayed later without a new (paid) scrape."""
    import services

    with Session(isolated_engine) as s:
        user = User(name="U", email="raw1@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    record = {"name": "Jane", "experience": [{"title": "X", "company": "Y"}]}
    monkeypatch.setattr(
        LinkedInIngestor, "ingest_brightdata",
        lambda self, url: {"source_type": "linkedin",
                           "source_file": f"linkedin:{url}", "full_text": "x",
                           "linkedin_record": record},
    )
    monkeypatch.setattr(
        "agents.parser.ResumeParserAgent.parse_and_save", lambda self, data: None
    )

    services.ingest_linkedin("https://www.linkedin.com/in/janedev", uid)

    with Session(isolated_engine) as s:
        u = s.get(User, uid)
        assert u.linkedin_raw_record
        assert json.loads(u.linkedin_raw_record)["name"] == "Jane"


def test_replay_linkedin_uses_stored_record_without_scrape(isolated_engine, monkeypatch):
    """Replay re-runs the structured mapping against the stored raw record and
    never triggers a new Bright Data scrape."""
    import services
    import agents.parser as parser_module
    from database.models import Experience

    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    monkeypatch.setattr(parser_module, "get_llm", lambda **kw: None)
    monkeypatch.setattr(
        parser_module.ResumeParserAgent, "_extract_skills", lambda self, text: []
    )

    record = {"experience": [
        {"title": "ML Engineer", "company": "Acme",
         "start_date": "2020", "description": "Built models."},
    ]}
    with Session(isolated_engine) as s:
        user = User(name="U", email="replay1@example.com",
                    linkedin_ingested_url="https://www.linkedin.com/in/janedev",
                    linkedin_raw_record=json.dumps(record))
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    def _no_scrape(self, url):
        raise AssertionError("replay must not call Bright Data")
    monkeypatch.setattr(LinkedInIngestor, "ingest_brightdata", _no_scrape)

    services.replay_linkedin(uid)

    with Session(isolated_engine) as s:
        exps = s.exec(select(Experience).where(Experience.user_id == uid)).all()
    assert len(exps) == 1
    assert exps[0].title == "ML Engineer" and exps[0].company == "Acme"


def test_replay_linkedin_no_stored_record(isolated_engine, monkeypatch):
    import services

    with Session(isolated_engine) as s:
        user = User(name="U", email="replay2@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    result = services.replay_linkedin(uid)
    assert "No stored LinkedIn scrape" in result


# ── profile update auto-trigger ─────────────────────────────────────────────────

def test_profile_update_schedules_ingest(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u3@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", "test-key")
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 1


def test_profile_update_no_trigger_when_unchanged(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u4@example.com",
                    linkedin_ingested_url="https://www.linkedin.com/in/janedev")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", "test-key")
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev/")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 0


def test_profile_update_no_trigger_without_key(isolated_engine, monkeypatch):
    from fastapi import BackgroundTasks
    import web.routers.profile_router as pr

    with Session(isolated_engine) as s:
        user = User(name="U", email="u5@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(pr, "BRIGHTDATA_API_KEY", None)
    bg = BackgroundTasks()
    body = pr.UpdateProfileBody(linkedin_url="https://www.linkedin.com/in/janedev")
    pr.update_profile(body, bg, user=user)

    assert len(bg.tasks) == 0


# ── sign-in auto-refresh ────────────────────────────────────────────────────────

def _login_user(isolated_engine, monkeypatch, **fields):
    """Build a user and a BackgroundTasks, with auth_router pointed at the test DB."""
    from fastapi import BackgroundTasks
    import web.routers.auth_router as ar
    import web.routers.dependencies as deps

    monkeypatch.setattr(ar, "BRIGHTDATA_API_KEY", "test-key")
    monkeypatch.setattr(ar, "engine", isolated_engine)
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")

    with Session(isolated_engine) as s:
        user = User(name="U", **fields)
        s.add(user); s.commit(); s.refresh(user)
    return ar, BackgroundTasks(), user


def test_login_refresh_when_stale(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login1@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 1


def test_login_no_refresh_when_fresh_today(isolated_engine, monkeypatch):
    from datetime import datetime, timezone
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login2@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
        linkedin_ingested_at=datetime.now(timezone.utc),
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_without_url(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch, email="login3@example.com",
    )
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_without_key(isolated_engine, monkeypatch):
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login4@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    monkeypatch.setattr(ar, "BRIGHTDATA_API_KEY", None)
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


def test_login_no_refresh_when_quota_exhausted(isolated_engine, monkeypatch):
    import web.routers.dependencies as deps
    ar, bg, user = _login_user(
        isolated_engine, monkeypatch,
        email="login5@example.com",
        linkedin_url="https://www.linkedin.com/in/janedev",
    )
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    for _ in range(2):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(user.user_id, s)
    ar._maybe_refresh_linkedin_on_login(bg, user)
    assert len(bg.tasks) == 0


# ── ingest endpoint ─────────────────────────────────────────────────────────────

def test_ingest_linkedin_endpoint(isolated_engine, monkeypatch):
    import web.routers.ingest_router as ir

    with Session(isolated_engine) as s:
        user = User(name="U", email="u6@example.com")
        s.add(user); s.commit(); s.refresh(user)

    monkeypatch.setattr(ir.services, "ingest_linkedin",
                        lambda url, user_id: "LinkedIn profile ingested.")

    body = ir.LinkedInBody(url="https://www.linkedin.com/in/janedev")
    with Session(isolated_engine) as s:
        out = asyncio.run(ir.ingest_linkedin(body, user=user, _=None, session=s))
    assert out == {"result": "LinkedIn profile ingested."}
    # The attempt was counted against the LinkedIn quota.
    with Session(isolated_engine) as s:
        from database.models import AIUsage
        rows = s.exec(
            __import__("sqlmodel").select(AIUsage).where(AIUsage.kind == "linkedin")
        ).all()
    assert len(rows) == 1 and rows[0].call_count == 1


def test_ingest_linkedin_endpoint_blocks_over_limit(isolated_engine, monkeypatch):
    """The manual endpoint's quota dependency raises 429 once the cap is hit."""
    from fastapi import HTTPException
    import web.routers.dependencies as deps

    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 2)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    with Session(isolated_engine) as s:
        user = User(name="U", email="u7@example.com")
        s.add(user); s.commit(); s.refresh(user)

    for _ in range(2):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(user.user_id, s)

    async def _run():
        with Session(isolated_engine) as s:
            with pytest.raises(HTTPException) as exc:
                await deps.check_linkedin_quota(user=user, session=s)
        assert exc.value.status_code == 429
    asyncio.run(_run())


def test_linkedin_quota_separate_from_ai(isolated_engine, monkeypatch):
    """LinkedIn and AI usage are counted independently (kind discriminator)."""
    import web.routers.dependencies as deps
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    with Session(isolated_engine) as s:
        user = User(name="U", email="u8@example.com")
        s.add(user); s.commit(); s.refresh(user)

    with Session(isolated_engine) as s:
        deps.increment_ai_usage(user.user_id, s)
        deps.increment_ai_usage(user.user_id, s)
        deps.increment_linkedin_usage(user.user_id, s)

    with Session(isolated_engine) as s:
        assert deps._usage_row(s, user.user_id, "ai").call_count == 2
        assert deps._usage_row(s, user.user_id, "linkedin").call_count == 1


def test_linkedin_owner_exempt(isolated_engine, monkeypatch):
    import web.routers.dependencies as deps
    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 1)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "owner@example.com")
    with Session(isolated_engine) as s:
        owner = User(name="O", email="owner@example.com")
        s.add(owner); s.commit(); s.refresh(owner)

    for _ in range(5):
        with Session(isolated_engine) as s:
            deps.increment_linkedin_usage(owner.user_id, s)

    with Session(isolated_engine) as s:
        assert deps.linkedin_quota_remaining(s, owner.user_id, owner.email) is True


def test_profile_auto_trigger_skips_when_quota_exhausted(isolated_engine, monkeypatch):
    """The background auto-trigger self-guards against the LinkedIn cap."""
    import web.routers.profile_router as pr
    import web.routers.dependencies as deps

    monkeypatch.setattr(deps, "LINKEDIN_DAILY_LIMIT", 1)
    monkeypatch.setattr(deps, "OWNER_EMAIL", "")
    monkeypatch.setattr(pr, "engine", isolated_engine)
    with Session(isolated_engine) as s:
        user = User(name="U", email="u9@example.com")
        s.add(user); s.commit(); s.refresh(user)
        uid = user.user_id

    with Session(isolated_engine) as s:
        deps.increment_linkedin_usage(uid, s)  # exhaust the cap

    called = {"n": 0}
    monkeypatch.setattr(pr.services, "ingest_linkedin",
                        lambda url, user_id: called.__setitem__("n", called["n"] + 1))

    pr._linkedin_ingest_task(uid, "https://www.linkedin.com/in/x", "u9@example.com")
    assert called["n"] == 0  # scrape skipped — over quota
