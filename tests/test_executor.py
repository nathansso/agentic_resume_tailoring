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

from database.models import JobDescription, JobHead, UserJobResult, UserPreference

BENCH_PROFILE = "benchmark_profile.md"


def _one_line_each(texts):
    return [1 for _ in texts]


@pytest.fixture()
def env(isolated_engine, monkeypatch):
    """The benchmark profile seeded as one user, one job, a one-line measurer."""
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import PROFILES_DIR
    from eval.scripted_host import open_task_job, seed_profile
    from eval.tailoring_benchmark import load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", _one_line_each)
    uid = seed_profile(load_profile(PROFILES_DIR / BENCH_PROFILE))
    task = load_tasks(limit=5)[3]
    job_id = open_task_job(uid, task)
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
    ({"id": "a", "op": "delete_bullet", "item_key": EXP}, "delete_bullet needs cut_bullets"),
    ({"id": "a", "op": "delete_bullet", "item_key": EXP, "cut_bullets": [f"{EXP2}#b1"]},
     "must be 'exp:machine learning engineer"),
    ({"id": "a", "op": "delete_bullet", "item_key": EXP, "cut_bullets": [f"{EXP}#bx"]}, "#b<n>"),
    ({"id": "a", "op": "delete_bullet", "item_key": EXP, "cut_bullets": [f"{EXP}#b1"],
      "bullets": [{"text": "x", "cites": [EXP]}]}, "takes no bullets"),
    ({"id": "a", "op": "delete", "item_key": EXP, "cut_bullets": [f"{EXP}#b1"]},
     "cut_bullets is for delete_bullet"),
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
    from harness import policy
    assert node["provenance"]["policy_version"] == policy.version() != policy.UNVERSIONED   # #241
    with Session(isolated_engine) as s:     # materialized for the web app / art ui
        result = s.exec(select(UserJobResult).where(
            UserJobResult.job_id == UUID(node["job_id"]))).one()
        assert result.tailored_resume_content == node["content"]
        assert {e["title"] for e in node["content"]["experiences"]} >= {"Machine Learning Engineer"}
        # ...and the job says it has a resume, so art ui shows the editor for it (#276).
        assert s.get(JobDescription, UUID(job_id)).status == "tailored"


def test_a_project_without_blurbs_renders_its_description_minus_a_restating_lead_in(env):
    """#260: a tagline folded into the description is not said twice on the page; a project
    with blurbs renders those, unchanged."""
    from harness.executor import _KG
    from harness.ingest import upsert_items

    uid, _, _ = env
    upsert_items(uid, [
        {"kind": "project", "data": {
            "name": "semantic-search",
            "description": "Semantic search library. Open-source semantic search library "
                           "using sentence-transformers."}},
        {"kind": "project", "data": {
            "name": "notes-index", "description": "Notes index. Notes index over markdown.",
            "blurbs": ["Indexed 40k markdown notes."]}}])
    kg = _KG(uid)
    assert kg.source_bullets["proj:semantic-search"] == [
        "Open-source semantic search library using sentence-transformers."]
    assert kg.source_bullets["proj:notes-index"] == ["Indexed 40k markdown notes."]
    assert kg.item("proj:semantic-search")["bullets"] == kg.source_bullets["proj:semantic-search"]


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


def test_a_plan_renders_the_users_dragged_order(isolated_engine, env):
    """Carrying the override is not enough: the node is rendered from its content,
    so the content must already be in the user's order (#277)."""
    from harness import tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    exp = next(e for e in _experiences(uid, first) if e["title"] == "Machine Learning Engineer")
    dragged = list(reversed(exp["bullets"]))
    layout = {"section_order": ["projects", "experience", "skills", "education"],
              "bullets": [{"section": "experience", "item": "Machine Learning Engineer",
                           "order": dragged}]}
    with Session(isolated_engine) as s:
        r = s.exec(select(UserJobResult)).one()
        r.layout_overrides = layout
        s.add(r)
        s.commit()
    drag = tree.record_result(uid, job_id, source="editor", note="layout")
    out = _run(uid, {"job_id": job_id, "parent": drag["node_id"], "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less games"}]})

    assert out["committed"]
    content = tree.get_node(uid, out["node_id"])["content"]
    assert next(e for e in content["experiences"]
                if e["title"] == "Machine Learning Engineer")["bullets"] == dragged
    assert content["_section_order"][:3] == ["projects", "experience", "skills"]


def _experiences(uid, out):
    from harness import tree
    return tree.get_node(uid, out["node_id"])["content"]["experiences"]


def _woven(kg_bullets):
    """EXP2's source bullets, with one reworded to use a posting term the page lacks.

    The job is opened through `open_job` (#192), so coverage uses real keyword
    weights: the term must be one the candidate's graph supports (weight > 0).
    """
    out = [{"text": b, "cites": [f"{EXP2}#b{i}"]} for i, b in enumerate(kg_bullets)]
    out[3]["text"] = "Led the migration from a monolith to event-driven services using Kafka to integrate billing and search."
    return out


def test_a_clean_revise_adding_a_supported_keyword_is_kept(env):
    from harness.executor import _KG

    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "keywords": ["integrate"], "bullets": _woven(_KG(uid).source_bullets[EXP2])}]})
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


# â”€â”€ a plan that changes nothing is not a version (#257) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _events(engine):
    from database.models import TreeEvent
    with Session(engine) as s:
        return len(s.exec(select(TreeEvent)).all())


def _nodes(engine):
    from database.models import TailorNode
    with Session(engine) as s:
        return len(s.exec(select(TailorNode)).all())


