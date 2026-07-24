"""Knowledge-Updates regression eval tests (issue #21, on the #51 harness).

Runs `eval/knowledge_updates_eval.py` against the checked-in `eval/ku_dataset/`
on an isolated SQLite engine. The eval is the LongMemEval-derived regression the
Phase 1 epic (#140) calls for: a later turn contradicts an earlier fact, and the
knowledge graph must *update* rather than go stale or duplicate.

Offline by design — the scripted extractors stand in for the #142 seam, so what
is pinned here is the pipeline contract, not a model's output on the day.
"""
import json

import pytest

from eval import knowledge_updates_eval as ku
from database.models import Experience


def _task(task_id: str) -> dict:
    return next(t for t in ku.load_tasks() if t["id"] == task_id)


def test_dataset_is_present_and_well_formed():
    tasks = ku.load_tasks()
    assert tasks, "eval/ku_dataset/ must contain at least one task"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), f"duplicate task ids: {ids}"
    for task in tasks:
        for key in ("id", "description", "turns", "notes", "decisions", "expect"):
            assert key in task, f"{task.get('id')!r} is missing {key!r}"
        assert task["expect"], f"{task['id']!r} asserts nothing"


def test_load_tasks_rejects_unknown_ids():
    with pytest.raises(SystemExit):
        ku.load_tasks(["no_such_task"])


def test_knowledge_updates_eval_is_green(isolated_engine, tmp_path):
    """The regression itself: every task's post-state matches its expectation."""
    results = ku.run_eval(engine=isolated_engine, out_dir=tmp_path)

    failing = [r for r in results["task_results"] if not r["passed"]]
    assert not failing, "\n".join(
        f"{r['task_id']}: {'; '.join(r['failures'])}" for r in failing
    )
    assert results["update_accuracy"] == 1.0
    assert results["mode"] == "scripted", "the suite must not call a provider"


def test_eval_writes_a_results_file(isolated_engine, tmp_path):
    results = ku.run_eval(
        task_ids=["new_fact_is_added"], engine=isolated_engine, out_dir=tmp_path)
    written = list(tmp_path.glob("knowledge_updates_*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding="utf-8")) == results


def test_stale_graph_is_reported_as_a_failure(isolated_engine, tmp_path, monkeypatch):
    """The eval must actually fail when the update is dropped — otherwise it
    proves nothing. Neuter the supersede path and watch the promotion task go red."""
    import services

    monkeypatch.setattr(
        services, "_supersede_experience",
        lambda *a, **kw: "Updated experience (pretend).")
    monkeypatch.setitem(
        services._SUPERSEDERS, "experience", services._supersede_experience)

    results = ku.run_eval(
        task_ids=["promotion_supersedes_experience"],
        engine=isolated_engine, out_dir=tmp_path,
    )
    assert results["update_accuracy"] == 0.0
    failures = results["task_results"][0]["failures"]
    assert any("Staff Engineer" in f for f in failures), failures


def test_check_expectations_flags_a_duplicated_row():
    """The other failure mode: superseding by adding a second row."""
    graph = {
        "skills": [], "projects": [],
        "experiences": [
            {"title": "Senior Engineer", "company": "Acme"},
            {"title": "Staff Engineer", "company": "Acme"},
        ],
    }
    failures = ku.check_expectations(
        {"experiences": [{"company": "Acme", "title": "Staff Engineer", "count": 1}]},
        [], graph,
    )
    assert failures and "got 2" in failures[0]


def test_run_task_seeds_and_applies_through_the_service_layer(isolated_engine):
    """The eval drives the real persistence path, not a shortcut of its own."""
    from sqlmodel import Session, select

    record = ku.run_task(_task("promotion_supersedes_experience"), isolated_engine)
    assert record["passed"], record["failures"]
    assert record["decisions"] == ["supersede"]

    with Session(isolated_engine) as session:
        rows = session.exec(select(Experience)).all()
    assert len(rows) == 1 and rows[0].title == "Staff Engineer"
    assert rows[0].source_context == "ku:promotion_supersedes_experience"
