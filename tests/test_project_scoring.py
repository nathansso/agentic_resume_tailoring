"""
Tests for composite project selection scoring (issue #46).

Covers agents/project_scorer.py (pure scoring), the tailor wiring
(_score_and_select_projects, _count_linked_skills), the Project.metrics
column roundtrip, and parser persistence of GitHub repo metrics.
"""
from types import SimpleNamespace

from sqlmodel import Session, select

import agents.project_scorer as project_scorer
from agents.project_scorer import (
    RELEVANCE_WEIGHT,
    COMPLEXITY_WEIGHT,
    LINKED_SKILLS_CAP,
    TEXT_RICHNESS_CAP,
    BLURB_VARIETY_CAP,
    STARS_CAP,
    LANGUAGES_CAP,
    README_LENGTH_CAP,
    score_project,
)
from agents.tailor import ResumeTailorAgent
from conftest import _seed_user_and_skill
from database.models import Project, User


JD_TEXT = (
    "We are seeking a machine learning engineer to build and deploy models "
    "with python and tensorflow. You will design scalable training pipelines, "
    "work with large datasets, and ship reliable services."
)

# Built-out project: long description, full blurb variety, many linked skills,
# GitHub signals — but only adjacent to the JD (no ML keywords).
RICH_PROJECT = {
    "name": "Distributed Pipeline Platform",
    "description": (
        "Designed and operated a distributed event-streaming platform processing "
        "millions of records daily with kafka, airflow orchestration, postgres "
        "storage, redis caching, docker containers, kubernetes deployment, "
        "terraform infrastructure, grafana observability dashboards, automated "
        "integration testing, and python services behind a fastapi gateway"
    ),
    "blurbs": {
        "concise": "Built a distributed streaming platform with kafka and python.",
        "detailed": "Operated airflow pipelines feeding postgres and redis at scale.",
        "metrics": "Cut pipeline latency 70% across millions of daily events.",
        "technical": "Kubernetes-deployed fastapi services with terraform and grafana.",
    },
    "linked_skills": 7,
    "metrics": {"stars": 40, "languages": ["Python", "Go", "HCL"], "readme_length": 3000},
}

# Thin project: strong exact JD keyword match but no depth at all.
THIN_PROJECT = {
    "name": "ML Demo",
    "description": "trained machine learning models with python and tensorflow on large datasets",
    "blurbs": {},
    "linked_skills": 0,
    "metrics": {},
}


# ── score_project: complexity ─────────────────────────────────────────────────

def test_complexity_rich_beats_thin():
    rich = score_project(RICH_PROJECT, JD_TEXT)
    thin = score_project(THIN_PROJECT, JD_TEXT)
    assert rich["complexity"]["score"] > thin["complexity"]["score"]


def test_complexity_breakdown_has_signals():
    result = score_project(RICH_PROJECT, JD_TEXT)
    signals = result["complexity"]["signals"]
    assert set(signals) == {"linked_skills", "text_richness", "blurb_variety", "github_metrics"}
    assert all(0.0 <= v <= 1.0 for v in signals.values())


def test_github_signal_omitted_when_no_metrics():
    result = score_project(THIN_PROJECT, JD_TEXT)
    assert "github_metrics" not in result["complexity"]["signals"]


def test_scorer_uses_stars_when_present():
    base = {**THIN_PROJECT, "metrics": {}}
    starred = {**THIN_PROJECT, "metrics": {"stars": 50}}
    assert (
        score_project(starred, JD_TEXT)["complexity"]["score"]
        > score_project(base, JD_TEXT)["complexity"]["score"]
    )


# ── score_project: acceptance case ────────────────────────────────────────────

def test_impressive_adjacent_outranks_thin_exact_match():
    """High-complexity adjacent project beats low-complexity exact keyword match."""
    rich = score_project(RICH_PROJECT, JD_TEXT)
    thin = score_project(THIN_PROJECT, JD_TEXT)

    # The thin project really is the better pure-keyword match...
    assert thin["relevance"]["score"] > rich["relevance"]["score"]
    # ...but the composite blend ranks the built-out project higher.
    assert rich["composite"] > thin["composite"]


def test_composite_is_weighted_blend():
    result = score_project(RICH_PROJECT, JD_TEXT)
    expected = (
        RELEVANCE_WEIGHT * result["relevance"]["score"]
        + COMPLEXITY_WEIGHT * result["complexity"]["score"]
    )
    assert abs(result["composite"] - expected) < 0.1


# ── Module constants ──────────────────────────────────────────────────────────

