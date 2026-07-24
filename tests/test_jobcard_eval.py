"""JobCard card-quality eval tests (issue #137, on the #51 harness).

Runs `eval/jobcard_eval.py` against the checked-in `eval/jobcard_dataset/`. The
eval measures whether a compiled card is a sufficient statistic for the next
tailoring decision, weighted so a lost `user-rejected` item that recurs is
penalized harder than a generic-recap miss (#129 finding 1).

Offline by design — the planner arm is a deterministic probe rule and
`role_family` is pinned by the task files, so what is pinned here is the
compile's information-preservation contract, not a model's output on the day.

The load-bearing test is `test_eval_goes_red_when_the_negation_signal_is_lost`:
an eval that cannot fail proves nothing.
"""
import json

import pytest

from eval import jobcard_eval as je


def _task(task_id: str) -> dict:
    return next(t for t in je.load_tasks() if t["id"] == task_id)


# ── dataset ───────────────────────────────────────────────────────────────────

def test_dataset_is_present_and_well_formed():
    tasks = je.load_tasks()
    assert tasks, "eval/jobcard_dataset/ must contain at least one task"
    ids = [t["id"] for t in tasks]
    assert len(ids) == len(set(ids)), f"duplicate task ids: {ids}"
    for task in tasks:
        for key in ("id", "description", "prior_job", "next_job", "expect"):
            assert key in task, f"{task.get('id')!r} is missing {key!r}"
        assert task["next_job"].get("items"), f"{task['id']!r} plans nothing"
        for item in task["next_job"]["items"]:
            assert {"key", "section", "label"} <= set(item), item


def test_dataset_covers_the_negation_case():
    """The whole point of the tier: at least one task must exercise a rejection
    that recurs, or the weighted metric is never actually tested."""
    assert any(t.get("recurring_rejected") for t in je.load_tasks())


def test_load_tasks_rejects_unknown_ids():
    with pytest.raises(SystemExit):
        je.load_tasks(["no_such_task"])


# ── the eval is green on the shipped compile ──────────────────────────────────

def test_jobcard_eval_is_green(tmp_path):
    results = je.run_eval(out_dir=tmp_path)

    failing = [r for r in results["task_results"] if not r["passed"]]
    assert not failing, "\n".join(
        f"{r['task_id']}: quality {r['card_quality']} < {r['min_card_quality']}"
        for r in failing
    )
    assert results["card_quality"] == 1.0
    assert results["functional_equivalence"] == 1.0
    assert results["negation_failures"] == 0
    assert results["mode"] == "scripted", "the suite must not call a provider"


def test_eval_writes_a_results_file(tmp_path):
    results = je.run_eval(task_ids=["rejected_project_recurs"], out_dir=tmp_path)
    written = list(tmp_path.glob("jobcard_*.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding="utf-8")) == results


# ── the eval can actually fail ────────────────────────────────────────────────

def test_eval_goes_red_when_the_negation_signal_is_lost(tmp_path, monkeypatch):
    """Neuter the rejection compile and the negation task must go red.

    An eval that only ever passes is decoration. This drops exactly the field
    #137 exists to preserve and asserts the metric notices — with a named
    negation failure, not just a lower number.
    """
    import agents.job_card as jc

    monkeypatch.setattr(jc, "extract_rejected_items", lambda _decisions: [])

    results = je.run_eval(task_ids=["rejected_project_recurs"], out_dir=tmp_path)

    assert results["card_quality"] < 1.0
    assert results["task_results"][0]["passed"] is False
    failures = results["task_results"][0]["negation_failures"]
    assert failures and "pixel adventure" in failures[0]


def test_eval_goes_red_when_emphasis_is_lost(tmp_path, monkeypatch):
    """The other half of the compile: losing what the resume led with also
    diverges the plans."""
    import agents.job_card as jc

    monkeypatch.setattr(jc, "_emphasized", lambda _result: {
        "experiences": [], "projects": [], "skills": [],
        "led_experience": None, "led_project": None,
    })
    results = je.run_eval(task_ids=["emphasis_carries_forward"], out_dir=tmp_path)
    assert results["card_quality"] < 1.0


def test_a_lost_rejection_is_penalized_harder_than_a_generic_miss():
    """The weighting itself: same number of diverging items, very different
    scores, because one of them is a recurring rejection."""
    items = [
        {"key": "proj:a", "section": "project", "label": "Alpha"},
        {"key": "proj:b", "section": "project", "label": "Beta"},
        {"key": "proj:c", "section": "project", "label": "Gamma"},
    ]
    reference = {"proj:a": "delete", "proj:b": "keep", "proj:c": "revise"}

    lost_rejection = je.score_plans(
        items, reference, {**reference, "proj:a": "revise"}, ["proj:a"])
    generic_miss = je.score_plans(
        items, reference, {**reference, "proj:b": "revise"}, ["proj:a"])

    assert lost_rejection["functional_equivalence"] == \
        generic_miss["functional_equivalence"]
    assert lost_rejection["card_quality"] < generic_miss["card_quality"]
    assert lost_rejection["negation_failures"]
    assert not generic_miss["negation_failures"]


# ── the arms and the ablation ─────────────────────────────────────────────────

def test_the_probe_rule_actually_depends_on_the_memory():
    """A planner arm that ignored the memory would score every card 1.0,
    including an empty one — so the metric would be meaningless."""
    items = [{"key": "proj:a", "section": "project", "label": "Alpha"}]
    assert je.plan_from_memory(items, {"removed": ["alpha"]}) == {"proj:a": "delete"}
    assert je.plan_from_memory(items, {"emphasized": ["alpha"]}) == {"proj:a": "keep"}
    assert je.plan_from_memory(items, {}) == {"proj:a": "revise"}


def test_full_history_and_card_arms_agree_on_the_shipped_compile():
    """Functional equivalence, directly: the distilled card drives the same
    decisions as reading the raw finished result."""
    from agents.job_card import compile_card_payload

    task = _task("rejected_project_recurs")
    job, result = je._rows(task["prior_job"])
    payload = compile_card_payload(job, result, role_family="machine_learning")

    full = je.memory_from_full_history(task["prior_job"])
    card = je.memory_from_card(payload)
    assert card["removed"] == full["removed"] == ["pixel adventure"]
    assert set(card["emphasized"]) == set(full["emphasized"])
    assert card["user_score"] == full["user_score"] == 5


def test_ablation_reports_every_field(tmp_path):
    results = je.run_eval(out_dir=tmp_path)
    assert set(results["ablation"]) == set(je.ABLATION_FIELDS)
    # The two fields the probe consumes must show a real cost when dropped.
    assert results["ablation"]["rejected_items"]["worst_delta"] < 0
    assert results["ablation"]["emphasized"]["worst_delta"] < 0
    assert results["ablation"]["rejected_items"]["negation_failures"] >= 1


def test_ablation_can_be_skipped(tmp_path):
    results = je.run_eval(ablation=False, out_dir=tmp_path)
    assert results["ablation"] == {}


def test_respecting_a_rejection_raises_jd_keyword_density(tmp_path):
    """The downstream arm. The ATS composite is a coverage measure and cannot
    see an off-topic item being removed, so the eval reports relevance density
    beside it — and that is what actually moves on the negation task."""
    results = je.run_eval(task_ids=["rejected_project_recurs"], out_dir=tmp_path)
    record = results["task_results"][0]
    assert record["relevance_delta"] > 0
    assert record["outcome_delta"] == 0.0
