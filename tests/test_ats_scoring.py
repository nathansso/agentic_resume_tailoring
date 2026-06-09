"""Tests for the ATSScoringEngine and keyword-driven tailoring helpers."""
import pytest
from uuid import uuid4
from sqlmodel import Session

from agents.ats_scorer import ATSScoringEngine
from agents.tailor import ResumeTailorAgent


# ── Unit tests: _extract_keywords ────────────────────────────────────────────

def test_extract_keywords_basic():
    kw = ATSScoringEngine._extract_keywords("Python machine learning engineer")
    assert "python" in kw
    assert "machine" in kw
    assert "learning" in kw
    assert "engineer" in kw


def test_extract_keywords_filters_stop_words():
    kw = ATSScoringEngine._extract_keywords("required experience years role position")
    assert "required" not in kw
    assert "experience" not in kw
    assert "years" not in kw
    assert "role" not in kw
    assert "position" not in kw


def test_extract_keywords_min_length():
    kw = ATSScoringEngine._extract_keywords("go to be at ml ai sql")
    # "go", "to", "be", "at", "ml", "ai" are all < 3 chars
    assert len([w for w in kw if len(w) < 3]) == 0


def test_extract_keywords_skips_pure_numbers():
    kw = ATSScoringEngine._extract_keywords("5 years 10 experience 123")
    assert "5" not in kw
    assert "10" not in kw
    assert "123" not in kw


def test_extract_keywords_hyphenated():
    kw = ATSScoringEngine._extract_keywords("machine-learning full-stack")
    # Hyphenated words are kept as-is
    assert "machine-learning" in kw or ("machine" in kw and "learning" in kw)


# ── Unit tests: _keyword_coverage ────────────────────────────────────────────

def test_keyword_coverage_full_match():
    result = ATSScoringEngine._keyword_coverage(
        resume_text="experienced python developer with machine learning background",
        jd_text="python machine learning developer",
    )
    assert result["score"] == 100.0
    assert result["missing_keywords"] == []


def test_keyword_coverage_partial_match():
    result = ATSScoringEngine._keyword_coverage(
        resume_text="python developer",
        jd_text="python machine learning developer",
    )
    # "python" and "developer" match; "machine" and "learning" don't
    assert 0 < result["score"] < 100
    assert "python" in result["matched_keywords"]
    assert "machine" in result["missing_keywords"] or "learning" in result["missing_keywords"]


def test_keyword_coverage_no_match():
    result = ATSScoringEngine._keyword_coverage(
        resume_text="marketing copywriter branding",
        jd_text="python machine learning tensorflow",
    )
    assert result["score"] < 50
    assert len(result["missing_keywords"]) > 0


def test_keyword_coverage_empty_jd():
    result = ATSScoringEngine._keyword_coverage(
        resume_text="python developer",
        jd_text="",
    )
    assert result["score"] == 0.0
    assert result["total"] == 0


# ── Unit tests: _role_level ───────────────────────────────────────────────────

def test_role_level_exact_match():
    result = ATSScoringEngine._role_level(
        resume_text="senior software engineer 6 years experience",
        jd_text="senior engineer 5+ years",
    )
    assert result["score"] == 100.0
    assert result["jd_level"] == "senior"
    assert result["resume_level"] == "senior"


def test_role_level_gap_penalty():
    result = ATSScoringEngine._role_level(
        resume_text="junior developer entry-level associate",
        jd_text="senior engineer 5+ years",
    )
    # junior (idx 1) vs senior (idx 3) = gap of 2 → penalty 50
    assert result["score"] == pytest.approx(50.0)
    assert result["jd_level"] == "senior"
    assert result["resume_level"] == "junior"


def test_role_level_defaults_to_mid():
    result = ATSScoringEngine._role_level(
        resume_text="software engineer",
        jd_text="software engineer",
    )
    # No level keywords → both default to "mid" → perfect match
    assert result["score"] == 100.0
    assert result["jd_level"] == "mid"
    assert result["resume_level"] == "mid"


# ── Unit tests: _section_presence ────────────────────────────────────────────