def test_weights_and_caps_are_named_constants():
    assert abs(RELEVANCE_WEIGHT + COMPLEXITY_WEIGHT - 1.0) < 1e-9
    for cap in (LINKED_SKILLS_CAP, TEXT_RICHNESS_CAP, BLURB_VARIETY_CAP,
                STARS_CAP, LANGUAGES_CAP, README_LENGTH_CAP):
        assert cap > 0


# ── Tailor wiring: _score_and_select_projects ─────────────────────────────────

def test_select_projects_sorts_by_composite_and_attaches_breakdown():
    selected = ResumeTailorAgent._score_and_select_projects(
        [THIN_PROJECT, RICH_PROJECT], JD_TEXT, max_projects=4
    )
    assert [p["name"] for p in selected] == ["Distributed Pipeline Platform", "ML Demo"]
    scores = [p["selection_score"] for p in selected]
    assert scores == sorted(scores, reverse=True)
    for p in selected:
        assert set(p["selection_breakdown"]) == {"relevance", "complexity"}


def test_select_projects_strips_scoring_inputs_from_llm_payload():
    selected = ResumeTailorAgent._score_and_select_projects(
        [RICH_PROJECT], JD_TEXT, max_projects=4
    )
    assert "linked_skills" not in selected[0]
    assert "metrics" not in selected[0]

    # No-JD fallback path strips them too
    fallback = ResumeTailorAgent._score_and_select_projects(
        [RICH_PROJECT], "", max_projects=4
    )
    assert "linked_skills" not in fallback[0]
    assert "metrics" not in fallback[0]


# ── Tailor wiring: _count_linked_skills ───────────────────────────────────────

def test_count_linked_skills_matches_repo_slug_and_name():
    evidence_rows = [
        ("skill-1", "github:nathansso/cool-repo", None),
        ("skill-2", "github:nathansso/cool-repo", None),
        ("skill-3", "resume", "Built Cool Tool with pandas"),
        ("skill-4", "github:nathansso/other-repo", None),
        ("skill-1", "resume", None),  # duplicate skill — counted once
    ]
    by_slug = ResumeTailorAgent._count_linked_skills(
        "Unrelated Name", "https://github.com/nathansso/cool-repo", evidence_rows
    )
    assert by_slug == 2

    by_name = ResumeTailorAgent._count_linked_skills("Cool Tool", None, evidence_rows)
    assert by_name == 1

    assert ResumeTailorAgent._count_linked_skills("", None, evidence_rows) == 0


# ── Project.metrics column ────────────────────────────────────────────────────

def test_project_metrics_roundtrip(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    metrics = {"stars": 12, "languages": ["Python"], "readme_length": 800}
    with Session(isolated_engine) as session:
        session.add(Project(user_id=user.user_id, name="Metrics Repo", metrics=metrics))
        session.commit()

    with Session(isolated_engine) as session:
        proj = session.exec(select(Project).where(Project.name == "Metrics Repo")).first()
        assert proj.metrics == metrics

    # Rows created without metrics default to {} (backward compatible)
    with Session(isolated_engine) as session:
        session.add(Project(user_id=user.user_id, name="Legacy Project"))
        session.commit()
        legacy = session.exec(select(Project).where(Project.name == "Legacy Project")).first()
        assert legacy.metrics == {}


# ── Parser persistence of repo metrics ────────────────────────────────────────

def test_parser_saves_repo_metrics(isolated_engine, monkeypatch):
    import agents.parser as parser_module

    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    user = _seed_user_and_skill(isolated_engine)

    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user = SimpleNamespace(user_id=user.user_id)

    repo_metrics = {"my-repo": {"stars": 5, "languages": ["Python"], "readme_length": 400}}
    agent._save_projects(
        [{"name": "My-Repo", "description": "a repo"}],  # case-insensitive name match
        "github:tester",
        repo_metrics,
    )

    with Session(isolated_engine) as session:
        proj = session.exec(select(Project).where(Project.name == "My-Repo")).first()
        assert proj.metrics == repo_metrics["my-repo"]

    # Re-ingest refreshes metrics on the existing row instead of duplicating
    agent._save_projects(
        [{"name": "My-Repo", "description": "a repo"}],
        "github:tester",
        {"my-repo": {"stars": 9, "languages": ["Python"], "readme_length": 500}},
    )
    with Session(isolated_engine) as session:
        rows = session.exec(select(Project).where(Project.name == "My-Repo")).all()
        assert len(rows) == 1
        assert rows[0].metrics["stars"] == 9