def test_a_plan_whose_nodes_are_all_reverted_or_refused_commits_nothing(isolated_engine, env):
    from harness import executor, tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    nodes_before, events_before = _nodes(isolated_engine), _events(isolated_engine)
    src = executor._KG(uid).source_bullets[EXP]
    out = _run(uid, {"job_id": job_id, "parent": first["node_id"], "nodes": [
        {"id": "same", "op": "revise", "item_key": EXP,           # no target can improve
         "bullets": [{"text": t, "cites": [f"{EXP}#b{i}"]} for i, t in enumerate(src)]},
        {"id": "ghost", "op": "revise", "item_key": "exp:astronaut|nasa",
         "bullets": [{"text": "Flew", "cites": ["exp:astronaut|nasa"]}]},
        {"id": "keep", "op": "keep", "item_key": EXP2},
    ]})
    assert out.get("error") is None and out["committed"] is False and out["node_id"] is None
    assert out["reason"] == executor.NO_CHANGE and out["reason"].startswith("no_change")
    assert out["violations"] == [] and out["parent"] == first["node_id"]
    status = {n["id"]: n["status"] for n in out["nodes"]}      # the host still learns why
    assert status == {"same": "reverted", "ghost": "refused", "keep": "kept"}
    assert all(n["reason"] for n in out["nodes"] if n["status"] != "kept")
    assert _nodes(isolated_engine) == nodes_before and _events(isolated_engine) == events_before
    assert _head(isolated_engine, job_id) == first["node_id"]
    assert [e["kind"] for e in tree.events_since(uid, 0, job_id)] == ["commit"]


def test_an_empty_plan_on_a_parent_commits_nothing_and_a_dry_run_says_so(isolated_engine, env):
    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    again = _run(uid, {"job_id": job_id, "parent": first["node_id"]})
    dry = _run(uid, {"job_id": job_id, "parent": first["node_id"]}, dry_run=True)
    assert not again["committed"] and again["reason"].startswith("no_change")
    assert dry["dry_run"] and not dry["committed"] and dry["reason"].startswith("no_change")
    assert _nodes(isolated_engine) == 1 and _events(isolated_engine) == 1


def test_an_empty_first_plan_still_commits_the_baseline_copy(isolated_engine, env):
    """The art-tailor skill commits a job's track baseline with `parent: null, nodes: []`."""
    from harness import tree

    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "parent": None, "nodes": []})
    assert out["committed"] and out["node_id"] and "reason" not in out
    assert tree.get_node(uid, out["node_id"])["parent_id"] is None
    assert _head(isolated_engine, job_id) == out["node_id"]
    assert _nodes(isolated_engine) == 1 and _events(isolated_engine) == 1


def test_a_skills_reranking_alone_still_commits(isolated_engine, env):
    from harness import tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    names = [s["name"] for s in tree.get_node(uid, first["node_id"])["content"]["skills_ranked"]]
    assert len(names) > 1
    out = _run(uid, {"job_id": job_id, "parent": first["node_id"], "skills": names[::-1]})
    assert out["committed"] and "reason" not in out
    got = [s["name"] for s in tree.get_node(uid, out["node_id"])["content"]["skills_ranked"]]
    assert got == names[::-1]
    assert _nodes(isolated_engine) == 2 and _events(isolated_engine) == 2


def test_a_node_that_changes_the_page_still_commits(isolated_engine, env):
    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    out = _run(uid, {"job_id": job_id, "parent": first["node_id"], "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less games"}]})
    assert out["committed"] and out["nodes"][0]["status"] == "accepted"


def test_a_no_change_plan_leaves_the_parents_hand_edits_alone(isolated_engine, env):
    """A plan commit carries layout overrides but not a hand-edited .tex; skipping the no-op
    keeps the editor's version as HEAD instead of replacing it with one that drops the .tex."""
    from harness import tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    with Session(isolated_engine) as s:
        r = s.exec(select(UserJobResult)).one()
        r.edited_tex = "\\documentclass{article}\\begin{document}by hand\\end{document}"
        s.add(r)
        s.commit()
    edit = tree.record_result(uid, job_id, source="editor", note="tex")
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"]})
    assert not out["committed"] and out["reason"].startswith("no_change")
    assert _head(isolated_engine, job_id) == edit["node_id"] != first["node_id"]


# ── a commit never drops the user's hand-edited .tex silently (#278) ─────────

USER_TEXT = "Built and deployed ranking models serving 2M daily predictions with PyTorch."
DROP_PROJ = {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less games"}


def _hand_edit(engine, uid, job_id, change, layout=None):
    """The editor saving a hand-edited .tex over HEAD (and a layout override with it, as a drag
    does). Returns the editor node."""
    from agents.formatter import _convert_inline
    from harness import texdiff, tree

    head = tree.get_head(uid, job_id)["head"]
    tex = texdiff.node_tex(uid, {**head, "layout_overrides": layout or head["layout_overrides"]})
    edited = change(tex, _convert_inline)
    with Session(engine) as s:
        r = s.exec(select(UserJobResult)).one()
        r.edited_tex = edited
        if layout:
            r.layout_overrides = layout
        s.add(r)
        s.commit()
    return tree.record_result(uid, job_id, source="editor", note="tex edit")


@pytest.fixture()
def edited(isolated_engine, env):
    """A job whose HEAD is the user's hand edit of the first ML-engineer bullet."""
    from harness import executor

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]
    edit = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex.replace(
        r"\resumeItem{" + esc(src[0]) + "}", r"\resumeItem{" + esc(USER_TEXT) + "}"))
    return uid, job_id, first, edit, src


def test_a_commit_over_a_tex_edit_is_refused_and_names_the_edited_bullets(isolated_engine, edited):
    uid, job_id, first, edit, src = edited
    nodes_before = _nodes(isolated_engine)
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ]})

    assert out["committed"] is False and out["node_id"] is None
    [v] = out["violations"]
    assert v["check"] == "discards_editor_edits" and "discard_editor_edits" in v["hint"]
    assert v["detail"]["bullets"] == [{
        "item": EXP, "title": "Machine Learning Engineer @ Nimbus Analytics", "change": "revised",
        "bullet": f"{EXP}#b0", "before": src[0], "after": USER_TEXT, "position": 0}]
    assert _nodes(isolated_engine) == nodes_before and _head(isolated_engine, job_id) == edit["node_id"]
    again = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ]}, dry_run=True)
    assert [v["check"] for v in again["violations"]] == ["discards_editor_edits"]   # a dry run says so too


