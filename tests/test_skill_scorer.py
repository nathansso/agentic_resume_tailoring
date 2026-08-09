"""Tests for JD-relevance skill scoring + selection and formatter wiring (issue #54)."""
from uuid import uuid4

import pytest

from agents.skill_scorer import (
    CORE_FLOOR_K,
    MAX_SKILLS,
    MIN_SKILLS,
    compute_idf,
    rank_and_select_skills,
    score_skills,
)
from agents.formatter import ResumeFormatterAgent


def _skill(name, category="Other", proficiency=3, confidence=0.5):
    return {
        "name": name,
        "category": category,
        "proficiency": proficiency,
        "confidence": confidence,
    }


# ── Ranking ────────────────────────────────────────────────────────────────────

def test_ranks_jd_relevant_skills_above_irrelevant():
    skills = [
        _skill("Kubernetes", "Cloud"),
        _skill("PyTorch", "AI & Machine Learning"),
        _skill("Microsoft Excel", "Tools"),
    ]
    jd = "We need someone strong in Kubernetes to manage container orchestration."
    ranked = score_skills(skills, jd, matched_skills={})
    names = [s["name"] for s in ranked]
    assert names[0] == "Kubernetes"
    assert names.index("Kubernetes") < names.index("Microsoft Excel")


def test_match_confidence_and_jd_weight_boost_score():
    skills = [_skill("Python"), _skill("Rust")]
    jd = "Python and Rust both appear in this job description."
    matched = {
        "Python": {"match_type": "direct", "required": True, "weight": 3.0},
        "Rust": {"match_type": "indirect", "required": False, "weight": 1.0},
    }
    ranked = score_skills(skills, jd, matched_skills=matched)
    by_name = {s["name"]: s for s in ranked}
    assert by_name["Python"]["score"] > by_name["Rust"]["score"]
    # jd_weight + match_confidence components are present when matched_skills given
    assert "match_confidence" in by_name["Python"]["components"]
    assert "jd_weight" in by_name["Python"]["components"]


def test_idf_down_weights_common_terms():
    # "common" appears in every corpus doc (low IDF); "rareskill" in none (high IDF).
    corpus = ["common tooling"] * 5
    idf = compute_idf(corpus)
    from agents.skill_scorer import _idf_of
    assert _idf_of("common", idf) < _idf_of("rareskill", idf)


# ── Selection: cap + floor ──────────────────────────────────────────────────────

def test_cap_bounds_respected_for_large_skill_set():
    # 30 skills, all weakly relevant; selection must stay within [MIN, MAX] (+floor).
    skills = [_skill(f"Skill{i}", proficiency=1) for i in range(30)]
    jd = "Skill0 Skill1 Skill2 Skill3 Skill4 Skill5 Skill6 Skill7 Skill8 Skill9"
    selected = rank_and_select_skills(skills, jd, matched_skills={})
    assert selected is not None
    assert MIN_SKILLS <= len(selected) <= MAX_SKILLS + CORE_FLOOR_K


def test_core_floor_keeps_strong_skills_on_off_domain_jd():
    # JD shares nothing with the skills; the highest-proficiency skill must survive.
    skills = [_skill(f"Niche{i}", proficiency=1) for i in range(20)]
    skills.append(_skill("Flagship", proficiency=5, confidence=1.0))
    jd = "Completely unrelated marketing copywriting role with no technical overlap."
    selected = rank_and_select_skills(skills, jd, matched_skills={})
    assert selected is not None
    assert any(s["name"] == "Flagship" for s in selected)


def test_short_skill_list_returned_whole():
    skills = [_skill("Python"), _skill("SQL")]
    jd = "Python and SQL."
    selected = rank_and_select_skills(skills, jd, matched_skills={})
    assert {s["name"] for s in selected} == {"Python", "SQL"}


# ── No-JD fallback ──────────────────────────────────────────────────────────────

def test_no_jd_signal_returns_none():
    skills = [_skill("Python")]
    assert score_skills(skills, "", matched_skills={}) is None
    assert score_skills(skills, "   ", matched_skills={}) is None
    # JD with only stop words / numbers yields no keywords → None.
    assert score_skills(skills, "the and of 123", matched_skills={}) is None


def test_rank_and_select_returns_none_without_jd():
    assert rank_and_select_skills([_skill("Python")], "", matched_skills={}) is None


