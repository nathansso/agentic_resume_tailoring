"""The citation faithfulness gate and negative pins (issue #198).

Every bullet on a host-built page rests on live evidence: verbatim a source
bullet of its item, or citing ids that resolve. An uncited bullet, a cite that
does not resolve, and a cite to an item the user deleted are all rejected.
A negative pin (a hard suppression by term) is a fact that must never render,
so a bullet that mentions it is rejected even when it is properly cited.
"""

import pytest
from sqlmodel import Session, select

from database.models import Project, UserPreference
from harness.acceptance import Context, citation_violations, negative_pin_violations
from test_executor import EXP, EXP2, _run, env  # noqa: F401  (fixture)

STREAM = "proj:streamboard"


def _src(uid, key):
    from harness.executor import _KG
    return _KG(uid).source_bullets[key]


def _cited(key, texts):
    return [{"text": t, "cites": [f"{key}#b{i}"]} for i, t in enumerate(texts)]


def _revise(key, bullets, **kw):
    return {"id": f"r:{key}", "op": "revise", "item_key": key, "strategy": "tighten",
            "bullets": bullets, "accept": {"improves": ["relevance_density"]}, **kw}


def _pin(engine, uid, term):
    with Session(engine) as s:
        pref = UserPreference(user_id=uid, text=f"Never mention {term}", polarity="suppress",
                              target_term=term.lower(), scope_type="global", strength=5)
        s.add(pref)
        s.commit()
        return str(pref.preference_id)


# ── citations ────────────────────────────────────────────────────────────────