def test_carrying_the_edit_over_as_a_node_lets_the_commit_through(isolated_engine, edited):
    from harness import tree

    uid, job_id, first, edit, src = edited
    bullets = [{"text": USER_TEXT, "cites": [f"{EXP}#b0"]}] + [
        {"text": t, "cites": [f"{EXP}#b{i}"]} for i, t in enumerate(src) if i]
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [
        {"id": "user-edit", "op": "revise", "item_key": EXP, "bullets": bullets,
         "accept": {"improves": []}}]})

    assert out["nodes"][0]["status"] == "accepted", out["nodes"]
    assert out["violations"] == [] and out["committed"] and "discarded_editor_edits" not in out
    node = tree.get_node(uid, out["node_id"])
    assert node["edited_tex"] is None
    assert next(e for e in node["content"]["experiences"] if e["title"] == "Machine Learning Engineer"
                )["bullets"][0] == USER_TEXT


def test_a_partly_carried_edit_is_still_refused_for_the_rest(isolated_engine, env):
    """Two edits: a reworded bullet and a removed one. Carrying the first leaves the second named;
    a delete_bullet for it clears the refusal."""
    from harness import executor

    uid, job_id, _ = env
    _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]

    def change(tex, esc):
        tex = tex.replace(r"\resumeItem{" + esc(src[0]) + "}", r"\resumeItem{" + esc(USER_TEXT) + "}")
        return tex.replace("        \\resumeItem{" + esc(src[1]) + "}\n", "")
    edit = _hand_edit(isolated_engine, uid, job_id, change)
    revise = {"id": "r", "op": "revise", "item_key": EXP, "accept": {"improves": []},
              "bullets": [{"text": USER_TEXT, "cites": [f"{EXP}#b0"]}] + [
                  {"text": t, "cites": [f"{EXP}#b{i}"]} for i, t in enumerate(src) if i]}
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [revise]})
    [v] = out["violations"]
    assert [(b["change"], b["bullet"]) for b in v["detail"]["bullets"]] == [("removed", f"{EXP}#b1")]

    cut = {"id": "c", "op": "delete_bullet", "item_key": EXP, "cut_bullets": [f"{EXP}#b1"],
           "because": "user:removed it in the editor"}
    done = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [cut]})
    [v] = done["violations"]                      # the reworded bullet is not carried by this plan
    assert [(b["change"], b["bullet"]) for b in v["detail"]["bullets"]] == [("revised", f"{EXP}#b0")]


def test_an_explicit_discard_commits_and_says_what_was_dropped(isolated_engine, edited):
    from harness import tree

    uid, job_id, first, edit, src = edited
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ],
                     "discard_editor_edits": True})

    assert out["committed"] and out["violations"] == []
    [dropped] = out["discarded_editor_edits"]["bullets"]
    assert (dropped["bullet"], dropped["before"], dropped["after"]) == (f"{EXP}#b0", src[0], USER_TEXT)
    node = tree.get_node(uid, out["node_id"])
    assert node["edited_tex"] is None and node["parent_id"] == edit["node_id"]
    assert USER_TEXT not in str(node["content"])
    with Session(isolated_engine) as s:
        assert s.exec(select(UserJobResult)).one().edited_tex is None
    # With nothing to discard the flag changes nothing and reports nothing.
    plain = _run(uid, {"job_id": job_id, "parent": out["node_id"], "discard_editor_edits": True,
                       "nodes": [{"id": "k", "op": "keep", "item_key": EXP2}]})
    assert "discarded_editor_edits" not in plain


def test_an_edit_outside_the_bullets_cannot_be_carried_only_discarded(isolated_engine, env):
    uid, job_id, _ = env
    _run(uid, {"job_id": job_id})
    edit = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex.replace(
        "\\begin{center}", "\\begin{center}\n    \\small Phone 555-0100", 1))
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ]})
    [v] = out["violations"]
    assert v["detail"]["bullets"] == [] and v["detail"]["unmapped"][0]["region"] == "header"
    assert "Phone 555-0100" in v["detail"]["unmapped"][0]["diff"]
    ok = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ],
                    "discard_editor_edits": True})
    assert ok["committed"] and ok["discarded_editor_edits"]["unmapped"][0]["region"] == "header"


def test_a_whitespace_only_tex_save_is_not_an_edit_to_protect(isolated_engine, env):
    uid, job_id, _ = env
    _run(uid, {"job_id": job_id})
    edit = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex + "\n\n")
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ]})
    assert out["committed"] and out["violations"] == []


def test_a_no_change_plan_over_a_tex_edit_is_not_refused(isolated_engine, edited):
    uid, job_id, first, edit, src = edited
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"]})
    assert out["violations"] == [] and out["reason"].startswith("no_change")


def test_a_commit_over_an_editor_drag_is_unaffected(isolated_engine, env):
    """A drag saves the reordered .tex and a layout override together. The override is applied to
    the new version, so nothing the drag did is dropped and nothing is refused."""
    from harness import tree

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    exp = next(e for e in _experiences(uid, first) if e["title"] == "Machine Learning Engineer")
    dragged = list(reversed(exp["bullets"]))
    layout = {"section_order": ["projects", "experience", "skills", "education"],
              "bullets": [{"section": "experience", "item": "Machine Learning Engineer",
                           "order": dragged}]}
    drag = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex, layout=layout)
    assert tree.get_node(uid, drag["node_id"])["edited_tex"]          # the tex came along

    out = _run(uid, {"job_id": job_id, "parent": drag["node_id"], "nodes": [DROP_PROJ]})
    assert out["committed"] and out["violations"] == [] and "discarded_editor_edits" not in out
    content = tree.get_node(uid, out["node_id"])["content"]
    assert next(e for e in content["experiences"]
                if e["title"] == "Machine Learning Engineer")["bullets"] == dragged


