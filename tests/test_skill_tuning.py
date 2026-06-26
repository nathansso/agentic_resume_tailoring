"""Tests for tunable weights/bounds + selection metric and harness (issue #54 Phase 4)."""
import importlib

import agents.skill_scorer as ss
from agents.skill_scorer import (
    WEIGHTS,
    rank_and_select_skills,
    score_skills,
    selection_recall,
)


def _skill(name, proficiency=3, confidence=0.0, is_core=False):
    return {"name": name, "category": "Other", "proficiency": proficiency,
            "confidence": confidence, "is_core": is_core}


# ── env override helpers ────────────────────────────────────────────────────────

def test_env_float_reads_and_falls_back(monkeypatch):
    monkeypatch.setenv("SKILL_TEST_F", "0.42")
    assert ss._env_float("SKILL_TEST_F", 0.1) == 0.42
    assert ss._env_float("SKILL_TEST_MISSING", 0.1) == 0.1
    monkeypatch.setenv("SKILL_TEST_F", "notanumber")
    assert ss._env_float("SKILL_TEST_F", 0.1) == 0.1  # bad value → default


def test_env_int_reads_and_falls_back(monkeypatch):
    monkeypatch.setenv("SKILL_TEST_I", "12")
    assert ss._env_int("SKILL_TEST_I", 8) == 12
    assert ss._env_int("SKILL_TEST_MISSING", 8) == 8


def test_module_constants_pick_up_env_on_reload(monkeypatch):
    monkeypatch.setenv("SKILL_MIN", "3")
    monkeypatch.setenv("SKILL_W_TFIDF", "0.5")
    try:
        importlib.reload(ss)
        assert ss.MIN_SKILLS == 3
        assert ss.WEIGHTS["tfidf"] == 0.5
    finally:
        monkeypatch.undo()
        importlib.reload(ss)  # restore shipped defaults for other tests
    assert ss.MIN_SKILLS == 8


# ── per-call weight override ────────────────────────────────────────────────────

def test_weights_override_changes_ranking():
    skills = [_skill("Apple", proficiency=1), _skill("Banana", proficiency=5)]
    jd = "Apple is mentioned in this role."  # only Apple appears in the JD

    default = score_skills(skills, jd, matched_skills={})
    assert default[0]["name"] == "Apple"  # lexical relevance wins by default

    prof_heavy = score_skills(
        skills, jd, matched_skills={}, weights={**WEIGHTS, "proficiency": 0.30, "tfidf": 0.10}
    )
    assert prof_heavy[0]["name"] == "Banana"  # proficiency now dominates


# ── per-call bounds override ────────────────────────────────────────────────────

def test_bounds_override_caps_selection():
    skills = [_skill(f"Skill{i}") for i in range(10)]
    jd = " ".join(f"Skill{i}" for i in range(10))
    tight = rank_and_select_skills(
        skills, jd, matched_skills={}, bounds={"min_k": 2, "max_k": 2, "core_floor_k": 0}
    )
    assert len(tight) == 2


# ── selection recall metric ─────────────────────────────────────────────────────

def test_selection_recall():
    selected = [{"name": "Python"}, {"name": "Docker"}]
    assert selection_recall(selected, ["python", "docker"]) == 1.0
    assert selection_recall(selected, ["Python", "Kubernetes"]) == 0.5
    assert selection_recall(selected, []) == 1.0  # nothing to recall


# ── harness ─────────────────────────────────────────────────────────────────────

def test_eval_harness_runs_over_fixtures():
    from eval.skill_selection_eval import evaluate_preset, load_tasks
    tasks = load_tasks()
    assert tasks, "expected checked-in skill-selection fixtures"
    metrics = evaluate_preset(tasks, dict(WEIGHTS))
    assert 0.0 <= metrics["mean_recall"] <= 1.0
    assert metrics["mean_count"] > 0