# ── Determinism ─────────────────────────────────────────────────────────────────

def test_ranking_is_deterministic():
    skills = [_skill("Python"), _skill("Java"), _skill("Go")]
    jd = "Python Java Go backend services."
    a = rank_and_select_skills(skills, jd, matched_skills={})
    b = rank_and_select_skills(skills, jd, matched_skills={})
    assert a == b


# ── Formatter wiring ────────────────────────────────────────────────────────────

def test_formatter_preserves_ranked_order_no_alphabetical_sort():
    agent = ResumeFormatterAgent(uuid4())
    ranked = [
        {"name": "PyTorch", "category": "AI & Machine Learning", "score": 0.9},
        {"name": "TensorFlow", "category": "AI & Machine Learning", "score": 0.8},
        {"name": "Docker", "category": "Tools", "score": 0.7},
    ]
    cats = agent._get_skill_categories(ranked)
    # Within-category order follows score (PyTorch before TensorFlow), NOT A→Z.
    assert cats["AI & Machine Learning"] == ["PyTorch", "TensorFlow"]
    ordered = agent._ordered_skill_cats(cats, ranked)
    # Category order follows first appearance (relevance), AI first then Tools.
    assert [c for c, _ in ordered] == ["AI & Machine Learning", "Tools"]


def test_formatter_fallback_uses_static_order_and_sort():
    agent = ResumeFormatterAgent(uuid4())
    cats = {"Tools": ["Zsh", "Awk"], "Languages & Libraries": ["Python", "C"]}
    ordered = agent._ordered_skill_cats(cats, ranked=None)
    # Static order puts Languages & Libraries before Tools, skills sorted A→Z.
    assert ordered[0][0] == "Languages & Libraries"
    assert ordered[0][1] == ["C", "Python"]
    assert ordered[1][1] == ["Awk", "Zsh"]


def test_build_tex_skills_renders_ranked_section():
    agent = ResumeFormatterAgent(uuid4())
    ranked = [{"name": "Kubernetes", "category": "Cloud", "score": 0.9}]
    tex = agent._build_tex_skills(ranked)
    assert "Technical Skills" in tex
    assert "Kubernetes" in tex


# ── Determinism of the DB-backed merge (issue #158) ────────────────────────────
#
# `_rank_skills` loads UserSkill rows with an unordered query and merges rows
# that share a canonical name. `category` and the skill_id feeding the cached
# embedding were both first-wins, so row order picked the winner and moved the
# ranking. These tests shuffle insertion order and demand an identical result.

import numpy as np
from sqlmodel import Session

import agents.matcher as matcher_module
from database.models import JobDescription, Skill, User, UserSkill


class _DistinctVectorModel:
    """Deterministic per-text vectors, distinct for aliases of one canonical name.

    `Postgres` and `PostgreSQL` must encode *differently*, otherwise the test
    cannot observe which row `id_by_name` chose.

    Vectors live in the positive orthant on purpose: `_semantic_similarity`
    clamps negative cosines to 0.0, so two distinct-but-negatively-aligned
    vectors would both score 0.0 and hide the very difference under test.
    """

    def encode(self, texts, normalize_embeddings=True):
        vecs = []
        for t in texts:
            seed = sum((i + 1) * ord(c) for i, c in enumerate(t.lower())) or 1
            rng = np.random.default_rng(seed % (2**32))
            vecs.append(np.abs(rng.standard_normal(8)) + 0.1)
        arr = np.asarray(vecs, dtype=float)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms


# (raw name, category, proficiency, confidence). Three canonical names are
# reached by two different raw names each — the shape that makes first-wins
# observable, since aliases carry different categories *and* different vectors.
_ALIASED_SKILLS = [
    ("Python", "Languages", 5, 0.90),
    ("python", "Programming", 3, 0.60),      # -> Python
    ("Postgres", "Data", 3, 0.50),           # -> PostgreSQL
    ("PostgreSQL", "Databases", 4, 0.85),    # -> PostgreSQL
    ("sklearn", "ML", 3, 0.55),              # -> scikit-learn
    ("scikit-learn", "Libraries", 4, 0.70),  # -> scikit-learn
    ("FastAPI", "Frameworks", 4, 0.80),
    ("Docker", "Tooling", 3, 0.70),
    ("Redis", "Databases", 2, 0.50),
    ("TypeScript", "Languages", 4, 0.75),
]

