"""The bullet library and track baselines (issue #229): the Jev-free core of #199.

Variants (approved wording the user confirmed), promotion that never approves on its own,
track baselines a job starts from, `suggest_actions` on its fallbacks, and the
`variant_drift` guard. Everything is deterministic; the one Jev-backed check (the cited-bullet
support gate) runs on a scripted transport. Line counts come from an injected measurer, so none
of this needs a LaTeX engine.
"""

import copy
import json
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

import database.db as db
from database.models import BulletVariant, JobRoleFamily, TrackBaseline, User, UserPreference
from harness.contract import invoke
from test_executor import EXP, EXP2, PROJ, _head, _run, env  # noqa: F401  (fixture)
from test_jev import FakeTransport, auto, choice_answer  # noqa: F401  (fixture)

WOVEN = ("Led the migration from a monolith to event-driven services using Kafka to integrate "
         "billing and search.")


# ── helpers ──────────────────────────────────────────────────────────────────

def _import(uid, key, text, **extra):
    """One variant through `upsert_items`; returns the single result row."""
    data = {"item_key": key, "text": text, **extra}
    return invoke("upsert_items", uid, {"records": [{"kind": "variant", "data": data}]})["results"][0]


def _variants(uid, key, status="approved"):
    from harness import library
    return library.list_variants(uid, key, status=status)


def _vid(result):
    return result["key"].split(":", 1)[1]


def _second_user(isolated_engine, name="Other User"):
    with Session(isolated_engine) as s:
        user = User(name=name, email=f"{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        return user.user_id


def _source(uid, key):
    from harness.executor import _KG
    return _KG(uid).source_bullets[key]


def _revise(key, bullets, **node):
    return {"id": f"revise:{key}", "op": "revise", "item_key": key, "bullets": bullets,
            "accept": {"improves": ["coverage"]}, **node}


def _weave_bullets(uid, variant_id=None, text=WOVEN):
    """EXP2's source bullets with the fourth replaced by `text` (named `from_variant` when given)."""
    out = [{"text": b, "cites": [f"{EXP2}#b{i}"]} for i, b in enumerate(_source(uid, EXP2))]
    out[3] = {"text": text, "cites": []} if variant_id else {"text": text, "cites": [f"{EXP2}#b3"]}
    if variant_id:
        out[3]["from_variant"] = variant_id
    return out


def _plan(uid, job_id, nodes, **kw):
    """A plan on the job's current HEAD."""
    from harness import tree

    head = tree.get_head(uid, job_id)["head"]
    return _run(uid, {"job_id": job_id, "parent": head["node_id"] if head else None,
                      "nodes": nodes}, **kw)


def _committed(uid, job_id, nodes=()):
    out = _plan(uid, job_id, list(nodes))
    assert out["committed"], out
    return out["node_id"]


def _task_job(uid, index, **metadata):
    """Open another benchmark task's job through `open_job`; returns the result dict."""
    from eval.scripted_host import extract_requirements
    from eval.tailoring_benchmark import load_tasks

    task = load_tasks(limit=5)[index]
    out = invoke("open_job", uid, {
        "jd_text": task["description"], "requirements": extract_requirements(task["description"]),
        "metadata": {"title": task["title"], "company": task["company"], **metadata}})
    assert out.get("error") is None, out
    return out


# ── the table, and an old database ───────────────────────────────────────────

def test_an_existing_database_without_the_new_tables_loads(tmp_path, monkeypatch):
    """The three tables are additive: a database built before them gains them from `init_db`
    and keeps its rows."""
    from sqlalchemy import inspect as sa_inspect
    from sqlmodel import SQLModel, create_engine

    import database.db as db_module
    from database import models

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}",
                           connect_args={"check_same_thread": False})
    new = {BulletVariant.__table__, TrackBaseline.__table__, JobRoleFamily.__table__}
    old = [t for t in SQLModel.metadata.sorted_tables if t not in new]
    SQLModel.metadata.create_all(engine, tables=old)
    with Session(engine) as s:
        s.add(models.User(name="Old", email="old@example.com"))
        s.commit()
    assert not {"bulletvariant", "trackbaseline", "jobrolefamily"} & set(sa_inspect(engine).get_table_names())

    monkeypatch.setattr(db_module, "engine", engine)
    db_module.init_db()
    assert {"bulletvariant", "trackbaseline", "jobrolefamily"} <= set(sa_inspect(engine).get_table_names())
    with Session(engine) as s:
        assert s.exec(select(models.User)).one().name == "Old"
        assert s.exec(select(BulletVariant)).all() == []


# ── importing a curated library ──────────────────────────────────────────────

def test_an_imported_variant_arrives_approved_and_cites_its_item(env):
    uid, *_ = env
    out = _import(uid, EXP2, WOVEN, tags={"track": "Data Science"})
    assert out["status"] == "created" and out["key"].startswith("variant:")
    (v,) = _variants(uid, EXP2)
    assert v["status"] == "approved" and v["text"] == WOVEN
    assert v["cites"] == [EXP2]                       # the citations gate resolves an item key
    assert v["tags"] == {"track": "data_science"}     # normalized like a baseline's track
    assert v["source_node_id"] is None and v["line_count"] == 1    # the injected measurer


def test_import_deduplicates_on_item_and_normalized_text(env):
    uid, *_ = env
    first = _import(uid, EXP2, WOVEN)
    again = _import(uid, EXP2.upper(), "  " + WOVEN.replace(" ", "  ") + " ")
    assert (first["status"], again["status"]) == ("created", "unchanged")
    assert again["key"] == first["key"] and len(_variants(uid, EXP2)) == 1
    other = _import(uid, EXP, WOVEN)                 # the same words under another item are another variant
    assert other["status"] == "created"


def test_a_duplicate_that_adds_tags_merges_and_a_draft_becomes_approved(env, isolated_engine):
    from harness import library

    uid, *_ = env
    first = _import(uid, EXP2, WOVEN)
    merged = _import(uid, EXP2, WOVEN, tags={"track": "mle"}, cites=[f"{EXP2}#b3"])
    assert merged["status"] == "merged" and merged["key"] == first["key"]
    (v,) = _variants(uid, EXP2)
    assert v["tags"] == {"track": "mle"} and v["cites"] == [EXP2, f"{EXP2}#b3"]

    with Session(isolated_engine) as s:
        row = s.get(BulletVariant, UUID(_vid(first)))
        row.status = "draft"
        s.add(row)
        s.commit()
    assert _variants(uid, EXP2) == []
    assert _import(uid, EXP2, WOVEN)["status"] == "merged"      # importing is the user's confirmation
    assert [x["status"] for x in library.list_variants(uid, EXP2)] == ["approved"]


