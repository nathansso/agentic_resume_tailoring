"""The tailoring tree (issue #196): nodes, HEAD, provenance, change feed, backfill."""

import pytest
from sqlmodel import Session, select

from database.models import JobHead, TailorNode, TreeEvent, UserJobResult


def _content(*bullets, project="Next-Item Recommendation"):
    return {"experiences": [{"title": "Data Science Intern", "company": "IDX Exchange",
                             "bullets": list(bullets)}],
            "projects": [{"name": project, "bullets": ["Compared XGBoost with a GNN"]}],
            "skills_ranked": ["Python"]}


@pytest.fixture()
def job(isolated_engine):
    """A user with one tailored job; returns (user_id, job_id, result_id)."""
    from conftest import _seed_user_and_skill
    from test_job_card import _seed_job_and_result

    user = _seed_user_and_skill(isolated_engine)
    job_id, result_id = _seed_job_and_result(
        isolated_engine, user.user_id, content=_content("Built an ensemble", "Built a pipeline"))
    return user.user_id, job_id, result_id


def _set_result(engine, result_id, **fields):
    with Session(engine) as s:
        r = s.get(UserJobResult, result_id)
        for k, v in fields.items():
            setattr(r, k, v)
        s.add(r)
        s.commit()


def _head(engine, job_id):
    with Session(engine) as s:
        h = s.get(JobHead, job_id)
        return str(h.node_id) if h else None


# ── commits and HEAD ─────────────────────────────────────────────────────────

