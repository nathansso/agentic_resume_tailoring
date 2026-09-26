"""Plan programs and the executor (issue #197).

The executor runs a host's whole plan: arbitration, each node under the
per-metric acceptance rule, finalize, one committed tree node. Line counts come
from an injected measurer, so none of this needs a LaTeX engine.
"""

import copy
import json

from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from database.models import JobHead, UserJobResult, UserPreference

BENCH_PROFILE = "benchmark_profile.md"


def _one_line_each(texts):
    return [1 for _ in texts]


@pytest.fixture()
def env(isolated_engine, monkeypatch):
    """The benchmark profile seeded as one user, one job, a one-line measurer."""
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import PROFILES_DIR
    from eval.scripted_host import create_job, seed_profile
    from eval.tailoring_benchmark import load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", _one_line_each)
    uid = seed_profile(load_profile(PROFILES_DIR / BENCH_PROFILE))
    task = load_tasks(limit=5)[3]
    job_id = create_job(uid, task)
    return uid, job_id, task


def _run(uid, program, **kw):
    from harness.executor import execute_plan
    return execute_plan(uid, program, **kw)


def _head(engine, job_id):
    from uuid import UUID
    with Session(engine) as s:
        h = s.get(JobHead, UUID(job_id))
        return str(h.node_id) if h else None


EXP = "exp:machine learning engineer|nimbus analytics"
EXP2 = "exp:backend software engineer|bluefin software"
PROJ = "proj:pixel adventure"


# ── program schema ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("node,msg", [
    ({"id": "a", "op": "revise", "item_key": EXP}, "revise needs bullets"),
    ({"id": "a", "op": "replace", "item_key": PROJ}, "needs replacement_key"),
    ({"id": "a", "op": "replace", "item_key": EXP, "replacement_key": PROJ}, "swaps one project"),
    ({"id": "a", "op": "delete", "item_key": PROJ, "because": "felt like it"}, "pref: or user:"),
    ({"id": "a", "op": "keep", "item_key": "skill:python"}, "exp: or proj:"),
    ({"id": "a", "op": "delete", "item_key": PROJ,
      "accept": {"tolerances": {"duplication": 0.9}}}, "only tighten"),
])
def test_the_schema_rejects_malformed_nodes(node, msg):
    from harness.program import Program

    with pytest.raises(ValidationError, match=msg):
        Program.model_validate({"job_id": "j", "nodes": [node]})


def test_node_ids_and_item_keys_are_unique():
    from harness.program import Program

    keep = {"op": "keep", "item_key": EXP}
    with pytest.raises(ValidationError, match="unique"):
        Program.model_validate({"job_id": "j", "nodes": [{"id": "a", **keep}, {"id": "a", **keep}]})
    with pytest.raises(ValidationError, match="one node per item_key"):
        Program.model_validate({"job_id": "j", "nodes": [{"id": "a", **keep}, {"id": "b", **keep}]})


def test_json_pointer_patch():
    from harness.program import apply_patch

    doc = {"nodes": [{"id": "a"}], "parent": None}
    out = apply_patch(doc, [{"op": "replace", "path": "/parent", "value": "n1"},
                            {"op": "add", "path": "/nodes/-", "value": {"id": "b"}},
                            {"op": "remove", "path": "/nodes/0"}])
    assert out == {"nodes": [{"id": "b"}], "parent": "n1"} and doc["parent"] is None
    with pytest.raises(ValueError):
        apply_patch(doc, [{"op": "remove", "path": "/nodes/7"}])


# ── the acceptance rule, on content alone ────────────────────────────────────

def _content(bullets, skills=("python",)):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": list(bullets)}],
            "projects": [{"name": "Tool", "bullets": ["Shipped a command line tool"]}],
            "skills_ranked": [{"name": s} for s in skills]}


BASE = ["Built a forecasting service for retail demand",
        "Designed dashboards tracking weekly revenue",
        "Migrated reporting jobs to a scheduled pipeline",
        "Reviewed pull requests across three teams"]


