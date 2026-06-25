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