def test_record_result_chains_nodes_and_moves_head(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    first = tree.record_result(uid, jid, source="pipeline")
    _set_result(isolated_engine, rid, tailored_resume_content=_content("Built a tuned ensemble"))
    second = tree.record_result(uid, jid, source="pipeline", note="revise")

    assert first["parent_id"] is None and second["parent_id"] == first["node_id"]
    assert [n["seq"] for n in tree.history(uid, jid)] == [0, 1]
    assert _head(isolated_engine, jid) == second["node_id"]
    assert second["provenance"]["host"] == "art" and "art_version" in second["provenance"]


def test_an_unchanged_save_writes_no_node(isolated_engine, job):
    from harness import tree

    uid, jid, _ = job
    assert tree.record_result(uid, jid, source="pipeline") is not None
    assert tree.record_result(uid, jid, source="editor") is None
    assert len(tree.history(uid, jid)) == 1


def test_a_stale_parent_is_rejected_and_nothing_is_written(isolated_engine, job):
    from harness import tree

    uid, jid, _ = job
    root = tree.commit_node(uid, jid, content=_content("a"), source="host", expected_parent=None)
    tree.commit_node(uid, jid, content=_content("b"), source="host",
                     expected_parent=root["node_id"])
    with pytest.raises(tree.StaleParent):
        tree.commit_node(uid, jid, content=_content("c"), source="host",
                         expected_parent=root["node_id"])
    assert len(tree.history(uid, jid)) == 2


def test_unknown_source_is_refused(job):
    from harness import tree

    uid, jid, _ = job
    with pytest.raises(ValueError):
        tree.commit_node(uid, jid, content={}, source="mystery")


def _status(engine, job_id):
    from database.models import JobDescription
    with Session(engine) as s:
        return s.get(JobDescription, job_id).status


@pytest.mark.parametrize("before,after", [("created", "tailored"), ("analyzed", "tailored"),
                                          ("tailored", "tailored"), ("exported", "exported")])
def test_a_materialized_commit_marks_the_job_as_having_a_resume(isolated_engine, before, after):
    """A host-built version must reach the editor, which renders only for a job whose
    status says it has a resume (#276). An exported job is not moved back."""
    from conftest import _seed_user_and_skill
    from database.models import JobDescription
    from harness import tree

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        j = JobDescription(user_id=user.user_id, title="ML Engineer", company="Acme",
                           description="Build ML systems", status=before)
        s.add(j)
        s.commit()
        jid = j.job_id

    tree.commit_node(user.user_id, jid, content=_content("a"), source="host",
                     expected_parent=None, materialize=True)
    assert _status(isolated_engine, jid) == after


def test_a_commit_that_does_not_materialize_leaves_the_status_alone(isolated_engine, job):
    from database.models import JobDescription
    from harness import tree

    uid, jid, _ = job
    with Session(isolated_engine) as s:
        j = s.get(JobDescription, jid)
        j.status = "analyzed"
        s.add(j)
        s.commit()
    tree.commit_node(uid, jid, content=_content("a"), source="host")
    assert _status(isolated_engine, jid) == "analyzed"


# ── checkout, revert, siblings ───────────────────────────────────────────────

def test_checkout_materializes_the_node_into_the_current_result(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    first = tree.record_result(uid, jid, source="pipeline")
    _set_result(isolated_engine, rid, tailored_resume_content=_content("new"),
                edited_tex="\\documentclass{article}", layout_overrides={"skills": ["Python"]})
    tree.record_result(uid, jid, source="editor")

    tree.checkout(uid, jid, first["node_id"])
    with Session(isolated_engine) as s:
        r = s.get(UserJobResult, rid)
        assert r.tailored_resume_content == first["content"]
        assert r.edited_tex is None and r.layout_overrides is None
    assert _head(isolated_engine, jid) == first["node_id"]


def test_revert_moves_head_to_the_parent_when_it_is_the_restored_version(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    first = tree.record_result(uid, jid, source="pipeline")
    _set_result(isolated_engine, rid, tailored_resume_content=_content("new"))
    tree.record_result(uid, jid, source="pipeline")
    # What chat's revert does to the row, then its hook:
    _set_result(isolated_engine, rid, tailored_resume_content=first["content"])
    tree.record_revert(uid, jid)

    assert _head(isolated_engine, jid) == first["node_id"]
    assert len(tree.history(uid, jid)) == 2          # moved, not re-committed
    assert tree.events_since(uid, 0, jid)[-1]["kind"] == "checkout"


def test_branching_after_checkout_yields_a_sibling_pair(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    root = tree.record_result(uid, jid, source="pipeline")
    _set_result(isolated_engine, rid, tailored_resume_content=_content("branch a"))
    a = tree.record_result(uid, jid, source="pipeline")
    tree.checkout(uid, jid, root["node_id"])
    _set_result(isolated_engine, rid, tailored_resume_content=_content("branch b"))
    b = tree.record_result(uid, jid, source="editor")

    assert tree.sibling_pairs(uid, jid) == [
        {"parent_id": root["node_id"], "a": a["node_id"], "b": b["node_id"]}]


def test_diff_reports_bullet_changes_by_item_key(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    first = tree.record_result(uid, jid, source="pipeline")
    _set_result(isolated_engine, rid,
                tailored_resume_content=_content("Built an ensemble", "Shipped a recommender",
                                                 project="Recipe Popularity"))
    second = tree.record_result(uid, jid, source="pipeline")

    d = tree.diff_nodes(uid, first["node_id"], second["node_id"])
    by_key = {c["key"]: c for c in d["items"]}
    exp = by_key["exp:data science intern|idx exchange"]
    assert exp["change"] == "revised"
    assert exp["bullets_added"] == ["Shipped a recommender"]
    assert exp["bullets_removed"] == ["Built a pipeline"]
    assert by_key["proj:next-item recommendation"]["change"] == "removed"
    assert by_key["proj:recipe popularity"]["change"] == "added"


# ── change feed ──────────────────────────────────────────────────────────────

def test_the_feed_is_ordered_and_resumes_from_a_cursor(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    tree.record_result(uid, jid, source="pipeline")
    head = tree.get_head(uid, jid, since_event=0)
    cursor = head["cursor"]
    _set_result(isolated_engine, rid, tailored_resume_content=_content("edited in the editor"))
    edit = tree.record_result(uid, jid, source="editor", note="tex edit")

    later = tree.get_head(uid, jid, since_event=cursor)
    assert [e["node_id"] for e in later["events"]] == [edit["node_id"]]
    assert [n["node_id"] for n in later["editor_edits"]] == [edit["node_id"]]
    assert later["head"]["node_id"] == edit["node_id"] and later["cursor"] > cursor
    ids = [e["event_id"] for e in tree.events_since(uid, 0)]
    assert ids == sorted(ids)


def test_the_tree_is_scoped_to_its_user(isolated_engine, job):
    from harness import tree
    from database.models import User

    uid, jid, _ = job
    node = tree.record_result(uid, jid, source="pipeline")
    with Session(isolated_engine) as s:
        other = User(name="Other", email="other@example.com")
        s.add(other)
        s.commit()
        other_id = other.user_id
    assert tree.history(other_id, jid) == []
    with pytest.raises(tree.NotFound):
        tree.get_node(other_id, node["node_id"])


# ── migration ────────────────────────────────────────────────────────────────

def test_backfill_chains_existing_results_once(isolated_engine, job):
    from database.db import init_db
    from harness import tree

    uid, jid, rid = job
    _set_result(isolated_engine, rid, tailored_resume_previous={
        "content": _content("the version before"), "score_breakdown": {"composite": 60}})

    init_db()                      # runs _backfill_tailor_tree
    nodes = tree.history(uid, jid)
    assert [n["source"] for n in nodes] == ["backfill", "backfill"]
    assert nodes[1]["parent_id"] == nodes[0]["node_id"]
    assert _head(isolated_engine, jid) == nodes[1]["node_id"]
    with Session(isolated_engine) as s:
        assert s.exec(select(TreeEvent)).all() == []      # backfill is not a live change

    init_db()
    assert len(tree.history(uid, jid)) == 2               # idempotent


def test_deleting_a_job_removes_its_tree(isolated_engine, job):
    import services
    from harness import tree

    uid, jid, _ = job
    tree.record_result(uid, jid, source="pipeline")
    assert services.delete_job(str(jid)) == "Job deleted."
    with Session(isolated_engine) as s:
        assert s.exec(select(TailorNode)).all() == []
        assert s.exec(select(JobHead)).all() == []
        assert s.exec(select(TreeEvent)).all() == []


# ── write hooks ──────────────────────────────────────────────────────────────

def test_an_editor_tex_save_commits_an_editor_node(isolated_engine, job, monkeypatch):
    import database.db as db_module
    import web.routers.jobs_router as jobs_router_module
    import web.auth as web_auth_module
    from fastapi.testclient import TestClient
    from database.models import User
    from harness import tree
    from web.app import app

    uid, jid, _ = job
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(jobs_router_module, "engine", isolated_engine)
    with Session(isolated_engine) as s:
        user = s.get(User, uid)
    app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
    try:
        client = TestClient(app, raise_server_exceptions=True)
        tex = "\\documentclass{article}\\begin{document}Hi\\end{document}"
        assert client.put(f"/api/jobs/{jid}/tex", json={"tex": tex}).status_code == 200
    finally:
        app.dependency_overrides.clear()

    nodes = tree.history(uid, jid)
    assert nodes[-1]["source"] == "editor" and nodes[-1]["note"] == "tex edit"
    assert tree.get_node(uid, nodes[-1]["node_id"])["edited_tex"] == tex


# ── a hand edit of the .tex is reported as bullets (#278) ────────────────────

EXP_KEY = "exp:data science intern|idx exchange"


def _pipeline_version(engine, job):
    """A first version in the pipeline's shape (skills_ranked as dicts), which the formatter renders."""
    from harness import tree

    uid, jid, rid = job
    _set_result(engine, rid, tailored_resume_content={
        **_content("Built an ensemble", "Built a pipeline"),
        "skills_ranked": [{"name": "Python", "category": "Languages", "score": 1.0}]})
    return tree.record_result(uid, jid, source="pipeline")


def _tex_edit(engine, job, change, base=None):
    """The editor saving a hand-edited .tex on top of `base` (default: a pipeline version).
    Returns (base node, editor node)."""
    from harness import texdiff, tree

    uid, jid, rid = job
    base = base or _pipeline_version(engine, job)
    tex = texdiff.node_tex(uid, tree.get_node(uid, base["node_id"]))
    edited = change(tex)
    assert edited != tex
    _set_result(engine, rid, edited_tex=edited)
    return base, tree.record_result(uid, jid, source="editor", note="tex edit")


def _tex_diff(uid, base, edit):
    from harness.contract import invoke
    out = invoke("diff_nodes", uid, {"from_node": base["node_id"], "to_node": edit["node_id"]})
    assert out["error"] is None and out["tex_changed"] is True
    return out["tex_edits"]


def test_a_text_edit_to_a_bullet_names_the_bullet(isolated_engine, job):
    base, edit = _tex_edit(isolated_engine, job, lambda t: t.replace(
        r"\resumeItem{Built an ensemble}", r"\resumeItem{Built a \textbf{tuned} ensemble \& more}"))
    out = _tex_diff(job[0], base, edit)
    assert out["bullets"] == [{
        "item": EXP_KEY, "title": "Data Science Intern @ IDX Exchange", "change": "revised",
        "bullet": f"{EXP_KEY}#b0", "before": "Built an ensemble",
        "after": "Built a **tuned** ensemble & more", "position": 0}]
    assert out["unmapped"] == [] and out["reordered"] == [] and out["section_order"] is None


def test_an_added_bullet_is_reported_with_its_item(isolated_engine, job):
    base, edit = _tex_edit(isolated_engine, job, lambda t: t.replace(
        "\\resumeItem{Built a pipeline}\n",
        "\\resumeItem{Built a pipeline}\n        \\resumeItem{Shipped a recommender}\n"))
    out = _tex_diff(job[0], base, edit)
    assert [(b["item"], b["change"], b["after"], b["position"]) for b in out["bullets"]] == [
        (EXP_KEY, "added", "Shipped a recommender", 2)]
    assert out["bullets"][0]["bullet"] is None and out["unmapped"] == []


def test_a_removed_bullet_carries_its_id_on_the_earlier_version(isolated_engine, job):
    base, edit = _tex_edit(isolated_engine, job, lambda t: t.replace(
        "        \\resumeItem{Built an ensemble}\n", ""))
    out = _tex_diff(job[0], base, edit)
    assert [(b["change"], b["bullet"], b["before"], b["after"]) for b in out["bullets"]] == [
        ("removed", f"{EXP_KEY}#b0", "Built an ensemble", None)]
    assert out["unmapped"] == []


def test_bullets_that_only_moved_are_reordered_not_edited(isolated_engine, job):
    def swap(t):
        a, b = "        \\resumeItem{Built an ensemble}\n", "        \\resumeItem{Built a pipeline}\n"
        return t.replace(a + b, b + a)
    base, edit = _tex_edit(isolated_engine, job, swap)
    out = _tex_diff(job[0], base, edit)
    assert out["bullets"] == [] and out["unmapped"] == []
    assert [r["item"] for r in out["reordered"]] == [EXP_KEY]


def test_an_edit_outside_the_bullets_is_an_unmapped_diff_never_nothing(isolated_engine, job):
    base, edit = _tex_edit(isolated_engine, job, lambda t: t.replace(
        "\\begin{center}", "\\begin{center}\n    \\small Phone 555-0100", 1))
    out = _tex_diff(job[0], base, edit)
    assert out["bullets"] == []
    [region] = out["unmapped"]
    assert region["region"] == "header" and "+\\small Phone 555-0100" in region["diff"]


def test_source_that_cannot_be_mapped_falls_back_to_a_diff(isolated_engine, job):
    """A rewrite with no section markers and no item headings: the bullets are gone, and the
    lines are reported as unmapped rather than as nothing."""
    base, edit = _tex_edit(isolated_engine, job, lambda t: "\\documentclass{article}\n"
                           "\\begin{document}\nJust some text.\n\\end{document}\n")
    out = _tex_diff(job[0], base, edit)
    assert out["unmapped"] and any("Just some text." in r["diff"] for r in out["unmapped"])
    assert {b["change"] for b in out["bullets"]} == {"removed"}


def test_an_unchanged_tex_save_reports_no_edits(isolated_engine, job):
    from harness import tree

    uid, jid, rid = job
    base, edit = _tex_edit(isolated_engine, job, lambda t: t + "\n\n")      # whitespace only
    out = tree.diff_nodes(uid, base["node_id"], edit["node_id"])
    assert out["tex_changed"] is True
    assert out["tex_edits"] == {"bullets": [], "reordered": [], "section_order": None,
                                "unmapped": []}


def test_get_head_tells_the_host_which_bullets_changed_since_the_last_non_editor_version(
        isolated_engine, job):
    from harness import tree
    from harness.contract import invoke

    uid, jid, rid = job
    base = _pipeline_version(isolated_engine, job)
    cursor = tree.get_head(uid, jid, since_event=0)["cursor"]
    _, first = _tex_edit(isolated_engine, job, lambda t: t.replace(
        "Built an ensemble", "Built a tuned ensemble"), base=base)
    _, second = _tex_edit(isolated_engine, job, lambda t: t.replace(
        "Built a pipeline", "Built a data pipeline"), base=first)

    out = invoke("get_head", uid, {"job_id": str(jid), "since_event": cursor})
    assert out["error"] is None
    early, late = out["editor_edits"]
    assert early["node_id"] == first["node_id"] and early["changes"] is None
    changes = late["changes"]
    assert changes["since_node"] == base["node_id"] and changes["tex_changed"] is True
    assert [(b["bullet"], b["before"], b["after"]) for b in changes["tex_edits"]["bullets"]] == [
        (f"{EXP_KEY}#b0", "Built an ensemble", "Built a tuned ensemble"),
        (f"{EXP_KEY}#b1", "Built a pipeline", "Built a data pipeline")]


def test_the_short_summary_is_capped(isolated_engine, job):
    from harness import texdiff

    edits = {"bullets": [{"item": "exp:a|b", "title": "", "change": "removed", "bullet": f"x#b{n}",
                          "before": "w" * 400, "after": None, "position": None} for n in range(12)],
             "reordered": [], "section_order": None,
             "unmapped": [{"region": "header", "reason": "r", "diff": "\n".join("l" * 3 for _ in range(40))}]}
    short = texdiff.brief(edits)
    assert len(short["bullets"]) == 8 and short["truncated"] == 4
    assert all(len(b["before"]) <= 160 for b in short["bullets"])
    assert len(short["unmapped"][0]["diff"].splitlines()) == 13
    assert len(texdiff.public(edits)["bullets"]) == 12


# ── write safety ─────────────────────────────────────────────────────────────

def test_write_tools_refuse_on_a_read_only_process(isolated_engine, job):
    from harness import tree
    from harness.contract import invoke

    uid, jid, _ = job
    node = tree.record_result(uid, jid, source="pipeline")
    out = invoke("checkout", uid, {"job_id": str(jid), "node_id": node["node_id"]},
                 allow_writes=False)
    assert out["error"]["code"] == "read_only"
    # Reads still work read-only.
    assert invoke("history", uid, {"job_id": str(jid)}, allow_writes=False)["error"] is None


def test_remote_databases_are_read_only_unless_writes_are_allowed():
    from harness.runtime import resolve_database_url, writes_allowed

    url = "postgresql://u@h/db"
    assert "default_transaction_read_only" in resolve_database_url(url, {})
    assert "default_transaction_read_only" not in resolve_database_url(url, {}, allow_writes=True)
    assert writes_allowed("sqlite:///x.db", False)
    assert not writes_allowed(url, False) and writes_allowed(url, True)