def test_a_hand_reorder_without_an_override_is_not_dropped_silently(isolated_engine, env):
    """Moving bullets in the text alone leaves nothing for the new version to apply."""
    from harness import executor

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]

    def swap(tex, esc):
        a, b = (f"        \\resumeItem{{{esc(src[i])}}}\n" for i in (0, 1))
        return tex.replace(a + b, b + a)
    edit = _hand_edit(isolated_engine, uid, job_id, swap)
    out = _run(uid, {"job_id": job_id, "parent": edit["node_id"], "nodes": [DROP_PROJ]})
    [v] = out["violations"]
    assert [r["item"] for r in v["detail"]["reordered"]] == [EXP] and v["detail"]["bullets"] == []


# ── the user's own edit, carried over verbatim, is not vetoed by a guard (#306) ──

# What a user types is not what an optimiser writes: short, repetitive, off the posting. It
# lowers MTLD and the posting's keyword density, and an ordinary revise of it is reverted.
USER_PLAIN = "Worked on the team and the team worked on the work of the team."
USER_UNSUPPORTED = "Cut ranking latency by 47% across 9 regions."


def _carry_nodes(src, text, cites=None, drop_first=True):
    """A revise of EXP that puts `text` first and keeps the other source bullets, citing them."""
    first = {"text": text, "cites": cites or [f"{EXP}#b0"]}
    return [{"id": "user-edit", "op": "revise", "item_key": EXP, "accept": {"improves": []},
             "bullets": [first] + [{"text": t, "cites": [f"{EXP}#b{i}"]}
                                   for i, t in enumerate(src) if i or not drop_first]}]


def _edited_with(isolated_engine, env, text):
    """A job whose HEAD is the user's hand edit of the first ML-engineer bullet to `text`."""
    from harness import executor

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]
    edit = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex.replace(
        r"\resumeItem{" + esc(src[0]) + "}", r"\resumeItem{" + esc(text) + "}"))
    return uid, job_id, first, edit, src


def _plan(job_id, edit, nodes, **kw):
    return {"job_id": job_id, "parent": edit["node_id"], "nodes": nodes, **kw}


def test_a_verbatim_carry_over_commits_despite_a_guard_regression_and_reports_it(
        isolated_engine, env):
    from harness import tree

    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, USER_PLAIN)))

    [node] = out["nodes"]
    assert node["status"] == "accepted", node
    assert node["reason"].startswith("guard_waived: carried editor edit")
    assert set(node["waived"]) == {"mtld", "relevance_density"}, node     # a guard and a target
    assert node["carried_edit"] == [{"change": "revised", "bullet": f"{EXP}#b0", "after": USER_PLAIN}]
    assert out["committed"] and out["violations"] == [] and "discarded_editor_edits" not in out
    assert USER_PLAIN in str(tree.get_node(uid, out["node_id"])["content"])


def test_the_same_text_with_no_tex_edit_behind_it_is_an_ordinary_revise(isolated_engine, env):
    """No hand edit to verify against (a label cannot stand in for one): every guard applies."""
    from harness import executor

    uid, job_id, _ = env
    first = _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]
    out = _run(uid, {"job_id": job_id, "parent": first["node_id"],
                     "nodes": _carry_nodes(src, USER_PLAIN)})
    [node] = out["nodes"]
    assert node["status"] == "reverted" and "carried_edit" not in node and not node.get("waived")
    assert node["reason"].startswith(("guard:", "no_target_improved"))


@pytest.mark.parametrize("change", [
    pytest.param(lambda t: t + " Also this.", id="a word more"),
    pytest.param(lambda t: t.replace("Worked", "Labored"), id="a word swapped"),
    pytest.param(lambda t: t.rstrip("."), id="a trailing period"),
])
def test_text_that_differs_from_the_edit_even_slightly_faces_every_guard(
        isolated_engine, env, change):
    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, change(USER_PLAIN))))
    [node] = out["nodes"]
    assert node["status"] == "reverted" and "carried_edit" not in node and not node.get("waived")
    assert node["reason"].startswith(("guard:", "no_target_improved"))
    assert not out["committed"]


def test_whitespace_is_the_only_difference_a_carry_over_forgives(isolated_engine, env):
    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    spaced = "  " + USER_PLAIN.replace(" and ", "   and\n ") + " "
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, spaced)))
    assert out["nodes"][0]["status"] == "accepted" and out["nodes"][0]["carried_edit"]


def test_a_revise_that_carries_the_edit_but_also_rewrites_another_bullet_is_ordinary(
        isolated_engine, env):
    """One verbatim bullet must not carry a waiver for text the user never wrote."""
    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    nodes = _carry_nodes(src, USER_PLAIN)
    nodes[0]["bullets"][1]["text"] = "Worked on the team and the team worked on the work again."
    nodes2 = _carry_nodes(src, USER_PLAIN)
    del nodes2[0]["bullets"][1]                           # or silently drops one the user kept
    for plan_nodes in (nodes, nodes2):
        out = _run(uid, _plan(job_id, edit, plan_nodes))
        [node] = out["nodes"]
        assert "carried_edit" not in node and not node.get("waived"), node
        assert node["status"] == "reverted" and not out["committed"]


def test_a_carried_edit_that_breaks_a_hard_gate_is_refused_with_an_actionable_reason(
        isolated_engine, env):
    from harness import tree

    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_UNSUPPORTED)
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, USER_UNSUPPORTED)))

    [node] = out["nodes"]
    assert node["status"] == "reverted" and not out["committed"]
    reason = node["reason"]
    assert reason.startswith("hard_gate: consistency") and "47%" in reason     # the gate and the claim
    assert "upsert_items" in reason and "ask the user" in reason and "retry" in reason
    assert "Do not reword" in reason
    assert not node.get("waived")                         # nothing is waived on a refused node
    assert tree.get_head(uid, job_id)["head"]["node_id"] == edit["node_id"]
    assert out["reason"].startswith("no_change")          # the edit stayed unreflected; nothing moved