def test_a_stuffing_edit_is_reverted_even_though_a_target_rises():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="Python engineer building forecasting pipelines in Python")
    before = metric_vector(_content(BASE), ctx)
    stuffed = metric_vector(_content([b + " in Python" for b in BASE]), ctx)
    verdict = accept(before, stuffed)
    assert stuffed["targets"]["relevance_density"] > before["targets"]["relevance_density"]
    assert not verdict["accepted"] and verdict["reason"].startswith("guard: stuffing")


def test_a_requested_delete_needs_no_target_to_improve():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting")
    before = metric_vector(_content(BASE), ctx)
    same = copy.deepcopy(before)
    assert not accept(before, same)["accepted"]
    assert accept(before, same, requested=True)["accepted"]


def test_hard_gates_block_only_new_violations():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting", hard_suppress={"proj:tool"})
    before = metric_vector(_content(BASE), ctx)
    assert before["gates"]["preferences"] == ["suppressed:proj:tool"]
    better = metric_vector(_content(BASE[:3] + ["Automated weekly forecasting reports"]), ctx)
    assert accept(before, better)["accepted"]          # the old violation is not new

    ctx.hard_emphasize = {"exp:engineer|acme"}
    emptied = _content(BASE)
    emptied["experiences"] = []
    v = accept(before, metric_vector(emptied, ctx), requested=True)
    assert not v["accepted"] and v["reason"].startswith("hard_gate: preferences")


def test_the_ats_composite_is_reported_and_never_decides():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting dashboards")
    before = metric_vector(_content(BASE), ctx)
    after = copy.deepcopy(before)
    after["report"]["ats"] = 99.0
    assert not accept(before, after)["accepted"]


# ── executing programs ───────────────────────────────────────────────────────

def test_a_first_plan_builds_on_the_kg_and_commits_a_host_node(isolated_engine, env):
    from harness import tree

    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [{"id": "k", "op": "keep", "item_key": EXP}],
                     "host": {"name": "claude-code", "version": "2.1"}})
    assert out.get("error") is None and out["committed"]
    assert _head(isolated_engine, job_id) == out["node_id"]
    node = tree.get_node(uid, out["node_id"])
    assert node["source"] == "host" and node["parent_id"] is None
    assert node["program"]["nodes"][0]["id"] == "k"
    assert node["provenance"]["host"] == "claude-code"
    assert node["provenance"]["program_id"] == out["program_id"]
    with Session(isolated_engine) as s:     # materialized for the web app / art ui
        result = s.exec(select(UserJobResult).where(
            UserJobResult.job_id == UUID(node["job_id"]))).one()
        assert result.tailored_resume_content == node["content"]
        assert {e["title"] for e in node["content"]["experiences"]} >= {"Machine Learning Engineer"}


def test_refused_nodes_never_execute_and_say_why(isolated_engine, env):
    from harness.executor import _KG

    uid, job_id, _ = env
    src = _KG(uid).source_bullets[EXP]
    with Session(isolated_engine) as s:
        pref = UserPreference(user_id=uid, text="Never show the game project",
                              polarity="suppress", target_key=PROJ, scope_type="global",
                              strength=5)
        s.add(pref)
        s.commit()
        pref_id = pref.preference_id
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "ghost", "op": "revise", "item_key": "exp:astronaut|nasa",
         "bullets": [{"text": "Flew", "cites": ["exp:astronaut|nasa"]}]},
        {"id": "badcite", "op": "revise", "item_key": EXP,
         "bullets": [{"text": src[0], "cites": [f"{EXP}#b99"]}]},
        {"id": "nocite", "op": "revise", "item_key": EXP2,
         "bullets": [{"text": "Did things", "cites": []}]},
        {"id": "pinned", "op": "keep", "item_key": PROJ},
    ]})
    status = {n["id"]: (n["status"], n["reason"].split(":")[0]) for n in out["nodes"]}
    assert status == {"ghost": ("refused", "unknown_key"), "badcite": ("refused", "unresolved_cite"),
                      "nocite": ("refused", "uncited_bullet"), "pinned": ("refused", "hard_preference")}
    # Nothing executed, and the suppressed project still on the page blocks the commit.
    assert out["metrics"]["final"] == out["metrics"]["base"]
    assert not out["committed"] and [v["check"] for v in out["violations"]] == ["preferences"]
    assert _head(isolated_engine, job_id) is None

    fixed = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "drop", "op": "delete", "item_key": PROJ, "because": f"pref:{pref_id}"}]})
    assert fixed["nodes"][0]["status"] == "accepted" and fixed["committed"]