@pytest.mark.parametrize("data,why", [
    ({"item_key": "exp:astronaut|nasa", "text": "Flew"}, "No experience or project"),
    ({"item_key": "skill:python", "text": "Python"}, "No experience or project"),
    ({"item_key": EXP2, "text": "   "}, "text is required"),
    ({"item_key": EXP2, "text": "x", "cites": ["exp:astronaut|nasa"]}, "names no item"),
    ({"item_key": EXP2, "text": "x", "tags": {"employer": "x"}}, "tags.employer"),
    ({"item_key": EXP2}, "text"),
])
def test_import_refuses_a_bad_record_with_a_reason(env, data, why):
    uid, *_ = env
    out = invoke("upsert_items", uid, {"records": [{"kind": "variant", "data": data}]})["results"][0]
    assert out["status"] == "invalid" and why in out["message"]
    assert _variants(uid, EXP2, status=None) == []


def test_a_variant_can_arrive_in_the_same_call_as_its_item(env):
    uid, *_ = env
    out = invoke("upsert_items", uid, {"records": [
        {"kind": "variant", "data": {"item_key": "exp:analyst|new co", "text": "Built the thing."}},
        {"kind": "experience", "data": {"title": "Analyst", "company": "New Co",
                                        "bullets": ["Built the thing."]}}]})
    assert [r["status"] for r in out["results"]] == ["created", "created"]
    assert [v["text"] for v in _variants(uid, "exp:analyst|new co")] == ["Built the thing."]


def test_the_schema_is_published_for_hosts(env):
    out = invoke("ingest_schema", env[0], {"kind": "variant"})
    assert out["required"] == ["item_key", "text"]
    assert {"item_key", "text", "cites", "tags"} <= set(out["json_schema"]["properties"])


# ── get_item ─────────────────────────────────────────────────────────────────

def test_get_item_lists_approved_variants_only(env):
    uid, job_id, _ = env
    approved = _import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"], tags={"track": "mle"})
    node_id = _committed(uid, job_id)
    draft = invoke("promote_bullet", uid, {"node_id": node_id, "bullet": _source(uid, EXP2)[0]})
    assert draft["variant"]["status"] == "draft"

    got = invoke("get_item", uid, {"key": EXP2})
    assert [v["variant_id"] for v in got["variants"]] == [_vid(approved)]
    (v,) = got["variants"]
    assert v["text"] == WOVEN and v["cites"] == [f"{EXP2}#b3"] and v["tags"] == {"track": "mle"}
    assert got["record"]["title"]                          # the KG record is untouched
    assert invoke("get_item", uid, {"key": "skill:python"}).get("variants") is None


# ── promotion and approval ───────────────────────────────────────────────────

def test_promotion_makes_a_draft_and_never_approves_it(env):
    uid, job_id, _ = env
    bullet = _source(uid, EXP2)[0]
    node_id = _committed(uid, job_id)
    out = invoke("promote_bullet", uid, {"node_id": node_id, "bullet": f"  {bullet}  "})
    v = out["variant"]
    assert out["created"] and v["status"] == "draft" and v["item_key"] == EXP2
    assert v["source_node_id"] == node_id and v["tags"]["job_id"] == job_id
    assert v["tags"]["track"] == "machine_learning"        # the job's role family, from its title
    assert v["cites"] == [f"{EXP2}#b0"]                    # what the bullet cited on that node

    # Not offered, not usable, and still a draft after everything else has run.
    assert _variants(uid, EXP2) == []
    assert invoke("get_item", uid, {"key": EXP2})["variants"] == []
    sug = invoke("suggest_actions", uid, {"job_id": job_id})
    assert {i["item_key"]: i["match"] for i in sug["items"]}[EXP2] == "no_match"
    refused = _plan(uid, job_id, [_revise(EXP2, [
        {"text": bullet, "cites": [], "from_variant": v["variant_id"]}])], dry_run=True)
    assert refused["nodes"][0]["reason"].startswith("variant_not_approved")
    assert [x["status"] for x in _variants(uid, EXP2, status=None)] == ["draft"]


def test_promoting_the_same_bullet_again_returns_the_existing_variant(env):
    uid, job_id, _ = env
    node_id = _committed(uid, job_id)
    bullet = _source(uid, EXP)[1]
    first = invoke("promote_bullet", uid, {"node_id": node_id, "bullet": bullet})
    again = invoke("promote_bullet", uid, {"node_id": node_id, "bullet": bullet})
    assert first["created"] and not again["created"]
    assert again["variant"]["variant_id"] == first["variant"]["variant_id"]
    assert len(_variants(uid, EXP, status=None)) == 1


def test_promotion_refuses_what_is_not_on_the_node_or_not_the_users(env, isolated_engine):
    uid, job_id, _ = env
    node_id = _committed(uid, job_id)
    gone = invoke("promote_bullet", uid, {"node_id": node_id, "bullet": "Never written"})
    assert gone["error"]["code"] == "bullet_not_found"
    wrong_item = invoke("promote_bullet", uid, {"node_id": node_id, "item_key": EXP,
                                                "bullet": _source(uid, EXP2)[0]})
    assert wrong_item["error"]["code"] == "bullet_not_found"
    for node in ("not-a-uuid", str(uuid4())):
        assert invoke("promote_bullet", uid, {"node_id": node, "bullet": "x"})["error"]["code"] == "not_found"
    stranger = _second_user(isolated_engine)
    assert invoke("promote_bullet", stranger, {"node_id": node_id,
                                               "bullet": _source(uid, EXP)[0]})["error"]["code"] == "not_found"
    assert _variants(uid, EXP, status=None) == []


def test_approve_variant_is_the_only_way_to_approve_and_only_for_the_owner(env, isolated_engine):
    uid, job_id, _ = env
    node_id = _committed(uid, job_id)
    draft = invoke("promote_bullet", uid, {"node_id": node_id,
                                           "bullet": _source(uid, EXP)[0]})["variant"]
    stranger = _second_user(isolated_engine)
    denied = invoke("approve_variant", stranger, {"variant_id": draft["variant_id"]})
    assert denied["error"]["code"] == "not_found"
    assert _variants(uid, EXP) == []

    ok = invoke("approve_variant", uid, {"variant_id": draft["variant_id"]})
    assert ok["changed"] and ok["variant"]["status"] == "approved"
    assert [v["variant_id"] for v in _variants(uid, EXP)] == [draft["variant_id"]]
    assert not invoke("approve_variant", uid, {"variant_id": draft["variant_id"]})["changed"]
    for bad in ("nope", str(uuid4())):
        assert invoke("approve_variant", uid, {"variant_id": bad})["error"]["code"] == "not_found"


def test_the_library_tools_are_writes_where_they_write():
    from harness.contract import BY_NAME

    assert BY_NAME["suggest_actions"].read_only
    assert all(not BY_NAME[n].read_only for n in ("promote_bullet", "approve_variant", "save_baseline"))