def test_a_carried_edit_that_cites_nothing_real_names_the_citation_gate(isolated_engine, env):
    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, USER_PLAIN, cites=[f"{EXP}#b99"])))
    [node] = out["nodes"]
    assert node["status"] == "refused" and node["reason"].startswith("unresolved_cite")


def test_an_edited_text_that_a_hard_preference_forbids_is_still_refused(isolated_engine, env):
    uid, job_id, _ = env
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=uid, text="Never mention the work of the team",
                             polarity="suppress", target_term="work of the team",
                             scope_type="global", strength=5))
        s.commit()
    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    out = _run(uid, _plan(job_id, edit, _carry_nodes(src, USER_PLAIN)))
    [node] = out["nodes"]
    assert node["status"] == "refused" and node["reason"].startswith("negative_pin")
    assert not out["committed"] and not node.get("waived")


def test_the_edit_loop_runs_end_to_end_from_diff_nodes_to_a_commit(isolated_engine, env):
    """editor edit -> diff_nodes.tex_edits -> a revise built from it -> commit, no discard flag."""
    from harness import tree
    from harness.contract import invoke

    uid, job_id, first, edit, src = _edited_with(isolated_engine, env, USER_PLAIN)
    diff = invoke("diff_nodes", uid, {"from_node": first["node_id"], "to_node": edit["node_id"]})
    [e] = diff["tex_edits"]["bullets"]
    assert (e["change"], e["bullet"], e["after"]) == ("revised", f"{EXP}#b0", USER_PLAIN)

    # The host reads the edit and carries it over: the edited bullet's text, the rest as they were.
    n = int(e["bullet"].rpartition("#b")[2])
    bullets = [{"text": e["after"] if i == n else t, "cites": [f"{EXP}#b{i}"]}
               for i, t in enumerate(src)]
    out = _run(uid, _plan(job_id, edit, [{"id": "user-edit", "op": "revise", "item_key": EXP,
                                          "bullets": bullets, "accept": {"improves": []}}]))
    assert out["committed"] and out["violations"] == [] and "discarded_editor_edits" not in out
    assert out["nodes"][0]["carried_edit"][0]["bullet"] == e["bullet"]
    node = tree.get_node(uid, out["node_id"])
    assert node["edited_tex"] is None and node["parent_id"] == edit["node_id"]
    assert next(x for x in node["content"]["experiences"]
                if x["title"] == "Machine Learning Engineer")["bullets"][n] == USER_PLAIN


def test_the_acceptance_rule_waives_guards_and_a_fallen_target_for_a_carried_edit_only():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting")
    before = metric_vector(_content(BASE), ctx)
    before["guards"]["mtld"] = 80.0
    before["targets"]["relevance_density"] = 0.4
    after = copy.deepcopy(before)
    after["guards"]["mtld"] = 40.0
    after["targets"]["relevance_density"] = 0.3
    assert not accept(before, after)["accepted"]
    assert not accept(before, after, requested=True)["waived"].get("relevance_density")
    verdict = accept(before, after, carried=True)
    assert verdict["accepted"] and verdict["reason"].startswith("guard_waived: carried editor edit")
    assert verdict["waived"] == {"mtld": 0.5, "relevance_density": 0.1}
    # A target that fell while another rose would not have vetoed, so it is not "waived".
    after["targets"]["coverage"] = before["targets"]["coverage"] + 0.2
    assert set(accept(before, after, carried=True)["waived"]) == {"mtld"}
    # Nothing regressed and nothing improved: a carried edit owes the posting nothing.
    same = accept(before, copy.deepcopy(before), carried=True)
    assert same["accepted"] and same["waived"] == {} and same["reason"] is None
    # Hard gates apply in full.
    ctx2 = Context(jd_text="retail demand forecasting", hard_suppress={"proj:tool"})
    gated = accept(metric_vector(_content(BASE), Context(jd_text="x")),
                   metric_vector(_content(BASE), ctx2), carried=True)
    assert not gated["accepted"] and gated["reason"].startswith("hard_gate: preferences")
    assert gated["waived"] == {}


def test_carried_by_accounts_for_every_change_from_the_users_edits_and_nothing_else():
    from harness.texdiff import carried_by

    k = "exp:a|b"
    edits = {"bullets": [
        {"item": k, "change": "revised", "bullet": f"{k}#b0", "before": "old one", "after": "new  one"},
        {"item": k, "change": "removed", "bullet": f"{k}#b2", "before": "old three", "after": None},
        {"item": k, "change": "added", "bullet": None, "before": None, "after": "added four"},
        {"item": "exp:other|x", "change": "revised", "bullet": "exp:other|x#b0",
         "before": "o", "after": "other text"}]}
    page = ["old one", "old two", "old three"]
    ok = carried_by(edits, k, page, ["new one", "old two", "added four"])
    assert [e["change"] for e in ok] == ["revised", "added"]
    assert carried_by(edits, k, page, ["old two", "new one"])                  # a removal and a revise
    assert carried_by(edits, k, page, ["old two", "added four", "new one"])    # in any order
    assert carried_by(edits, k, page, ["old one", "old two", "old three"]) is None   # no new text
    assert carried_by(edits, k, page, ["new one!", "old two", "old three"]) is None  # reworded
    assert carried_by(edits, k, page, ["new one", "old three"]) is None        # drops "old two"
    assert carried_by(edits, k, page, ["new one", "old two", "old three", "extra"]) is None
    assert carried_by(edits, k, page, ["other text", "old two", "old three"]) is None   # another item's
    assert carried_by(None, k, page, ["new one"]) is None and carried_by({}, k, page, ["x"]) is None