def test_section_presence_with_data(isolated_engine):
    from database.models import User, Skill, UserSkill, Experience
    from conftest import _seed_user_and_skill

    ctx = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        exp = Experience(
            user_id=ctx.user_id,
            title="Software Engineer",
            company="Acme",
            bullets=["Built APIs"],
        )
        session.add(exp)
        session.commit()

        result = ATSScoringEngine._section_presence(ctx.user_id, session)

    assert result["score"] == 100.0
    assert "skills" in result["present"]
    assert "experience" in result["present"]


def test_section_presence_missing_required(isolated_engine):
    from database.models import User

    with Session(isolated_engine) as session:
        user = User(name="Empty User", email="empty@test.com")
        session.add(user)
        session.commit()
        user_id = user.user_id

        result = ATSScoringEngine._section_presence(user_id, session)

    assert result["score"] == 0.0
    assert "skills" in result["missing"]
    assert "experience" in result["missing"]


# ── Unit tests: _score_and_select_projects ───────────────────────────────────

def test_score_and_select_projects_ranking():
    projects = [
        {"name": "Data Analytics Dashboard", "description": "pandas numpy visualization", "blurbs": {}},
        {"name": "Portfolio Website", "description": "html css javascript", "blurbs": {}},
        {"name": "ML Pipeline", "description": "python tensorflow scikit-learn machine learning", "blurbs": {}},
    ]
    jd_text = "machine learning python tensorflow data scientist"
    selected = ResumeTailorAgent._score_and_select_projects(projects, jd_text, max_projects=2)

    assert len(selected) == 2
    # ML Pipeline should rank first
    assert selected[0]["name"] == "ML Pipeline"
    assert selected[0]["keyword_score"] > selected[1]["keyword_score"]


def test_score_and_select_projects_max_cap():
    projects = [{"name": f"Project {i}", "description": "python", "blurbs": {}} for i in range(10)]
    selected = ResumeTailorAgent._score_and_select_projects(projects, "python developer", max_projects=4)
    assert len(selected) == 4


def test_score_and_select_projects_empty():
    assert ResumeTailorAgent._score_and_select_projects([], "python", max_projects=4) == []


def test_score_and_select_projects_no_jd():
    projects = [{"name": "My Project", "description": "cool stuff", "blurbs": {}}]
    # Empty JD → falls back to first max_projects with no scoring
    selected = ResumeTailorAgent._score_and_select_projects(projects, "", max_projects=4)
    assert len(selected) == 1


# ── Integration test: full ATSScoringEngine.score() ──────────────────────────

def test_ats_scorer_full_integration(isolated_engine, monkeypatch):
    from database.models import (
        User, Skill, UserSkill, Experience, JobDescription, JobSkill,
    )
    from conftest import _seed_user_and_skill
    import agents.ats_scorer as scorer_module
    import agents.matcher as matcher_module

    monkeypatch.setattr(scorer_module, "engine", isolated_engine, raising=False)
    monkeypatch.setattr(matcher_module, "engine", isolated_engine, raising=False)

    ctx = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        # Add experience
        exp = Experience(
            user_id=ctx.user_id,
            title="Senior Software Engineer",
            company="Tech Corp",
            description="Built python services using fastapi and postgresql",
            bullets=["Developed REST APIs", "Deployed to cloud"],
        )
        session.add(exp)

        # Add job
        job = JobDescription(
            title="Senior Python Engineer",
            company="Acme",
            description="Looking for senior python developer with fastapi postgresql experience. "
                        "Must have strong communication skills and 5+ years experience.",
            user_id=ctx.user_id,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        scorer = ATSScoringEngine()
        breakdown = scorer.score(ctx.user_id, job.job_id, session, skill_coverage_score=75.0)

    # Validate structure
    assert "composite" in breakdown
    assert "skill_coverage" in breakdown
    assert "keyword_coverage" in breakdown
    assert "section_presence" in breakdown
    assert "role_level" in breakdown

    # Composite is between 0 and 100
    assert 0 <= breakdown["composite"] <= 100

    # Keyword coverage should be > 0 (we have python, fastapi, etc.)
    kd = breakdown["keyword_coverage"]
    assert kd["score"] > 0
    assert len(kd["matched_keywords"]) > 0

    # Role level: JD says "5+ years" (senior), resume says "Senior" → match
    rl = breakdown["role_level"]
    assert rl["jd_level"] == "senior"
    assert rl["resume_level"] == "senior"