# ── suggest_actions ──────────────────────────────────────────────────────────

def _job_terms(uid, job_id):
    import services
    from database.models import JobDescription

    with Session(db.engine) as s:
        text = s.get(JobDescription, UUID(job_id)).description
    weights = services.resolve_keyword_weights(UUID(job_id), uid, text, persist=False)
    return sorted((t for t, w in weights.items() if w > 0), key=lambda t: (-weights[t], t))


def test_suggest_actions_picks_the_variant_with_the_best_overlap(env):
    uid, job_id, _ = env
    top = _job_terms(uid, job_id)[:6]
    strong = "Built " + " ".join(top) + " pipelines"
    middling = "Built " + " ".join(top[:1]) + " reports for the quarterly planning cycle meeting"
    weak = "Organized the annual office holiday celebration party"
    for text in (weak, middling, strong):
        _import(uid, EXP, text)
    item = {i["item_key"]: i for i in invoke("suggest_actions", uid, {"job_id": job_id})["items"]}[EXP]
    assert item["match"] == "variant" and item["variant"]["text"] == strong
    from harness.library import VARIANT_MATCH_FLOOR
    assert item["variant"]["score"] == item["best_score"] > 2 * VARIANT_MATCH_FLOOR
    assert item["approved_variants"] == 3 and item["source"] == "fallback"
    assert item["variant"]["cites"] == [EXP] and item["variant"]["status"] == "approved"


def test_a_variant_below_the_floor_is_no_match_and_an_item_with_none_is_no_match(env):
    from harness.library import VARIANT_MATCH_FLOOR

    uid, job_id, _ = env
    _import(uid, EXP, "Organized the annual office holiday celebration party")
    out = invoke("suggest_actions", uid, {"job_id": job_id})
    by_key = {i["item_key"]: i for i in out["items"]}
    assert out["floor"] == VARIANT_MATCH_FLOOR
    assert by_key[EXP]["match"] == "no_match" and by_key[EXP]["variant"] is None
    assert by_key[EXP]["best_score"] < VARIANT_MATCH_FLOOR and by_key[EXP]["approved_variants"] == 1
    assert by_key[EXP2]["match"] == "no_match" and by_key[EXP2]["approved_variants"] == 0
    assert by_key[EXP2]["best_score"] == 0.0


def test_with_uniform_weights_the_score_is_relevance_density():
    from agents.checks import relevance_density
    from harness.library import best_variant, job_terms, variant_score

    jd = "Build python pipelines for forecasting and analytics dashboards"
    from agents.ats_scorer import ATSScoringEngine
    keywords = ATSScoringEngine._extract_keywords(jd)
    terms = job_terms(None, jd)
    assert terms == {k: 1.0 for k in keywords}
    for text in ("Built python forecasting pipelines nightly", "Organized the holiday party"):
        assert variant_score(text, terms) == round(relevance_density(text, keywords), 4)
    # Weights count: a term at half weight is worth half a term at full weight.
    heavy = {"python": 1.0, "forecasting": 0.5}
    assert variant_score("python forecasting", heavy) == 0.75
    # No answer yet: all-zero weights fall back to uniform, as keyword coverage does.
    assert job_terms({"python": 0.0}, jd) == terms
    assert best_variant([], terms) == (None, 0.0)


def test_actions_are_the_valid_ops_with_uniform_propensities(env, isolated_engine):
    uid, job_id, _ = env
    first = invoke("suggest_actions", uid, {"job_id": job_id})
    assert first["node_id"] is None                      # no history: the version a first plan starts from
    by_key = {i["item_key"]: i for i in first["items"]}
    for item in first["items"]:
        ops = [a["op"] for a in item["actions"]]
        assert ops == [o for o in ("keep", "revise", "replace", "delete") if o in ops]   # OPS order
        assert sum(a["propensity"] for a in item["actions"]) == pytest.approx(1.0)
        assert len({a["propensity"] for a in item["actions"]}) == 1
    assert [a["op"] for a in by_key[EXP]["actions"]] == ["keep", "revise", "delete"]
    # Every project is already on the page, so there is none to swap in.
    assert "replace" not in [a["op"] for a in by_key[PROJ]["actions"]]

    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=uid, text="Never show the game project", polarity="suppress",
                             target_key=PROJ, scope_type="global", strength=5))
        s.add(UserPreference(user_id=uid, text="Always show the backend role", polarity="emphasize",
                             target_key=EXP2, scope_type="global", strength=5))
        s.commit()
    node_id = _committed(uid, job_id, [{"id": "drop", "op": "delete", "item_key": "proj:streamboard",
                                        "because": "user:fewer projects"}] + [
        {"id": "pin", "op": "delete", "item_key": PROJ, "because": "user:pinned away"}])
    second = invoke("suggest_actions", uid, {"job_id": job_id})
    by_key = {i["item_key"]: i for i in second["items"]}
    assert second["node_id"] == node_id and PROJ not in by_key        # the delete committed
    assert [a["op"] for a in by_key[EXP2]["actions"]] == ["keep", "revise"]      # emphasized: no delete
    assert [a["op"] for a in by_key["proj:semanticsearch-lite"]["actions"]] == [
        "keep", "revise", "replace", "delete"]                                    # a free project exists
    assert by_key["proj:semanticsearch-lite"]["actions"][2]["propensity"] == pytest.approx(0.25)