def test_an_added_bullet_in_the_tex_is_carried_the_same_way(isolated_engine, env):
    uid, job_id, _ = env
    from harness import executor

    first = _run(uid, {"job_id": job_id})
    src = executor._KG(uid).source_bullets[EXP]
    edit = _hand_edit(isolated_engine, uid, job_id, lambda tex, esc: tex.replace(
        "        \\resumeItem{" + esc(src[-1]) + "}\n",
        "        \\resumeItem{" + esc(src[-1]) + "}\n        \\resumeItem{" + esc(USER_PLAIN) + "}\n"))
    bullets = [{"text": t, "cites": [f"{EXP}#b{i}"]} for i, t in enumerate(src)] + [
        {"text": USER_PLAIN, "cites": [f"{EXP}#b0"]}]
    out = _run(uid, _plan(job_id, edit, [{"id": "u", "op": "revise", "item_key": EXP,
                                          "bullets": bullets, "accept": {"improves": []}}]))
    [node] = out["nodes"]
    assert node["status"] == "accepted", node
    assert node["carried_edit"] == [{"change": "added", "bullet": None, "after": USER_PLAIN}]
    assert out["committed"] and out["violations"] == []


# ── a requested delete is not vetoed by a guard (#279) ───────────────────────

def _mtld_down(vector, by=0.30):
    """`vector` with MTLD lower by a fraction, as any delete lowers it."""
    out = copy.deepcopy(vector)
    out["guards"]["mtld"] = round(out["guards"]["mtld"] * (1 - by), 4)
    return out


def test_a_requested_delete_that_only_regresses_mtld_is_accepted_and_reports_it():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting")
    before = metric_vector(_content(BASE), ctx)
    before["guards"]["mtld"] = 80.0                  # a short fixture has none (None)
    after = _mtld_down(before)
    assert not accept(before, after, improves=["coverage"])["accepted"]       # an edit is still vetoed
    verdict = accept(before, after, requested=True)
    assert verdict["accepted"] and verdict["gate_violations"] == {}
    assert verdict["guard_regressions"] == verdict["waived"] and set(verdict["waived"]) == {"mtld"}
    assert verdict["reason"].startswith("guard_waived") and "mtld" in verdict["reason"]
    assert accept(before, copy.deepcopy(before), requested=True)["waived"] == {}   # nothing to waive


def test_a_requested_delete_that_breaks_a_hard_gate_is_still_refused():
    from harness.acceptance import Context, accept, metric_vector

    ctx = Context(jd_text="retail demand forecasting", hard_emphasize={"exp:engineer|acme"})
    before = metric_vector(_content(BASE), ctx)
    emptied = _content(BASE)
    emptied["experiences"] = []
    after = metric_vector(emptied, ctx)
    before["guards"]["mtld"] = 80.0                     # a short fixture has none (None)
    after["guards"]["mtld"] = 40.0                      # and a guard regresses too
    verdict = accept(before, after, requested=True)
    assert not verdict["accepted"] and verdict["reason"].startswith("hard_gate: preferences")
    assert verdict["waived"] == {}                      # nothing is waived on a refused node


def _spread_profile(exp_bullets=0, extra_projects=5):
    """The benchmark profile cut to two bullets an item (the floor `cut_hints` respects), plus
    five projects of varied wording: every `cut_hints` entry is a `drop_item`, and cutting a
    varied project lowers the page's MTLD by more than the 10% tolerance. With `exp_bullets`,
    each experience instead carries that many varied bullets, so `cut_bullet` hints come first
    (#289)."""
    from eval.profile_fixture import load_profile
    from eval.tailoring_benchmark import PROFILES_DIR

    words = ("alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike "
             "november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee "
             "zulu amber basil cedar dune ember fjord glade harbor iris jade koala lotus maple "
             "nectar olive pearl quartz ridge sable tundra umber velvet willow yarrow zephyr").split()

    def varied(i, j):
        return "Built " + " ".join(words[(i * 7 + j * 5 + k * 3) % len(words)] for k in range(9)) + " service"

    profile = load_profile(PROFILES_DIR / BENCH_PROFILE)
    for item in (*profile.experiences, *profile.projects):
        item["bullets"] = item["bullets"][:2]
    for n, item in enumerate(profile.experiences if exp_bullets else ()):
        # The first two bullets repeat each other's words; the later ones, the ones `cut_hints`
        # cut, are varied, so cutting them lowers the page's MTLD.
        item["bullets"] = [f"Built reporting dashboards for weekly service reviews {j}" if j < 2
                           else varied(20 + n, j) for j in range(exp_bullets)]
    profile.projects += [{"name": f"Tool {words[i]}", "description": None, "repo_url": None,
                          "bullets": [varied(i, 0), varied(i, 1)]} for i in range(extra_projects)]
    return profile


def test_cut_hints_and_the_acceptance_rule_agree_end_to_end(isolated_engine, monkeypatch):
    """The #279 run: an over-budget page, `cut_hints` recommending `drop_item`s, the host sending
    them as requested deletes. Each used to come back `reverted` (`guard: mtld regressed`),
    so the budget stayed violated and nothing committed."""
    from eval.scripted_host import open_task_job, seed_profile
    from eval.tailoring_benchmark import load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", lambda texts: [2 for _ in texts])
    uid = seed_profile(_spread_profile())
    job_id = open_task_job(uid, load_tasks(limit=5)[3])
    finalize = {"min_skills": 0}

    over = _run(uid, {"job_id": job_id, "finalize": finalize}, dry_run=True)
    assert not over["committed"] and over["line_budget"]["over_by"] > 0
    drops = [h["item"] for h in over["cut_hints"]]
    assert drops and {h["action"] for h in over["cut_hints"]} == {"drop_item"}

    nodes = [{"id": f"d{i}", "op": "delete", "item_key": key, "because": "user:fewer projects"}
             for i, key in enumerate(drops)]
    out = _run(uid, {"job_id": job_id, "finalize": finalize, "nodes": nodes})
    assert out.get("error") is None
    assert [n["status"] for n in out["nodes"]] == ["accepted"] * len(drops), out["nodes"]
    waived = [n for n in out["nodes"] if n.get("waived")]
    assert any("mtld" in n["waived"] for n in waived), "the scenario must exercise the MTLD guard"
    assert all(n["reason"].startswith("guard_waived") for n in waived)
    assert out["violations"] == [] and out["line_budget"]["over_by"] <= 0 and out["committed"]
    assert _head(isolated_engine, job_id) == out["node_id"]