_JD_TEXT = (
    "Backend engineer: strong Python and FastAPI, PostgreSQL schema design, "
    "Docker containers, Redis caching, TypeScript on the frontend, and some "
    "scikit-learn modelling."
)

_MATCHED = {
    "Python": {"weight": 1.0, "required": True, "match_type": "direct"},
    "FastAPI": {"weight": 0.9, "required": True, "match_type": "direct"},
    "PostgreSQL": {"weight": 0.8, "required": True, "match_type": "name_match"},
    "Docker": {"weight": 0.6, "required": False, "match_type": "semantic"},
}


def _rank_with_insertion_order(engine, job_id, order):
    """Seed a fresh user whose UserSkill rows are written in `order`, then rank."""
    import agents.tailor as tailor_module

    with Session(engine) as session:
        user = User(name="Order Probe", email=f"probe-{uuid4().hex[:10]}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

        for name, category, prof, conf in order:
            skill = Skill(name=name, category=category)
            session.add(skill)
            session.commit()
            session.refresh(skill)
            session.add(UserSkill(
                user_id=uid, skill_id=skill.skill_id,
                proficiency=prof, confidence_score=conf, is_core=False,
            ))
            session.commit()

    return tailor_module.ResumeTailorAgent._rank_skills(uid, job_id, _JD_TEXT, _MATCHED)


@pytest.fixture()
def _ranking_env(isolated_engine, monkeypatch):
    """Bind tailor's module-level engine and a network-free embedding model."""
    import agents.tailor as tailor_module

    monkeypatch.setattr(tailor_module, "engine", isolated_engine)
    monkeypatch.setattr(matcher_module, "get_embedding_model", lambda: _DistinctVectorModel())

    with Session(isolated_engine) as session:
        job = JobDescription(title="Backend Engineer", company="Acme", description=_JD_TEXT)
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = job.job_id
    return isolated_engine, job_id


def test_rank_skills_is_independent_of_userskill_insertion_order(_ranking_env):
    """The acceptance criterion: shuffling insertion order must not move selection."""
    engine, job_id = _ranking_env

    baseline = _rank_with_insertion_order(engine, job_id, _ALIASED_SKILLS)
    assert baseline, "expected a non-empty ranking"

    import random
    for seed in range(6):
        shuffled = list(_ALIASED_SKILLS)
        random.Random(seed).shuffle(shuffled)
        got = _rank_with_insertion_order(engine, job_id, shuffled)
        assert got == baseline, (
            f"insertion order (seed={seed}) changed the ranking:\n"
            f"  baseline={[(s['name'], s['category'], s['score']) for s in baseline]}\n"
            f"  got     ={[(s['name'], s['category'], s['score']) for s in got]}"
        )


def test_rank_skills_category_does_not_depend_on_row_order(_ranking_env):
    """`category` was first-wins — the visible `category_count` fingerprint (#158)."""
    engine, job_id = _ranking_env

    forward = _rank_with_insertion_order(engine, job_id, _ALIASED_SKILLS)
    reverse = _rank_with_insertion_order(engine, job_id, list(reversed(_ALIASED_SKILLS)))

    assert {s["name"]: s["category"] for s in forward} == \
           {s["name"]: s["category"] for s in reverse}
    # And the merge resolves to the content-chosen representative, not whichever
    # row happened to arrive first: min() over (raw name, category). The sort is
    # plain codepoint order, so "PostgreSQL" precedes "Postgres" ('S' < 's').
    cats = {s["name"]: s["category"] for s in forward}
    assert cats["PostgreSQL"] == "Databases"    # from raw "PostgreSQL"
    assert cats["scikit-learn"] == "Libraries"  # "scikit-learn" < "sklearn"


def test_rank_skills_scores_do_not_depend_on_row_order(_ranking_env):
    """`id_by_name` picked which cached embedding fed the semantic component."""
    engine, job_id = _ranking_env

    forward = _rank_with_insertion_order(engine, job_id, _ALIASED_SKILLS)
    reverse = _rank_with_insertion_order(engine, job_id, list(reversed(_ALIASED_SKILLS)))

    assert {s["name"]: s["score"] for s in forward} == \
           {s["name"]: s["score"] for s in reverse}