def test_suggest_actions_can_judge_any_version_of_the_job_and_is_deterministic(env):
    uid, job_id, _ = env
    _import(uid, EXP, "Built " + " ".join(_job_terms(uid, job_id)[:4]) + " pipelines")
    n0 = _committed(uid, job_id)
    n1 = _run(uid, {"job_id": job_id, "parent": n0, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less"}]})["node_id"]
    old = invoke("suggest_actions", uid, {"job_id": job_id, "node_id": n0})
    new = invoke("suggest_actions", uid, {"job_id": job_id})
    assert old["node_id"] == n0 and new["node_id"] == n1
    assert PROJ in [i["item_key"] for i in old["items"]]
    assert PROJ not in [i["item_key"] for i in new["items"]]
    assert json.dumps(invoke("suggest_actions", uid, {"job_id": job_id, "node_id": n0})) == json.dumps(old)


def test_suggest_actions_refuses_another_users_job_or_a_foreign_node(env, isolated_engine):
    uid, job_id, _ = env
    node_id = _committed(uid, job_id)
    stranger = _second_user(isolated_engine)
    assert invoke("suggest_actions", stranger, {"job_id": job_id})["error"]["code"] == "not_found"
    assert invoke("suggest_actions", uid, {"job_id": str(uuid4())})["error"]["code"] == "not_found"
    other_job = _task_job(uid, 2)["job_id"]
    assert invoke("suggest_actions", uid, {"job_id": other_job,
                                           "node_id": node_id})["error"]["code"] == "not_found"


# ── starting a revision from a variant: the plan ─────────────────────────────

def test_from_variant_names_one_variant_per_bullet_and_the_node_form_needs_one_bullet():
    from harness.program import Program

    bullet = {"text": "x", "cites": ["exp:a|b"]}
    ok = Program.model_validate({"job_id": "j", "nodes": [
        {"id": "a", "op": "revise", "item_key": EXP, "from_variant": "v1", "bullets": [bullet]}]})
    assert ok.nodes[0].bullets[0].from_variant == "v1"        # the shorthand lands on the bullet
    per_bullet = Program.model_validate({"job_id": "j", "nodes": [
        {"id": "a", "op": "revise", "item_key": EXP,
         "bullets": [{**bullet, "from_variant": "v1"}, bullet, {**bullet, "from_variant": "v2"}]}]})
    assert [b.from_variant for b in per_bullet.nodes[0].bullets] == ["v1", None, "v2"]

    with pytest.raises(ValidationError, match="exactly one bullet"):
        Program.model_validate({"job_id": "j", "nodes": [
            {"id": "a", "op": "revise", "item_key": EXP, "from_variant": "v1",
             "bullets": [bullet, bullet]}]})
    with pytest.raises(ValidationError, match="revise needs bullets|exactly one bullet"):
        Program.model_validate({"job_id": "j", "nodes": [
            {"id": "a", "op": "keep", "item_key": EXP, "from_variant": "v1"}]})
    with pytest.raises(ValidationError, match="name different variants"):
        Program.model_validate({"job_id": "j", "nodes": [
            {"id": "a", "op": "revise", "item_key": EXP, "from_variant": "v1",
             "bullets": [{**bullet, "from_variant": "v2"}]}]})


def test_a_bullet_that_names_an_approved_variant_starts_from_its_text_and_cites(env):
    uid, job_id, _ = env
    v = _import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"])
    out = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, _vid(v)))])
    node = out["nodes"][0]
    assert node["status"] == "accepted", node
    assert out["committed"] and out["metrics"]["final"]["guards"]["variant_drift"] == 0.0
    from harness import tree
    page = {e["title"]: e for e in tree.get_node(uid, out["node_id"])["content"]["experiences"]}
    item = page["Backend Software Engineer"]
    assert item["bullets"][3] == WOVEN and item["cites"][WOVEN] == [f"{EXP2}#b3"]   # the variant's own cites


def test_a_bullet_verbatim_an_approved_variant_needs_no_cites_at_all(env):
    uid, job_id, _ = env
    _import(uid, EXP2, WOVEN)
    bullets = _weave_bullets(uid)
    bullets[3] = {"text": WOVEN, "cites": []}
    out = _plan(uid, job_id, [_revise(EXP2, bullets)])
    assert out["nodes"][0]["status"] == "accepted", out["nodes"][0]


@pytest.mark.parametrize("setup,reason", [
    ("unknown", "unknown_variant"),
    ("draft", "variant_not_approved"),
    ("other_user", "unknown_variant"),
    ("wrong_item", "variant_wrong_item"),
])
def test_only_an_approved_variant_of_the_same_item_can_be_started_from(env, isolated_engine, setup, reason):
    uid, job_id, _ = env
    if setup == "unknown":
        vid = str(uuid4())
    elif setup == "draft":
        vid = _vid(_import(uid, EXP2, WOVEN))
        with Session(isolated_engine) as s:
            row = s.get(BulletVariant, UUID(vid))
            row.status = "draft"
            s.add(row)
            s.commit()
    elif setup == "other_user":
        stranger = _second_user(isolated_engine)
        with Session(isolated_engine) as s:
            row = BulletVariant(user_id=stranger, item_key=EXP2, text=WOVEN, status="approved",
                                cites=[EXP2])
            s.add(row)
            s.commit()
            vid = str(row.variant_id)
    else:
        vid = _vid(_import(uid, EXP, "Built something else entirely for the team"))
    out = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid))])
    node = out["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith(reason), node


# ── the edit-distance guard ──────────────────────────────────────────────────

def test_token_distance_is_the_normalized_edit_count():
    from harness.acceptance import token_distance

    assert token_distance("Built a service", "built a service.") == 0.0     # case and edge punctuation
    assert token_distance("a b c d", "a b x d") == 0.25                     # one substitution of four
    assert token_distance("a b c d", "a b") == 0.5                          # two deletions of four
    assert token_distance("a b", "x y z") == 1.0
    assert token_distance("", "") == 0.0 and token_distance("a b", "") == 1.0


def test_the_default_tolerance_allows_a_light_edit_and_stops_a_rewrite():
    """Why 0.35: a few swapped or woven words in a typical bullet are 0.1-0.25; a bullet that
    keeps the topic and rewrites the rest is 0.5 and up."""
    from harness.acceptance import DEFAULT_TOLERANCES, VARIANT_DRIFT_TOLERANCE, token_distance

    base = ("Built gradient boosted ranking models serving two million daily predictions "
            "with PyTorch and XGBoost")                                              # 14 tokens
    light = base.replace("Built", "Developed").replace("PyTorch", "PyTorch, Kafka")  # 2 edits
    woven = base + " using Airflow orchestration"                                    # 3 insertions
    rewrite = "Owned the ranking stack end to end, from features to serving and monitoring"
    assert DEFAULT_TOLERANCES["variant_drift"] == VARIANT_DRIFT_TOLERANCE == 0.35
    assert token_distance(base, light) == pytest.approx(2 / 15)
    assert token_distance(base, woven) == pytest.approx(3 / 17)
    assert token_distance(base, light) <= 0.25 and token_distance(base, woven) <= 0.25
    assert token_distance(base, rewrite) > 0.7


def test_drift_is_judged_against_the_tolerance_itself_not_the_parents_value():
    from harness.acceptance import accept

    def vec(drift):
        return {"gates": {}, "guards": {"variant_drift": drift}, "targets": {"coverage": 1.0},
                "report": {}}

    # A parent page already at 0.30 does not give the next node 0.30 of extra room.
    verdict = accept(vec(0.30), vec(0.50), improves=["coverage"])
    assert verdict["guard_regressions"] == {"variant_drift": 0.5}
    # And a node under the tolerance is fine whatever came before.
    assert accept(vec(0.0), vec(0.30), improves=["coverage"])["guard_regressions"] == {}
    # A tightened tolerance applies the same way.
    assert accept(vec(0.0), vec(0.30), improves=["coverage"],
                  tolerances={"variant_drift": 0.2})["guard_regressions"] == {"variant_drift": 0.3}
    assert accept({"gates": {}, "guards": {}, "targets": {"coverage": 0}, "report": {}},
                   vec(0.0), improves=["coverage"])["guard_regressions"] == {}     # a vector without it


def test_variant_drift_is_zero_when_no_bullet_names_a_variant():
    from harness.acceptance import Context, GUARDS, metric_vector

    content = {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": ["Built a thing"]}]}
    assert "variant_drift" in GUARDS
    assert metric_vector(content, Context(jd_text="x"))["guards"]["variant_drift"] == 0.0


LIGHT = WOVEN.replace("Led the migration", "Drove the migration").replace("billing and search", "billing, search and payments")


def test_a_light_edit_of_the_variant_is_kept_and_a_stray_one_is_reverted(env):
    from harness.acceptance import token_distance

    uid, job_id, _ = env
    v = _import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"])
    vid = _vid(v)
    assert 0 < token_distance(WOVEN, LIGHT) <= 0.35

    kept = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, LIGHT))], dry_run=True)
    assert kept["nodes"][0]["status"] == "accepted", kept["nodes"][0]
    assert 0 < kept["metrics"]["final"]["guards"]["variant_drift"] <= 0.35

    stray = ("Replaced a legacy monolith with services that integrate through Kafka topics "
             "while owning on-call duties and mentoring new hires across the team")
    assert token_distance(WOVEN, stray) > 0.35
    out = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, stray))], dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and node["reason"].startswith("guard: variant_drift regressed"), node
    assert out["metrics"]["final"]["guards"]["variant_drift"] == 0.0     # the page is as it was
    # The same words with no `from_variant` is a new bullet: the guard has nothing to measure.
    free = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, None, stray))], dry_run=True)
    assert free["metrics"]["final"]["guards"]["variant_drift"] == 0.0
    assert "variant_drift" not in (free["nodes"][0]["reason"] or "")