def test_a_requested_revise_that_stuffs_is_still_reverted(env):
    """`because` waives guards for a delete only: an action that writes text faces every guard."""
    from harness.executor import _KG

    uid, job_id, _ = env
    src = _KG(uid).source_bullets[EXP2]
    bullets = [{"text": f"{b.rstrip('.')} using Kafka and Redis and Airflow", "cites": [f"{EXP2}#b{i}"]}
               for i, b in enumerate(src)]
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "stuff", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "bullets": bullets, "because": "user:work in more of the posting"}]}, dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and "waived" not in node, node
    assert node["reason"].startswith(("guard:", "hard_gate:")), node


# ── bullet removal is a delete, not a revise (#289) ──────────────────────────

def _wide_env(monkeypatch):
    """Four experiences of four varied bullets and projects of two, at two rendered lines a
    bullet: over the page budget, with `cut_bullet` hints ahead of the `drop_item`s."""
    from eval.scripted_host import open_task_job, seed_profile
    from eval.tailoring_benchmark import load_tasks
    from harness import executor

    monkeypatch.setattr(executor, "MEASURER", lambda texts: [2 for _ in texts])
    uid = seed_profile(_spread_profile(exp_bullets=4, extra_projects=0))
    return uid, open_task_job(uid, load_tasks(limit=5)[3]), {"min_skills": 0}


def _cut_index(bullet_id):
    return int(bullet_id.rpartition("#b")[2])


def _nodes_from_hints(hints, because="user:fit the one-page budget"):
    """What the art-tailor skill tells a host to do: each hint carries its op; the cut bullets
    of one item share one `delete_bullet` node (a plan holds one node per item)."""
    cuts, nodes = {}, []
    for h in hints:
        if h["op"] == "delete_bullet":
            cuts.setdefault(h["item"], []).append(h["bullet"])
        else:
            nodes.append({"id": f"drop:{h['item']}", "op": h["op"], "item_key": h["item"],
                          "because": because})
    nodes += [{"id": f"cut:{key}", "op": "delete_bullet", "item_key": key, "cut_bullets": ids,
               "because": because} for key, ids in cuts.items()]
    return nodes


def test_a_host_following_cut_hints_commits_with_the_budget_met(isolated_engine, monkeypatch):
    """The #289 run: over budget, `cut_hints` leading with `cut_bullet`s. Sent as a `revise` each
    came back `reverted` (`guard: mtld regressed`) and nothing committed. Sent as the
    `delete_bullet` the hint names, every hint kind is accepted, the survivors are verbatim and
    the page fits."""
    from harness import tree
    from harness.executor import _KG

    uid, job_id, finalize = _wide_env(monkeypatch)
    hints = _run(uid, {"job_id": job_id, "finalize": finalize}, dry_run=True)["cut_hints"]
    assert {h["action"] for h in hints} == {"cut_bullet", "drop_item"}
    assert {h["op"] for h in hints} == {"delete_bullet", "delete"} and hints[0]["action"] == "cut_bullet"
    per_item = [h["item"] for h in hints if h["action"] == "cut_bullet"]
    assert len(per_item) > len(set(per_item)), "an item must lose more than one bullet"

    out = _run(uid, {"job_id": job_id, "finalize": finalize, "nodes": _nodes_from_hints(hints)})
    assert out.get("error") is None
    assert {n["status"] for n in out["nodes"]} == {"accepted"}, out["nodes"]
    assert out["violations"] == [] and out["line_budget"]["over_by"] <= 0 and out["committed"]
    assert _head(isolated_engine, job_id) == out["node_id"]

    page = tree.get_node(uid, out["node_id"])["content"]
    src = _KG(uid).source_bullets
    for item in page["experiences"]:
        key = f"exp:{item['title']}|{item['company']}".lower()
        cut = {_cut_index(h["bullet"]) for h in hints if h["item"] == key and h["action"] == "cut_bullet"}
        assert item["bullets"] == [b for n, b in enumerate(src[key]) if n not in cut], key
    dropped = {h["item"] for h in hints if h["action"] == "drop_item"}
    assert dropped and not dropped & {f"proj:{p['name']}".lower() for p in page["projects"]}


def test_the_same_cuts_sent_as_a_revise_are_still_reverted_by_a_guard(isolated_engine, monkeypatch):
    """`revise` keeps facing every guard, whatever its `because`: only the delete forms are waived."""
    from harness.executor import _KG

    uid, job_id, finalize = _wide_env(monkeypatch)
    hints = _run(uid, {"job_id": job_id, "finalize": finalize}, dry_run=True)["cut_hints"]
    src = _KG(uid).source_bullets
    nodes = []
    for node in _nodes_from_hints(hints):
        if node["op"] != "delete_bullet":
            continue
        key, cut = node["item_key"], {_cut_index(r) for r in node["cut_bullets"]}
        nodes.append({"id": node["id"], "op": "revise", "item_key": key, "strategy": "tighten",
                      "because": node["because"],
                      "bullets": [{"text": b, "cites": [f"{key}#b{n}"]}
                                  for n, b in enumerate(src[key]) if n not in cut]})
    out = _run(uid, {"job_id": job_id, "finalize": finalize, "nodes": nodes}, dry_run=True)
    assert all(n["status"] == "reverted" and "waived" not in n for n in out["nodes"]), out["nodes"]
    assert any(n["reason"].startswith("guard:") for n in out["nodes"]), out["nodes"]
    assert not out["committed"] and out["line_budget"]["over_by"] > 0