def test_an_uncited_bullet_is_refused(env):
    uid, job_id, _ = env
    bullets = _cited(EXP2, _src(uid, EXP2)[:3]) + [{"text": "Scaled the team to 12.", "cites": []}]
    node = _run(uid, {"job_id": job_id, "nodes": [_revise(EXP2, bullets)]}, dry_run=True)["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith("uncited_bullet: bullet 3")


def test_a_cite_to_a_deleted_item_is_refused_as_tombstoned(env, isolated_engine):
    import services

    uid, job_id, _ = env
    with Session(isolated_engine) as s:
        pid = s.exec(select(Project).where(Project.user_id == uid,
                                           Project.name == "StreamBoard")).one().project_id
    assert services.delete_project(uid, str(pid))
    bullets = _cited(EXP2, _src(uid, EXP2)[:2]) + [
        {"text": "Built a real-time Kafka pipeline.", "cites": [f"{STREAM}#b0"]}]
    out = _run(uid, {"job_id": job_id, "nodes": [
        _revise(EXP2, bullets),
        {"id": "k", "op": "keep", "item_key": STREAM}]}, dry_run=True)
    reasons = {n["id"]: n["reason"] for n in out["nodes"]}
    assert reasons[f"r:{EXP2}"].startswith("tombstoned_cite: bullet 2 cites proj:streamboard#b0")
    assert reasons["k"] == "tombstoned: the user deleted proj:streamboard"
    # A key that never existed is still just unknown.
    other = _run(uid, {"job_id": job_id, "nodes": [
        {"id": "x", "op": "keep", "item_key": "proj:no such thing"}]}, dry_run=True)
    assert other["nodes"][0]["reason"].startswith("unknown_key")


def test_cites_persist_on_the_committed_version(env):
    from harness.tree import get_head

    uid, job_id, _ = env
    src = _src(uid, EXP2)
    bullets = [{"text": "Built FastAPI and Flask services behind Nginx at 40k requests/minute.",
                "cites": [f"{EXP2}#b0"]}] + _cited(EXP2, src[1:3])
    bullets[2]["cites"] = [f"{EXP2}#b2"]
    out = _run(uid, {"job_id": job_id, "nodes": [_revise(EXP2, bullets)]})
    assert out["committed"], out["violations"]
    item = next(e for e in get_head(uid, job_id)["head"]["content"]["experiences"]
                if e["title"] == "Backend Software Engineer")
    assert item["cites"][bullets[0]["text"]] == [f"{EXP2}#b0"]
    assert out["metrics"]["final"]["gates"]["citations"] == []


def test_verbatim_source_bullets_count_as_cited_and_editor_bullets_never_block(env):
    from harness import tree

    uid, job_id, _ = env
    base = _run(uid, {"job_id": job_id, "nodes": []}, dry_run=True)
    assert base["metrics"]["base"]["gates"]["citations"] == []     # KG text cites itself

    # The user rewrites a bullet by hand in the editor: uncited, but theirs.
    head = tree.get_head(uid, job_id)["head"]
    content = head["content"] if head else base_content(uid, job_id)
    exp = next(e for e in content["experiences"] if e["title"] == "Machine Learning Engineer")
    exp["bullets"][0] = "My own wording of the ranking-model work."
    exp.pop("cites", None)
    node = tree.commit_node(uid, job_id, content=content, source="editor")
    out = _run(uid, {"job_id": job_id, "parent": node["node_id"],
                     "nodes": [{"id": "k", "op": "keep", "item_key": EXP}]})
    assert out["committed"], out["violations"]
    assert any("uncited" in v for v in out["metrics"]["base"]["gates"]["citations"])


def base_content(uid, job_id):
    from harness.executor import kg_default_content
    from database.models import JobDescription
    from uuid import UUID
    import database.db as db

    with Session(db.engine) as s:
        job = s.get(JobDescription, UUID(job_id))
    return kg_default_content(uid, job.description)


def test_the_gate_flags_any_uncited_unresolved_or_tombstoned_bullet():
    status = {"exp:a|b#b0": None, "exp:a|b#b9": "unresolved", "proj:gone#b0": "tombstoned"}
    ctx = Context(jd_text="", source_bullets={"exp:a|b": ["Did X."]},
                  cite_status=lambda c: status.get(c, "unresolved"))
    content = {"experiences": [{"title": "A", "company": "B",
                                "bullets": ["Did X.", "Did Y.", "Did Z.", "Did W."],
                                "cites": {"Did Z.": ["exp:a|b#b9"], "Did W.": ["proj:gone#b0"]}}]}
    got = citation_violations(content, ctx)
    assert got == ['exp:a|b: tombstoned:proj:gone#b0: "Did W."',
                   'exp:a|b: uncited: "Did Y."',
                   'exp:a|b: unresolved:exp:a|b#b9: "Did Z."']
    # A reorder is not a new violation (keyed by text, not position).
    content["experiences"][0]["bullets"].reverse()
    assert citation_violations(content, ctx) == got
    # Built outside the executor (no checker): the gate is off.
    assert citation_violations(content, Context(jd_text="")) == []


# ── negative pins ────────────────────────────────────────────────────────────

def test_a_cited_bullet_that_mentions_a_negative_pin_is_refused(env, isolated_engine):
    uid, job_id, _ = env
    _pin(isolated_engine, uid, "Kafka")
    src = _src(uid, EXP2)
    node = _run(uid, {"job_id": job_id, "nodes": [_revise(EXP2, _cited(EXP2, src))]},
                dry_run=True)["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith("negative_pin: bullet 3")


def test_a_pinned_fact_already_on_the_page_blocks_finalize_until_it_is_cut(env, isolated_engine):
    uid, job_id, _ = env
    pref_id = _pin(isolated_engine, uid, "Kafka")
    blocked = _run(uid, {"job_id": job_id, "nodes": []})
    pins = [v for v in blocked["violations"] if v["detail"].startswith("negative_pin:kafka@")]
    assert not blocked["committed"] and pins
    assert {v["detail"].split("@", 1)[1].split(" :: ")[0] for v in pins} >= {EXP2, STREAM}
    assert all("no longer mentions 'kafka'" in v["hint"] for v in pins)

    src = _src(uid, EXP2)
    fixed = _run(uid, {"job_id": job_id, "nodes": [
        _revise(EXP2, _cited(EXP2, src[:3])),
        {"id": "d", "op": "delete", "item_key": STREAM, "because": f"pref:{pref_id}"}]})
    assert fixed["committed"], fixed["violations"]
    final = fixed["metrics"]["final"]["gates"]["preferences"]
    assert not [v for v in final if v.startswith("negative_pin:")]


def test_negative_pins_match_whole_words_only():
    content = {"experiences": [{"title": "Engineer", "company": "Acme",
                                "bullets": ["Used Java daily.", "Wrote JavaScript tools."]}]}
    got = negative_pin_violations(content, {"java"})
    assert got == ['negative_pin:java@exp:engineer|acme :: "Used Java daily."']
    assert negative_pin_violations(content, set()) == []


@pytest.mark.parametrize("target_key", ["skill:kafka"])
def test_a_skill_keyed_suppression_is_not_a_negative_pin(target_key):
    from harness.executor import _negative_terms

    assert _negative_terms({"kafka": {"target_key": target_key}}) == {}
    assert _negative_terms({"kafka": {"target_key": None}}) == {"kafka": {"target_key": None}}