def test_a_node_may_tighten_the_drift_tolerance_and_never_loosen_it(env):
    uid, job_id, _ = env
    vid = _vid(_import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"]))
    bullets = _weave_bullets(uid, vid, LIGHT)
    tight = _revise(EXP2, bullets, accept={"improves": ["coverage"], "tolerances": {"variant_drift": 0.05}})
    out = _plan(uid, job_id, [tight], dry_run=True)
    assert out["nodes"][0]["status"] == "reverted"
    assert "variant_drift" in out["nodes"][0]["reason"]

    loose = _revise(EXP2, bullets, accept={"improves": ["coverage"], "tolerances": {"variant_drift": 0.9}})
    err = _plan(uid, job_id, [loose], dry_run=True)
    assert err["error"]["code"] == "invalid_program" and "variant_drift" in err["error"]["message"]
    assert "only tighten" in err["error"]["message"]


# ── gates on variant text ────────────────────────────────────────────────────

NUMERIC = "Cut forecast error by 37% across the retail demand models"
SRC = {"exp:engineer|acme": ["Built retail demand forecasting models"]}


def _page(bullet, cites):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": [bullet],
                             "cites": {bullet: cites}}]}


def test_the_support_check_skips_a_verbatim_variant_but_not_an_edited_one(auto):
    from harness.decisions.support import make_support_checker

    t = FakeTransport(answer_for=lambda s, w: choice_answer(
        "supported", {"supported": 0.97, "adds_unsupported": 0.02, "contradicts": 0.01}))
    auto.use(t)
    key = "exp:engineer|acme"
    approved = {key: {NUMERIC}}
    checker = make_support_checker(SRC, None, approved)
    assert checker(_page(NUMERIC, [f"{key}#b0"])) == [] and t.calls == []       # verbatim: not asked
    (finding,) = checker(_page(NUMERIC.replace("37%", "38%"), [f"{key}#b0"]))   # edited: checked
    assert finding["status"] == "checked" and len(t.calls) == 1
    # Not approved: the same text is checked like any new bullet.
    (finding,) = make_support_checker(SRC, None, {})(_page(NUMERIC, [f"{key}#b0"]))
    assert finding["status"] == "checked" and len(t.calls) == 2


def test_the_consistency_gate_skips_a_verbatim_variant_but_not_an_edited_one():
    from harness.acceptance import Context, consistency_violations

    key = "exp:engineer|acme"
    page = _page(NUMERIC, [f"{key}#b0"])
    plain = Context(jd_text="x", source_bullets=SRC, cite_status=lambda c: None)
    assert [v for v in consistency_violations(page, plain)] != []           # 37% is not in the evidence
    approved = Context(jd_text="x", source_bullets=SRC, cite_status=lambda c: None,
                       approved_variants={key: {NUMERIC}})
    assert consistency_violations(page, approved) == []                     # the user's own wording
    edited = _page(NUMERIC.replace("37%", "38%"), [f"{key}#b0"])
    assert consistency_violations(edited, approved) != []                   # checked normally


def test_through_the_plan_a_verbatim_variant_passes_consistency_and_an_edit_does_not(env):
    uid, job_id, _ = env
    text = WOVEN[:-1] + ", cutting deploy time by 63%."
    vid = _vid(_import(uid, EXP2, text, cites=[f"{EXP2}#b3"]))
    ok = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, text))], dry_run=True)
    assert ok["nodes"][0]["status"] == "accepted", ok["nodes"][0]
    assert not any(v.startswith("consistency:") for v in ok["metrics"]["final"]["gates"]["consistency"])
    edited = text.replace("63%", "64%")
    bad = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, edited))], dry_run=True)
    assert bad["nodes"][0]["status"] == "reverted"
    assert "consistency" in bad["nodes"][0]["reason"] and "64%" in bad["nodes"][0]["reason"]


def _jev(state, wire):
    """Scripted Jev: every cited bullet supported, no requirement covered."""
    if wire["type"] == "noul":
        return {"type": "noul", "noul": 0.02}
    return choice_answer("supported", {"supported": 0.97, "adds_unsupported": 0.02, "contradicts": 0.01})


def test_a_verbatim_variant_is_not_sent_to_jev_through_the_plan(auto, env):
    uid, job_id, _ = env
    t = FakeTransport(answer_for=_jev)
    auto.use(t)
    vid = _vid(_import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"]))
    out = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid))], dry_run=True)
    assert out["nodes"][0]["status"] == "accepted"
    asked = [c["state"]["bullet"] for c in t.calls if "bullet" in c["state"] and "evidence" in c["state"]]
    assert WOVEN not in asked

    lightly = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, LIGHT))], dry_run=True)
    assert lightly["nodes"][0]["status"] == "accepted"
    asked = [c["state"]["bullet"] for c in t.calls if "bullet" in c["state"] and "evidence" in c["state"]]
    assert LIGHT in asked                                  # an edited variant is checked normally
    (call,) = [c for c in t.calls if c["state"].get("bullet") == LIGHT and "evidence" in c["state"]]
    assert WOVEN in call["state"]["evidence"]              # and the variant it names is evidence