def test_a_delete_bullet_that_regresses_a_guard_is_accepted_and_reports_it(isolated_engine, monkeypatch):
    uid, job_id, finalize = _wide_env(monkeypatch)
    hints = _run(uid, {"job_id": job_id, "finalize": finalize}, dry_run=True)["cut_hints"]
    out = _run(uid, {"job_id": job_id, "finalize": finalize, "nodes": _nodes_from_hints(hints)},
               dry_run=True)
    waived = [n for n in out["nodes"] if n.get("waived")]
    assert any(n["op"] == "delete_bullet" for n in waived), "the scenario must exercise a guard"
    assert all(n["reason"].startswith("guard_waived") for n in waived)


def test_a_revise_that_removes_a_bullet_and_adds_text_faces_every_guard(env):
    """Bullet removal plus a rewrite is a revise: the waiver is for whole-bullet deletes only."""
    from harness.executor import _KG

    uid, job_id, _ = env
    src = _KG(uid).source_bullets[EXP2]
    bullets = [{"text": f"{b.rstrip('.')} using Kafka and Redis and Airflow", "cites": [f"{EXP2}#b{i}"]}
               for i, b in list(enumerate(src))[:-1]]
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "r", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "bullets": bullets, "because": "user:fit the one-page budget"}]}, dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and "waived" not in node, node
    assert node["reason"].startswith(("guard:", "hard_gate:")), node


def test_delete_bullet_removes_only_what_it_names_and_cites_follow(env):
    from harness import tree
    from harness.executor import _KG

    uid, job_id, _ = env
    src = _KG(uid).source_bullets[EXP]
    assert len(src) >= 3
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "c", "op": "delete_bullet", "item_key": EXP, "because": "user:too long",
         "cut_bullets": [f"{EXP}#b1"]}]})
    assert out["nodes"][0]["status"] == "accepted" and out["committed"]
    item = next(i for i in tree.get_node(uid, out["node_id"])["content"]["experiences"]
                if i["title"] == "Machine Learning Engineer")
    assert item["bullets"] == [b for n, b in enumerate(src) if n != 1]
    assert set(item["cites"]) == set(item["bullets"])


@pytest.mark.parametrize("cut,expect", [
    ([f"{EXP}#b99"], "unknown_bullet"),
    ([f"{EXP}#b0", f"{EXP}#b1", f"{EXP}#b2", f"{EXP}#b3"], "would_empty_item"),
])
def test_delete_bullet_is_refused_when_it_names_nothing_or_leaves_nothing(env, cut, expect):
    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "c", "op": "delete_bullet", "item_key": EXP, "because": "user:x", "cut_bullets": cut}]},
        dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith(expect), node


def test_a_delete_bullet_with_no_reason_is_judged_like_any_edit(env):
    """No `because`, no waiver: the node must improve a target and no guard may regress."""
    uid, job_id, _ = env
    out = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "c", "op": "delete_bullet", "item_key": EXP, "cut_bullets": [f"{EXP}#b3"]}]},
        dry_run=True)
    node = out["nodes"][0]
    assert "waived" not in node and not (node["reason"] or "").startswith("guard_waived"), node


def test_patch_plan_applies_cut_hints_as_delete_bullet_nodes(isolated_engine, monkeypatch):
    from harness.executor import patch_plan

    uid, job_id, finalize = _wide_env(monkeypatch)
    over = _run(uid, {"job_id": job_id, "finalize": finalize}, dry_run=True)
    out = patch_plan(uid, over["program_id"], [{"op": "add", "path": "/nodes/-", "value": n}
                                               for n in _nodes_from_hints(over["cut_hints"])])
    assert out["committed"] and out["violations"] == []


# ── preference scope: shown and enforced alike (#291) ────────────────────────

def _scoped_pin(engine, uid, key, scope_type, scope_value=None):
    with Session(engine) as s:
        s.add(UserPreference(user_id=uid, text=f"Never include {key}", polarity="suppress",
                             target_type="experience", target_key=key, target_term=key,
                             scope_type=scope_type, scope_value=scope_value, strength=5))
        s.commit()


def _family(uid, job_id, family):
    from harness import library
    library.record_role_family(uid, UUID(job_id), "", supplied=family)


@pytest.mark.parametrize("pin_family,refused", [("machine_learning", True), ("data_science", False)])
def test_a_role_family_pin_binds_only_on_jobs_of_that_family(isolated_engine, env, pin_family,
                                                            refused):
    uid, job_id, _ = env
    _family(uid, job_id, "machine_learning")
    _scoped_pin(isolated_engine, uid, EXP, "role_family", pin_family)
    out = _run(uid, {"job_id": job_id, "nodes": [{"id": "k", "op": "keep", "item_key": EXP}]},
               dry_run=True)
    status = out["nodes"][0]["status"]
    assert (status == "refused") is refused, out["nodes"][0]
    assert any(v["check"] == "preferences" for v in out["violations"]) is refused


def test_the_briefing_and_pins_show_the_scope_the_executor_enforces(isolated_engine, env):
    from harness.tools import art_briefing, art_pins

    uid, job_id, _ = env
    _family(uid, job_id, "machine_learning")
    _scoped_pin(isolated_engine, uid, EXP, "role_family", "machine_learning")
    _scoped_pin(isolated_engine, uid, EXP2, "job", job_id)

    def keys(out):
        return sorted(p["target_key"] for p in out["pins"])

    # Given the job, both readers resolve its family and include its own pins.
    briefing = art_briefing(uid, job_id=job_id)
    assert keys(briefing) == sorted([EXP, EXP2]) and briefing["role_family"] == "machine_learning"
    assert keys(art_pins(uid, job_id=job_id)) == sorted([EXP, EXP2])   # the hook's restore path
    # Without it, a job's own pin is not shown, and a family's only when named.
    assert keys(art_briefing(uid)) == []
    assert keys(art_briefing(uid, role_family="machine_learning")) == [EXP]