def test_an_unknown_preference_reason_is_refused(env):
    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "pref:nope"}]})
    assert out["nodes"][0]["status"] == "refused"
    assert out["nodes"][0]["reason"].startswith("unknown_preference")


def test_finalize_violations_commit_nothing_and_carry_hints(isolated_engine, env, monkeypatch):
    from harness import executor

    uid, job_id, _ = env
    over = _run(uid, {"job_id": job_id, "finalize": {"max_skills": 3, "min_skills": 0}})
    assert not over["committed"] and over["violations"][0]["check"] == "skills_cap"

    from sqlmodel import delete

    from database.models import BlockLineCache
    with Session(isolated_engine) as s:              # forget the one-line measurements
        s.exec(delete(BlockLineCache))
        s.commit()
    monkeypatch.setattr(executor, "MEASURER", lambda texts: [3 for _ in texts])
    tall = _run(uid, {"job_id": job_id})
    checks = {v["check"] for v in tall["violations"]}
    assert {"bullet_lines", "line_budget"} <= checks
    assert tall["cut_hints"] and tall["line_budget"]["over_by"] > 0
    assert _head(isolated_engine, job_id) is None


def test_without_a_latex_engine_the_budget_is_unmeasured_not_failed(env, monkeypatch):
    import agents.formatter as formatter
    from harness import executor

    uid, job_id, _ = env
    monkeypatch.setattr(executor, "MEASURER", None)
    monkeypatch.setattr(formatter, "_find_latex_engine", lambda: None)
    out = _run(uid, {"job_id": job_id})
    assert out["line_budget"] == {"status": "unmeasured"}
    assert out["metrics"]["final"]["gates"]["bullet_lines"] is None and out["committed"]


def test_a_stale_parent_is_refused_and_patch_plan_rebases(isolated_engine, env):
    from harness import tree
    from harness.executor import patch_plan

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    second = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:too junior"}]})
    assert second["error"]["code"] == "stale_parent"
    assert second["error"]["suggestions"] == [first["node_id"]]

    from harness.program import Program, program_id
    pid = program_id(Program.model_validate({"job_id": job_id, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:too junior"}]}
    ).model_dump(mode="json"))
    rebased = patch_plan(uid, pid, [{"op": "replace", "path": "/parent",
                                     "value": first["node_id"]}])
    assert rebased["committed"] and rebased["parent"] == first["node_id"]
    assert [n["parent_id"] for n in tree.history(uid, job_id)] == [None, first["node_id"]]


def test_patch_plan_errors(env):
    from harness.executor import patch_plan

    uid, job_id, _ = env
    assert patch_plan(uid, "prog_missing", [])["error"]["code"] == "not_found"
    dry = _run(uid, {"job_id": job_id}, dry_run=True)
    bad = patch_plan(uid, dry["program_id"], [{"op": "remove", "path": "/nodes/4"}])
    assert bad["error"]["code"] == "invalid_patch"
    invalid = patch_plan(uid, dry["program_id"],
                         [{"op": "add", "path": "/nodes/-", "value": {"id": "x", "op": "fly"}}])
    assert invalid["error"]["code"] == "invalid_program"


def test_a_plan_keeps_the_users_layout_overrides(isolated_engine, env):
    from harness import tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    layout = {"section_order": ["education", "projects", "experience", "skills"]}
    with Session(isolated_engine) as s:
        r = s.exec(select(UserJobResult)).one()
        r.layout_overrides = layout
        s.add(r)
        s.commit()
    drag = tree.record_result(uid, job_id, source="editor", note="layout")
    out = _run(uid, {"job_id": job_id, "parent": drag["node_id"], "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less games"}]})
    assert out["committed"] and first["committed"]
    assert tree.get_node(uid, out["node_id"])["layout_overrides"] == layout
    with Session(isolated_engine) as s:
        assert s.exec(select(UserJobResult)).one().layout_overrides == layout


