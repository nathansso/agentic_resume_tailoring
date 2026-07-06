"""
Tailoring efficacy benchmark tests (issue #51 Phase 1).

Covers the metric families in eval/metrics.py, the deterministic stub used for
offline runs, and an end-to-end smoke of eval/tailoring_benchmark.py driving
the real web API in a subprocess (its own isolated temp DB + env, so it can
never touch the developer's ~/.art data or this process's engine).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from eval.metrics import (
    OVER_REPEAT_THRESHOLD,
    ats_summary,
    experience_allocation,
    redundancy_metrics,
    skills_metrics,
    spearman,
)

ROOT = Path(__file__).resolve().parent.parent


# ── spearman ───────────────────────────────────────────────────────────────────

def test_spearman_perfect_and_inverse():
    assert spearman([1, 2, 3], [10, 20, 30]) == 1.0
    assert spearman([1, 2, 3], [30, 20, 10]) == -1.0


def test_spearman_undefined_cases():
    assert spearman([1], [2]) is None                # n < 2
    assert spearman([1, 1, 1], [1, 2, 3]) is None    # zero variance
    assert spearman([1, 2], [1, 2, 3]) is None       # length mismatch


def test_spearman_handles_ties():
    r = spearman([1, 2, 2, 3], [1, 2, 3, 4])
    assert r is not None and 0 < r <= 1


# ── experience allocation ──────────────────────────────────────────────────────

def _content(experiences=None, projects=None, skills_ranked=None, emphasized=None):
    return {
        "experiences": experiences or [],
        "projects": projects or [],
        "skills_ranked": skills_ranked or [],
        "skills_emphasized": emphasized or [],
    }


def test_experience_allocation_tracks_relevance():
    jd = "We need Kubernetes and Terraform experience for cloud infrastructure work."
    exps = [
        {"title": "Platform Engineer", "company": "A",
         "bullets": ["Ran Kubernetes clusters with Terraform for cloud infrastructure",
                     "Automated cloud infrastructure deployments with Terraform modules"]},
        {"title": "Barista", "company": "B", "bullets": ["Made espresso drinks"]},
    ]
    out = experience_allocation(_content(experiences=exps), jd)
    rows = out["experiences"]
    assert rows[0]["relevance"] > rows[1]["relevance"]
    assert rows[0]["words"] > rows[1]["words"]
    assert out["allocation_correlation"] == 1.0
    assert abs(sum(r["word_share"] for r in rows) - 1.0) < 0.01


def test_experience_allocation_empty_content():
    out = experience_allocation(_content(), "some jd text")
    assert out["experiences"] == []
    assert out["allocation_correlation"] is None
    assert out["total_bullet_words"] == 0


# ── skills metrics ─────────────────────────────────────────────────────────────

def test_skills_metrics_recall_and_selectivity():
    ranked = [
        {"name": "Python", "category": "Language", "score": 0.9},
        {"name": "Docker", "category": "Tool", "score": 0.5},
    ]
    matched = {"Python": {"match_type": "direct"}, "Kafka": {"match_type": "direct"}}
    out = skills_metrics(_content(skills_ranked=ranked), matched, total_profile_skills=10)
    assert out["rendered_count"] == 2
    assert out["selection_ratio"] == 0.2
    assert out["matched_recall"] == 0.5  # Python survived, Kafka did not
    assert out["categories"] == ["Language", "Tool"]


def test_skills_metrics_no_ranking():
    out = skills_metrics(_content(), {}, total_profile_skills=0)
    assert out["rendered_count"] == 0
    assert out["matched_recall"] is None
    assert out["within_cap_bounds"] is False


# ── redundancy ─────────────────────────────────────────────────────────────────

def test_redundancy_word_boundary_counting():
    # "SQL" must not be counted inside "MySQL" or "SQLAlchemy".
    exps = [{"title": "Eng", "company": "A",
             "bullets": ["Used MySQL and SQLAlchemy daily", "Wrote SQL queries"]}]
    ranked = [{"name": "SQL", "category": "Language", "score": 1.0}]
    out = redundancy_metrics(_content(experiences=exps, skills_ranked=ranked))
    assert out["term_counts"]["sql"] == 2  # skills section + one bullet


def test_redundancy_flags_over_repeated_terms():
    bullets = [f"Improved Python service number {i} with Python" for i in range(3)]
    exps = [{"title": "Eng", "company": "A", "bullets": bullets}]
    ranked = [{"name": "Python", "category": "Language", "score": 1.0}]
    out = redundancy_metrics(_content(experiences=exps, skills_ranked=ranked))
    assert out["term_counts"]["python"] > OVER_REPEAT_THRESHOLD
    assert "python" in out["over_repeated"]
    assert out["over_repeated_count"] == 1


def test_redundancy_falls_back_to_skills_emphasized():
    exps = [{"title": "Eng", "company": "A", "bullets": ["Shipped Python code"]}]
    out = redundancy_metrics(_content(experiences=exps, emphasized=["Python"]))
    assert out["term_counts"]["python"] == 2  # bullet + emphasized list rendered as skills


# ── ATS summary ────────────────────────────────────────────────────────────────

def test_ats_summary_deltas():
    baseline = {"composite": 40.0, "skill_coverage": {"score": 30.0},
                "keyword_coverage": {"score": 20.0}, "section_presence": {"score": 100.0},
                "role_level": {"score": 50.0}}
    tailored = {"composite": 70.0, "skill_coverage": {"score": 80.0},
                "keyword_coverage": {"score": 45.0}, "section_presence": {"score": 100.0},
                "role_level": {"score": 50.0}}
    out = ats_summary(baseline, tailored)
    assert out["delta"] == 30.0
    assert out["skill_coverage"]["delta"] == 50.0
    assert out["section_presence"]["delta"] == 0.0


def test_ats_summary_missing_breakdowns():
    out = ats_summary({}, {})
    assert out["delta"] is None


# ── stub determinism ───────────────────────────────────────────────────────────

def test_stub_jd_skill_extraction_is_deterministic_and_jd_sensitive():
    from eval.tailoring_benchmark import _stub_extract_jd_skills

    jd = "Looking for Python and Kubernetes engineers. TensorFlow is a plus."
    a, b = _stub_extract_jd_skills(jd), _stub_extract_jd_skills(jd)
    assert a == b
    names = {s["name"] for s in a}
    assert {"Python", "Kubernetes", "TensorFlow"} <= names
    assert "Unity" not in names


def test_stub_llm_round_trips_through_langchain_chain():
    from langchain_core.output_parsers import JsonOutputParser
    from langchain_core.prompts import ChatPromptTemplate

    from eval.tailoring_benchmark import _make_stub_llm

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a job description parser. Extract the job title and company name."),
        ("user", "Text:\n{text}"),
    ])
    chain = prompt | _make_stub_llm()(role="extract") | JsonOutputParser()
    out = chain.invoke({"text": "whatever"})
    assert out == {"title": "Benchmark Role", "company": "Benchmark Co"}


# ── dataset sanity ─────────────────────────────────────────────────────────────

def test_jd_dataset_is_present_and_well_formed():
    files = sorted((ROOT / "eval" / "jd_dataset").glob("*.json"))
    assert len(files) >= 5, "checked-in JD dataset went missing"
    for path in files:
        task = json.loads(path.read_text(encoding="utf-8"))
        for key in ("id", "company", "title", "description", "source", "url"):
            assert key in task, f"{path.name} missing {key}"
        assert len(task["description"]) > 500


# ── end-to-end smoke (real web API, stub LLM, subprocess isolation) ────────────

def test_benchmark_end_to_end_stub_smoke(tmp_path):
    """One task through register→ingest→analyze→tailor→export via the API."""
    proc = subprocess.run(
        [sys.executable, str(ROOT / "eval" / "tailoring_benchmark.py"),
         "--stub", "--limit", "1", "--out", str(tmp_path)],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )
    assert proc.returncode == 0, f"benchmark failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"

    results_files = list(tmp_path.glob("tailoring_benchmark_*.json"))
    assert len(results_files) == 1
    results = json.loads(results_files[0].read_text(encoding="utf-8"))
    assert results["mode"] == "stub"
    assert results["failed"] == []
    task = results["task_results"][0]
    m = task["metrics"]
    # The tailored composite must exist and beat (or match) baseline in stub mode.
    assert m["ats"]["tailored_composite"] is not None
    assert m["ats"]["delta"] is not None and m["ats"]["delta"] >= 0
    # All three quality families computed.
    assert m["experience_allocation"]["experiences"]
    assert m["skills"]["rendered_count"] > 0
    assert m["redundancy"]["term_counts"]
    # CSV artifact alongside the JSON.
    assert list(tmp_path.glob("tailoring_benchmark_*.csv"))
    # Rendered .tex + raw content written for the notebook's resume viewer.
    renders = list((tmp_path / "renders").rglob("*.tex"))
    assert renders and renders[0].read_text(encoding="utf-8").startswith("%----")
