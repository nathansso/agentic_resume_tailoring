"""Unit tests for the shared matched_skills selection helper (issue #150)."""

from agents.skill_selection import (
    is_metadata_key,
    skill_names,
    visible_matched_skills,
)

_EXPLAIN = {
    "matched": ["Python"],
    "emphasized": [],
    "inferred": [],
    "missing": ["Kubernetes"],
    "ats_score": 71.4,
}


def test_is_metadata_key():
    assert is_metadata_key("_explainability")
    assert is_metadata_key("_anything_else")
    assert not is_metadata_key("Python")
    assert not is_metadata_key("C++")


def test_skill_names_filters_metadata_and_preserves_order():
    matched = {
        "Python": {"weight": 1.0},
        "_explainability": _EXPLAIN,
        "FastAPI": {"weight": 0.5},
    }
    assert skill_names(matched) == ["Python", "FastAPI"]


def test_skill_names_handles_empty_and_none():
    assert skill_names(None) == []
    assert skill_names({}) == []
    assert skill_names({"_explainability": _EXPLAIN}) == []


def test_visible_matched_skills_keeps_values_intact():
    matched = {"Python": {"weight": 1.0, "required": True}, "_explainability": _EXPLAIN}
    visible = visible_matched_skills(matched)
    assert visible == {"Python": {"weight": 1.0, "required": True}}
    assert visible["Python"]["required"] is True


def test_filtering_does_not_mutate_the_caller_dict():
    """job_card.py reads matched_skills["_explainability"] on purpose (#150)."""
    matched = {"Python": {}, "_explainability": _EXPLAIN}
    skill_names(matched)
    visible_matched_skills(matched)
    assert matched["_explainability"] == _EXPLAIN
    assert "_explainability" in matched