# ── the named variant is evidence for its edited bullet ──────────────────────

EDITED = NUMERIC + " every night"          # not verbatim, so checked; keeps the variant-only 37%


def test_the_variants_own_text_is_consistency_evidence_for_the_bullet_that_names_it():
    from harness.acceptance import Context, consistency_violations

    key = "exp:engineer|acme"
    page = _page(EDITED, [f"{key}#b0"])
    named = Context(jd_text="x", source_bullets=SRC, cite_status=lambda c: None,
                    approved_variants={key: {NUMERIC}},
                    variant_evidence={(key, EDITED): NUMERIC})
    assert consistency_violations(page, named) == []                   # 37% was approved in the variant
    plain = Context(jd_text="x", source_bullets=SRC, cite_status=lambda c: None,
                    approved_variants={key: {NUMERIC}})
    assert [v for v in consistency_violations(page, plain) if "37%" in v]    # no from_variant: flagged
    other = Context(jd_text="x", source_bullets=SRC, cite_status=lambda c: None,
                    approved_variants={key: {NUMERIC}},
                    variant_evidence={(key, "some other bullet"): NUMERIC})
    assert consistency_violations(page, other) != []                   # only the bullet that names it
    changed = _page(EDITED.replace("37%", "38%"), [f"{key}#b0"])
    assert consistency_violations(changed, Context(
        jd_text="x", source_bullets=SRC, cite_status=lambda c: None, approved_variants={key: {NUMERIC}},
        variant_evidence={(key, EDITED.replace("37%", "38%")): NUMERIC})) != []   # a new number still is


def test_the_support_state_carries_the_variant_text_and_only_for_the_bullet_that_names_it(auto):
    from harness.decisions.support import make_support_checker

    t = FakeTransport(answer_for=_jev)
    auto.use(t)
    key = "exp:engineer|acme"
    page = _page(EDITED, [f"{key}#b0"])
    make_support_checker(SRC, None, {key: {NUMERIC}}, {(key, EDITED): NUMERIC})(page)
    assert t.calls[-1]["state"]["evidence"] == ["Built retail demand forecasting models", NUMERIC]
    make_support_checker(SRC, None, {key: {NUMERIC}})(page)             # no from_variant: nothing extra
    assert t.calls[-1]["state"]["evidence"] == ["Built retail demand forecasting models"]
    assert len(t.calls) == 2                                              # different state, different key


def test_through_the_plan_a_kept_variant_number_passes_only_when_the_variant_is_named(env):
    uid, job_id, _ = env
    text = WOVEN[:-1] + ", cutting deploy time by 63%."
    vid = _vid(_import(uid, EXP2, text, cites=[f"{EXP2}#b3"]))
    edited = text.replace("Led the migration", "Drove the migration")
    named = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid, edited))], dry_run=True)
    assert named["nodes"][0]["status"] == "accepted", named["nodes"][0]
    assert not any("63%" in v for v in named["metrics"]["final"]["gates"]["consistency"])
    unnamed = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, None, edited))], dry_run=True)
    assert unnamed["nodes"][0]["status"] == "reverted" and "63%" in unnamed["nodes"][0]["reason"]
    # A draft variant is never evidence: naming it is refused outright.
    draft = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, str(uuid4()), edited))], dry_run=True)
    assert draft["nodes"][0]["status"] == "refused"


@pytest.mark.parametrize("term_in_variant", ["kafka"])
def test_a_negative_pin_still_blocks_variant_text_with_no_exception(env, isolated_engine, term_in_variant):
    uid, job_id, _ = env
    vid = _vid(_import(uid, EXP2, WOVEN, cites=[f"{EXP2}#b3"]))
    with Session(isolated_engine) as s:
        s.add(UserPreference(user_id=uid, text="Never mention Kafka", polarity="suppress",
                             target_term=term_in_variant, scope_type="global", strength=5))
        s.commit()
    out = _plan(uid, job_id, [_revise(EXP2, _weave_bullets(uid, vid))], dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "refused" and node["reason"].startswith("negative_pin")
    assert term_in_variant in node["reason"]


# ── role family ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,family", [
    ("Machine Learning Engineer Intern", "machine_learning"),
    ("AI/ML Engineer", "machine_learning"),
    ("Computer Vision & Machine Learning - Associate", "machine_learning"),
    ("Machine Learning Research Scientist", "machine_learning"),
    ("Jr. Data Scientist", "data_science"),
    ("Data Analyst Intern", "data_science"),
    ("Data Engineer", "data_engineering"),
    ("Analytics Engineer", "data_engineering"),
    ("Research Intern", "research"),
    ("Software Engineering Intern", "software_engineering"),
    ("Full-Stack Developer", "software_engineering"),
    ("Entry Level Software Developer", "software_engineering"),
    ("Product Manager", "product_management"),
    ("Product Designer", "design"),
    ("DevOps Engineer", "devops_infrastructure"),
    ("Site Reliability Engineer", "devops_infrastructure"),
    ("Security Engineer", "security"),
    ("Firmware Engineer", "hardware"),
    ("Barista", "other"),
    ("", "other"),
])
def test_the_title_map_is_deterministic_and_ends_in_other(title, family):
    from agents.extraction_schemas import RoleFamily
    from agents.job_card import role_family_from_title

    assert role_family_from_title(title) == family
    assert family in {f.value for f in RoleFamily}


def test_open_job_takes_the_role_family_from_the_host_first(env):
    uid, *_ = env
    out = _task_job(uid, 2, role_family="Research")           # the title says data science
    assert (out["role_family"], out["role_family_source"]) == ("research", "host")
    again = invoke("open_job", uid, {"job_id": out["job_id"]})      # re-opened with nothing new
    assert (again["role_family"], again["role_family_source"]) == ("research", "host")
    changed = invoke("open_job", uid, {"job_id": out["job_id"],
                                       "metadata": {"role_family": "data_science"}})
    assert (changed["role_family"], changed["role_family_source"]) == ("data_science", "host")


def test_open_job_falls_back_to_the_title_and_then_to_other(env):
    uid, job_id, _ = env
    by_title = _task_job(uid, 2)
    assert (by_title["role_family"], by_title["role_family_source"]) == ("data_science", "title")
    nothing = invoke("open_job", uid, {"jd_text": "Pour coffee all day, every day.",
                                       "metadata": {"title": "Barista", "company": "Beans"}})
    assert (nothing["role_family"], nothing["role_family_source"]) == ("other", "default")
    assert nothing["baseline"] is None
    # The family the env job got is recorded for the executor to use.
    with Session(db.engine) as s:
        assert s.get(JobRoleFamily, UUID(job_id)).role_family == "machine_learning"