def _woven(kg_bullets):
    """EXP2's source bullets, with one reworded to use a posting term the page lacks."""
    out = [{"text": b, "cites": [f"{EXP2}#b{i}"]} for i, b in enumerate(kg_bullets)]
    out[3]["text"] = "Led the migration from a monolith to an event-driven architecture using Kafka."
    return out


def test_a_clean_revise_adding_a_supported_keyword_is_kept(env):
    from harness.executor import _KG

    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "keywords": ["architecture"], "bullets": _woven(_KG(uid).source_bullets[EXP2])}]})
    node = out["nodes"][0]
    assert node["status"] == "accepted", node
    assert node["deltas"]["coverage"] > 0 and out["committed"]


def test_permuting_independent_nodes_does_not_change_the_outcome(env):
    from harness.executor import _KG

    uid, job_id, _ = env
    nodes = [
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "bullets": _woven(_KG(uid).source_bullets[EXP2])},
        {"id": "drop", "op": "delete", "item_key": PROJ, "because": "user:less games"},
        {"id": "keep", "op": "keep", "item_key": EXP},
    ]
    a = _run(uid, {"job_id": job_id, "nodes": nodes}, dry_run=True)
    b = _run(uid, {"job_id": job_id, "nodes": nodes[::-1]}, dry_run=True)
    status = lambda r: sorted((n["id"], n["status"]) for n in r["nodes"])  # noqa: E731
    assert status(a) == status(b)
    assert a["metrics"]["final"] == b["metrics"]["final"]


def test_contract_tools_are_writes():
    from harness.contract import BY_NAME

    assert not BY_NAME["execute_plan"].read_only and not BY_NAME["patch_plan"].read_only


# ── determinism: the #197 acceptance test ────────────────────────────────────

def _strip(result):
    """A result without the one field a fresh commit must differ in."""
    return {k: v for k, v in result.items() if k not in ("node_id", "committed", "dry_run")}


def test_replaying_a_program_is_byte_identical(env):
    from eval.scripted_host import build_program

    uid, job_id, task = env
    program = build_program(uid, job_id, task["description"])
    runs = [_run(uid, program, dry_run=True), _run(uid, program, dry_run=True), _run(uid, program)]
    assert runs[2]["committed"], runs[2]
    dumps = {json.dumps(_strip(r), sort_keys=True) for r in runs}
    assert len(dumps) == 1
    statuses = {n["status"] for n in runs[2]["nodes"]}
    assert {"accepted", "kept"} <= statuses          # the policy exercises the rule


def _fresh_store(tmp_path, monkeypatch, name):
    """A second, independent SQLite store, patched in like `isolated_engine`."""
    from conftest import _sqlite_engine
    import agents.chat as chat_module
    import database.db as db_module
    import database.user_utils as user_utils_module
    import knowledge_graph.builder as kg_builder_module
    import services as services_module

    path = tmp_path / name
    path.mkdir()
    engine, _ = _sqlite_engine(path)
    for mod in (db_module, chat_module, kg_builder_module, services_module, user_utils_module):
        monkeypatch.setattr(mod, "engine", engine)
    return engine


def _ids_masked(out, job_id):
    text = json.dumps({"result": _strip(out["result"]), "program": out["program"]}, sort_keys=True)
    return text.replace(job_id, "$JOB").replace(out["result"].get("program_id") or "", "$PROG")


def test_benchmark_tasks_run_end_to_end_through_a_scripted_host_and_replay(tmp_path, monkeypatch):
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import PROFILES_DIR
    from eval.scripted_host import run_task, seed_profile
    from eval.tailoring_benchmark import load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", _one_line_each)
    tasks = load_tasks(limit=5)[:3]
    runs = []
    for store in ("a", "b"):
        engine = _fresh_store(tmp_path, monkeypatch, store)
        uid = seed_profile(load_profile(PROFILES_DIR / BENCH_PROFILE))
        outs = [run_task(uid, t) for t in tasks]
        assert all(o["result"]["committed"] for o in outs), [o["result"] for o in outs]
        runs.append([_ids_masked(o, o["job_id"]) for o in outs])
        engine.dispose()
    assert runs[0] == runs[1]
