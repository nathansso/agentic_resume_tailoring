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
    selected = ResumeTailorAgent._score_and_select_projects(projects, jd_text)

    # Only ML Pipeline matches the JD; the min-k floor keeps a second project.
    assert len(selected) >= 2
    # ML Pipeline should rank first
    assert selected[0]["name"] == "ML Pipeline"
    assert selected[0]["selection_score"] > selected[1]["selection_score"]


def test_score_and_select_projects_max_cap():
    # Ten equally-scored projects fill up to the dynamic MAX_PROJECTS clamp.
    from agents.project_scorer import MAX_PROJECTS
    projects = [{"name": f"Project {i}", "description": "python", "blurbs": {}} for i in range(10)]
    selected = ResumeTailorAgent._score_and_select_projects(projects, "python developer")
    assert len(selected) == MAX_PROJECTS


def test_score_and_select_projects_empty():
    assert ResumeTailorAgent._score_and_select_projects([], "python") == []


def test_score_and_select_projects_no_jd():
    projects = [{"name": "My Project", "description": "cool stuff", "blurbs": {}}]
    # Empty JD → falls back to the first MAX_PROJECTS with no scoring
    selected = ResumeTailorAgent._score_and_select_projects(projects, "")
    assert len(selected) == 1


# ── Unit tests: score_tailored / flatten_tailored_text (issue 12) ────────────

_TAILORED = {
    "experiences": [
        {
            "title": "Senior Software Engineer",
            "company": "Tech Corp",
            "bullets": ["Built python services with fastapi", "Deployed postgresql databases"],
        },
    ],
    "projects": [
        {
            "name": "ML Pipeline",
            "selected_style": "technical",
            "bullets": ["Trained tensorflow models on large datasets"],
        },
    ],
    "skills_emphasized": ["Python", "FastAPI"],
}

_JD = "Looking for senior python engineer with fastapi postgresql tensorflow experience."


def test_flatten_tailored_text():
    text = ATSScoringEngine.flatten_tailored_text(_TAILORED)
    assert "Senior Software Engineer at Tech Corp" in text
    assert "Built python services with fastapi" in text
    assert "ML Pipeline" in text
    assert "Trained tensorflow models on large datasets" in text
    assert "Skills: Python, FastAPI" in text


def test_flatten_tailored_text_empty():
    assert ATSScoringEngine.flatten_tailored_text({}) == ""


def test_score_tailored_structure():
    bd = ATSScoringEngine.score_tailored(
        _TAILORED, _JD, matched_skills={"Python": {}, "FastAPI": {}},
    )
    for key in ("composite", "skill_coverage", "keyword_coverage", "section_presence", "role_level"):
        assert key in bd
    assert 0 <= bd["composite"] <= 100
    # Both matched skills appear in the tailored text
    assert bd["skill_coverage"]["score"] == 100.0
    assert bd["skill_coverage"]["gaps"] == []
    # No baseline supplied → no delta keys
    assert "delta" not in bd
    assert "baseline_composite" not in bd


def test_score_tailored_delta_vs_baseline():
    baseline = {"composite": 50.0}
    bd = ATSScoringEngine.score_tailored(
        _TAILORED, _JD, matched_skills={"Python": {}}, baseline_breakdown=baseline,
    )
    assert bd["baseline_composite"] == 50.0
    assert bd["delta"] == pytest.approx(round(bd["composite"] - 50.0, 1))


def test_score_tailored_skill_gaps():
    bd = ATSScoringEngine.score_tailored(
        _TAILORED, _JD, matched_skills={"Python": {}, "Kubernetes": {}},
    )
    assert bd["skill_coverage"]["score"] == pytest.approx(50.0)
    assert bd["skill_coverage"]["gaps"] == ["Kubernetes"]


def test_score_tailored_missing_section():
    content = {"experiences": _TAILORED["experiences"], "projects": []}
    bd = ATSScoringEngine.score_tailored(content, _JD, matched_skills={})
    sp = bd["section_presence"]
    assert sp["score"] == pytest.approx(50.0)
    assert "projects" in sp["missing"]
    assert "experiences" in sp["present"]


def test_evaluate_node_attaches_algorithmic_breakdown(monkeypatch):
    import agents.tailor as tailor_module

    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())
    agent = tailor_module.ResumeTailorAgent()

    state = {
        "user_id": "u", "job_id": "j", "result_id": "r",
        "resume_text": "", "job_text": _JD,
        "matched_skills": {"Python": {}, "FastAPI": {}},
        "missing_skills": [],
        "priority_keywords": ["tensorflow", "postgresql"],
        "baseline_breakdown": {"composite": 40.0},
        "experiences": [], "projects": [],
        "tailored_content": _TAILORED,
        "evaluation": {},
        "best_content": {}, "best_evaluation": {}, "best_score": -1.0,
        "attempt": 2, "done": False,
    }
    out = agent._evaluate_node(state)
    ev = out["evaluation"]

    assert "ats_breakdown" in ev
    assert ev["ats_breakdown"]["baseline_composite"] == 40.0
    assert "delta" in ev["ats_breakdown"]
    # Legacy evaluation keys preserved for retry feedback
    assert ev["coverage_pct"] == 100.0
    assert ev["kw_coverage"] == 100.0  # both priority keywords present
    assert out["done"] is True


def test_tailored_score_breakdown_roundtrip(isolated_engine):
    from database.models import JobDescription, UserJobResult
    from conftest import _seed_user_and_skill

    ctx = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="Engineer", company="Acme", description="python", user_id=ctx.user_id)
        session.add(job)
        session.commit()
        session.refresh(job)

        result = UserJobResult(
            user_id=ctx.user_id,
            job_id=job.job_id,
            tailored_score_breakdown={"composite": 55.0, "delta": 5.0},
        )
        session.add(result)
        session.commit()
        result_id = result.result_id

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, result_id)
        assert stored.tailored_score_breakdown == {"composite": 55.0, "delta": 5.0}


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