def test_an_invalid_role_family_is_refused_with_the_valid_ones(env):
    uid, *_ = env
    out = invoke("open_job", uid, {"jd_text": "x", "metadata": {"title": "t", "company": "c",
                                                                  "role_family": "wizardry"}})
    assert out["error"]["code"] == "invalid_arguments"
    assert "data_science" in out["error"]["suggestions"] and "other" in out["error"]["suggestions"]


# ── track baselines ──────────────────────────────────────────────────────────

def test_save_baseline_pins_a_node_and_saving_the_track_again_replaces_it(env):
    uid, job_id, _ = env
    n0 = _committed(uid, job_id)
    first = invoke("save_baseline", uid, {"node_id": n0, "track": "Data Science"})
    assert (first["track"], first["node_id"], first["replaced"]) == ("data_science", n0, None)
    same = invoke("save_baseline", uid, {"node_id": n0, "track": "data-science"})
    assert same["track"] == "data_science" and same["replaced"] is None        # idempotent
    n1 = _run(uid, {"job_id": job_id, "parent": n0, "nodes": [
        {"id": "d", "op": "delete", "item_key": PROJ, "because": "user:less"}]})["node_id"]
    second = invoke("save_baseline", uid, {"node_id": n1, "track": "DATA_SCIENCE"})
    assert second["replaced"] == n0 and second["node_id"] == n1
    with Session(db.engine) as s:
        (row,) = s.exec(select(TrackBaseline).where(TrackBaseline.user_id == uid)).all()
        assert (row.track, str(row.node_id)) == ("data_science", n1)


def test_save_baseline_refuses_a_node_that_is_not_the_users_or_a_blank_track(env, isolated_engine):
    uid, job_id, _ = env
    n0 = _committed(uid, job_id)
    stranger = _second_user(isolated_engine)
    assert invoke("save_baseline", stranger, {"node_id": n0, "track": "x"})["error"]["code"] == "not_found"
    assert invoke("save_baseline", uid, {"node_id": str(uuid4()), "track": "x"})["error"]["code"] == "not_found"
    assert invoke("save_baseline", uid, {"node_id": n0, "track": "  "})["error"]["code"] == "invalid_arguments"


def _crafted_baseline(uid, job_id, track, edit):
    """Save the KG default, edited by `edit(content)`, as a node of `job_id` and pin it."""
    from database.models import JobDescription
    from harness import executor, tree

    with Session(db.engine) as s:
        job = s.get(JobDescription, UUID(job_id))
        s.expunge(job)
    kg = executor._KG(uid)
    content = executor.kg_default_content(uid, job.description, kg,
                                          executor.job_requirements(uid, UUID(job_id)))
    edit(content)
    head = tree.get_head(uid, job_id)["head"]
    node = tree.commit_node(uid, job_id, content=content, source="host",
                            expected_parent=head["node_id"] if head else None)
    assert invoke("save_baseline", uid, {"node_id": node["node_id"], "track": track})["node_id"]
    return node["node_id"], content


def _curate(content):
    """A baseline that differs from the whole KG: one curated bullet, two projects dropped."""
    bluefin = next(e for e in content["experiences"] if e["company"] == "Bluefin Software")
    bluefin["bullets"] = ["Curated: led the event-driven migration of billing."]
    bluefin["cites"] = {bluefin["bullets"][0]: [f"{EXP2}#b3"]}
    content["projects"] = [p for p in content["projects"] if p["name"] in ("StreamBoard", "LLM Resume Coach")]


def test_a_job_with_a_matching_track_starts_as_a_copy_of_the_baseline(env):
    from harness import tree

    uid, job_id, _ = env
    baseline, saved = _crafted_baseline(uid, job_id, "machine_learning", _curate)
    other = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models in python.",
                                     "metadata": {"title": "ML Engineer", "company": "Second Co",
                                                  "role_family": "machine_learning"}})
    assert other["baseline"] == {"track": "machine_learning", "node_id": baseline, "applies": True}

    out = _run(uid, {"job_id": other["job_id"], "nodes": []})
    assert out["committed"] and out["baseline"]["node_id"] == baseline
    assert out["baseline"]["track"] == "machine_learning" and out["baseline"]["source"] == "host"
    node = tree.get_node(uid, out["node_id"])
    assert node["parent_id"] is None and node["provenance"]["baseline"]["node_id"] == baseline
    got = node["content"]
    # Item content is the baseline's, not the knowledge graph's.
    assert [p["name"] for p in got["projects"]] == ["StreamBoard", "LLM Resume Coach"]
    assert got["projects"] == saved["projects"]
    assert got["experiences"] == saved["experiences"]
    assert "Curated: led the event-driven migration of billing." in [
        b for e in got["experiences"] for b in e["bullets"]]
    assert got["education"] == saved["education"]


def test_a_baseline_keeps_its_skill_set_and_re_ranks_it_for_the_new_posting(env):
    from harness.executor import _KG, baseline_content

    uid, job_id, _ = env
    crafted = {"experiences": [], "projects": [],
               "skills_ranked": [{"name": "Git", "category": "Tools", "score": 9.0},
                                 {"name": "Unity", "category": "Tools", "score": 8.0},
                                 {"name": "Kafka", "category": "Tools", "score": 7.0}]}
    jd = "Streaming platform work on Kafka. Kafka pipelines, Kafka consumers."
    reqs = [{"text": "Experience with Kafka streaming", "type": "required", "criticality": 5,
             "terms": ["kafka"], "ordinal": 0}]
    got = baseline_content(uid, jd, _KG(uid), reqs, crafted)
    names = [s["name"] for s in got["skills_ranked"]]
    assert names[0] == "Kafka" and names != ["Git", "Unity", "Kafka"]        # ranked for this posting
    assert set(names) <= {"Git", "Unity", "Kafka"}                           # the baseline's own set
    assert crafted["skills_ranked"][0]["name"] == "Git"                      # the baseline node is untouched
    # A baseline whose skills the KG no longer holds takes the KG's list.
    gone = baseline_content(uid, jd, _KG(uid), reqs, {"skills_ranked": [{"name": "Zzz"}]})
    assert [s["name"] for s in gone["skills_ranked"]][:1] == ["Kafka"] and len(gone["skills_ranked"]) > 3


def test_a_baseline_never_leaks_another_jobs_rule_answer_into_a_new_job(env):
    from harness import tree

    uid, job_id, _ = env
    edu = next(i["key"] for i in invoke("list_items", uid, {"kind": "education"})["items"])
    rule = invoke("upsert_items", uid, {"records": [{"kind": "rule", "data": {
        "item_key": edu, "field": "end_date", "question": "Does the role need enrollment after?",
        "value_if_yes": "2027-12", "value_if_no": "2027-06"}}]})["results"][0]
    rule_id = rule["key"].split(":", 1)[1]
    invoke("open_job", uid, {"job_id": job_id, "rule_answers": [{"rule_id": rule_id, "answer": True}]})
    baseline = _committed(uid, job_id)
    invoke("save_baseline", uid, {"node_id": baseline, "track": "data_science"})
    got = tree.get_node(uid, baseline)["content"]["education"][0]["end_date"]
    assert got == "2027-12"                                   # job 1's answer, in the baseline

    unanswered = invoke("open_job", uid, {"jd_text": "Data scientist role. Python.", "metadata": {
        "title": "Data Scientist", "company": "Fresh Co"}})
    assert unanswered["baseline"]["applies"] and unanswered["rules"][0]["status"] == "needs_answer"
    out = _run(uid, {"job_id": unanswered["job_id"], "nodes": []})
    start = tree.get_node(uid, out["node_id"])["content"]["education"][0]["end_date"]
    assert start != "2027-12" and out["rules_applied"] == []         # back to the stored value

    answered = invoke("open_job", uid, {"jd_text": "Data scientist role. Python.", "metadata": {
        "title": "Data Scientist", "company": "Answered Co"},
        "rule_answers": [{"rule_id": rule_id, "answer": False}]})
    out = _run(uid, {"job_id": answered["job_id"], "nodes": []})
    assert tree.get_node(uid, out["node_id"])["content"]["education"][0]["end_date"] == "2027-06"
    assert out["rules_applied"][0]["to"] == "2027-06"


def test_no_baseline_means_the_whole_kg_exactly_as_before(env):
    from harness import executor, tree

    uid, job_id, _ = env
    from database.models import JobDescription
    with Session(db.engine) as s:
        job = s.get(JobDescription, UUID(job_id))
        s.expunge(job)
    kg = executor._KG(uid)
    reqs = executor.job_requirements(uid, UUID(job_id))
    content, baseline = executor.start_content(uid, job, kg, reqs)
    assert baseline is None
    assert json.dumps(content, sort_keys=True) == json.dumps(
        executor.kg_default_content(uid, job.description, kg, reqs), sort_keys=True)
    out = _run(uid, {"job_id": job_id, "nodes": []})
    assert "baseline" not in out
    assert "baseline" not in tree.get_node(uid, out["node_id"])["provenance"]
    # The open_job result says so too.
    assert invoke("open_job", uid, {"job_id": job_id})["baseline"] is None


def test_only_the_track_named_for_the_role_family_is_chosen_and_never_another_users(env, isolated_engine):
    uid, job_id, _ = env
    n0 = _committed(uid, job_id)
    invoke("save_baseline", uid, {"node_id": n0, "track": "data_science"})    # the job is machine_learning
    assert invoke("open_job", uid, {"job_id": job_id})["baseline"] is None
    other = _task_job(uid, 2)                                                # a data science job
    assert other["baseline"]["track"] == "data_science"

    stranger = _second_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(TrackBaseline(user_id=stranger, track="machine_learning", node_id=UUID(n0),
                            job_id=UUID(job_id)))
        s.commit()
    assert invoke("open_job", uid, {"job_id": job_id})["baseline"] is None


def test_a_job_that_already_has_history_reports_the_baseline_without_applying_it(env):
    uid, job_id, _ = env
    n0 = _committed(uid, job_id)
    invoke("save_baseline", uid, {"node_id": n0, "track": "machine_learning"})
    out = invoke("open_job", uid, {"job_id": job_id})
    assert out["baseline"] == {"track": "machine_learning", "node_id": n0, "applies": False}


def test_deleting_a_job_removes_the_baselines_that_pinned_its_nodes(env):
    import services

    uid, job_id, _ = env
    n0 = _committed(uid, job_id)
    invoke("save_baseline", uid, {"node_id": n0, "track": "machine_learning"})
    assert services.delete_job(job_id) == "Job deleted."
    with Session(db.engine) as s:
        assert s.exec(select(TrackBaseline)).all() == [] and s.exec(select(JobRoleFamily)).all() == []


# ── the benchmark ────────────────────────────────────────────────────────────

def test_library_reuse_counts_verbatim_and_lightly_edited_variants_only():
    from harness.library import library_reuse

    variant = {"text": "Built gradient boosted ranking models serving two million daily predictions"}
    approved = {"exp:a|b": [variant]}
    page = {"experiences": [{"title": "A", "company": "B", "bullets": [
        variant["text"],                                                    # verbatim
        variant["text"].replace("Built", "Developed"),                      # one edit in ten: reused
        "Owned the ranking stack from features to serving and monitoring",  # not the variant
        ""]}], "projects": [{"name": "P", "bullets": ["Shipped a tool"]}]}
    out = library_reuse(page, approved)
    assert out == {"bullets": 4, "verbatim": 1, "edited": 1, "share": 0.5}
    assert library_reuse({}, {})["share"] is None


def test_the_scripted_host_uses_variants_and_reports_library_reuse(env):
    from eval.scripted_host import build_program, run_task

    uid, job_id, task = env
    plain = build_program(uid, job_id, task["description"])
    assert not [n for n in plain["nodes"] if n["id"].startswith("variant:")]   # no library: as before
    assert all("from_variant" not in b for n in plain["nodes"] for b in n.get("bullets") or [])

    top = _job_terms(uid, job_id)[:6]
    text = "Built " + " ".join(top) + " pipelines"
    vid = _vid(_import(uid, EXP, text))
    program = build_program(uid, job_id, task["description"])
    node = next(n for n in program["nodes"] if n["id"] == f"variant:{EXP}")
    assert node["bullets"][0] == {"text": text, "cites": [], "from_variant": vid}
    assert len(node["bullets"]) == 3 and program == build_program(uid, job_id, task["description"])

    out = run_task(uid, task)                        # the same posting, planned over the library
    assert out["result"]["committed"], out["result"]
    reuse = out["library_reuse"]
    assert reuse["verbatim"] >= 1 and 0 < reuse["share"] <= 1
    assert reuse["bullets"] > reuse["verbatim"]      # the rest of the page is source text
    # Report-only: nothing about it is in the metric vector, the targets or the gates.
    metrics = out["result"]["metrics"]["final"]
    assert "library_reuse" not in json.dumps(metrics)


def test_a_run_with_no_library_reports_no_reuse_and_the_same_program(env):
    from eval.scripted_host import page_library_reuse

    uid, job_id, _ = env
    assert page_library_reuse(uid, job_id) is None                  # no version yet
    _committed(uid, job_id)
    assert page_library_reuse(uid, job_id)["verbatim"] == 0
    assert page_library_reuse(uid, job_id)["share"] == 0.0
