"""The bullet library's two Jev decisions (issue #199): which approved variant fits a job, and
which saved track a job starts from.

Every Jev answer here is scripted on a fake transport or replayed from the cache; nothing calls
the API. The real answers behind the thresholds are the committed recordings
(`tests/test_library_labels.py`). With no key, mode `off` or an API error the #229 fallbacks run
unchanged, which these tests pin against what the rules compute on their own.
"""

import json
from uuid import UUID

import pytest
from sqlmodel import Session, select

import database.db as db
from database.models import JevDecision
from harness import library
from harness.contract import invoke
from harness.decisions import engine, library as jl, recordings
from harness.decisions.client import JevReplayMiss
from test_bullet_library import (  # noqa: F401  (helpers; `env` is a fixture)
    _committed, _crafted_baseline, _curate, _import, _job_terms, _source, _task_job,
)
from test_executor import EXP, EXP2, PROJ, _run, env  # noqa: F401
from test_jev import Boom, FakeTransport, auto, choice_answer  # noqa: F401  (fixture)

OLD_ITEM_KEYS = {"item_key", "title", "match", "variant", "best_score", "approved_variants",
                 "actions", "source"}


# ── scripting Jev ────────────────────────────────────────────────────────────

def jev(variant=None, track=None, variant_p=0.8, track_p=0.9, catch_p=None):
    """An `answer_for` for the fake transport. `variant(state, ids)` and `track(state, names)` return
    the option to choose (`no_match` / `none` allowed); the default is the first option. The rest of
    the probability is spread evenly over the other options, except that `catch_p` (when given) is what
    a variant question's `no_match` gets when it is not the choice."""
    def answer_for(state, wire):
        if wire["type"] == "noul":                       # a coverage or pin question in the same run
            return {"type": "noul", "noul": 0.9}
        options = list(wire["criteria"])
        if "no_match" in options:
            ids = [o for o in options if o != "no_match"]
            pick, p = (variant or (lambda s, i: i[0]))(state, ids), variant_p
        else:
            names = [o for o in options if o != "none"]
            pick, p = (track or (lambda s, n: n[0]))(state, names), track_p
        probs = {pick: p}
        if catch_p is not None and "no_match" in options and pick != "no_match":
            probs["no_match"] = catch_p
        others = [o for o in options if o not in probs]
        left = max(1.0 - sum(probs.values()), 0.0)
        for o in others:                                 # spread what is left evenly
            probs[o] = left / len(others)
        return choice_answer(pick, probs, confidence=p)
    return answer_for


def asked(transport, point):
    """The payloads of `point`'s questions the transport saw (a variant question has `no_match`)."""
    marker = "no_match" if point == jl.VARIANT_POINT else "none"
    return [c for c in transport.calls
            if any(marker in q["criteria"] for q in c["questions"].values())]


def _variants(uid, key, texts):
    return [_import(uid, key, t)["key"].split(":", 1)[1] for t in texts]


THREE = ["Built ranking models and shipped them to a production recommender",
         "Wrote data pipelines moving daily events into the warehouse",
         "Organized the annual office holiday celebration party"]


def _items(out):
    return {i["item_key"]: i for i in out["items"]}


# ── variant choice ───────────────────────────────────────────────────────────

def test_jevs_chosen_variant_and_its_propensities_come_through_suggest_actions(env, auto):
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)
    transport = FakeTransport(jev(variant=lambda s, i: ids[1], variant_p=0.7))
    auto.use(transport)
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["source"] == "jev" and item["match"] == "variant"
    assert item["variant"]["variant_id"] == ids[1] and item["variant"]["text"] == THREE[1]
    assert item["variant"]["score"] == 0.7 and item["best_score"] == 0.7
    assert item["approved_variants"] == 3
    # the whole distribution: every variant id and no_match, the picked one at Jev's probability
    assert set(item["propensity"]) == {*ids, "no_match"}
    assert item["propensity"][ids[1]] == 0.7
    assert sum(item["propensity"].values()) == pytest.approx(1.0, abs=1e-3)
    # the valid actions keep their uniform propensities
    assert sum(a["propensity"] for a in item["actions"]) == pytest.approx(1.0)


def test_the_question_is_one_choice_over_the_variants_with_a_narrow_state(env, auto):
    uid, job_id, task = env
    ids = _variants(uid, EXP, THREE)
    reqs = [{"text": f"Requirement number {i}", "type": "required", "criticality": 3, "terms": []}
            for i in range(10)] + [{"text": "Only mentioned", "type": "incidental", "criticality": 5, "terms": []}]
    assert invoke("open_job", uid, {"job_id": job_id, "requirements": reqs})["requirements"] == 11
    transport = FakeTransport(jev())
    auto.use(transport)
    invoke("suggest_actions", uid, {"job_id": job_id})
    (call,) = asked(transport, jl.VARIANT_POINT)
    state = call["state"]
    assert set(state) == {"job", "item"} and set(state["job"]) == {"title", "requirements"}
    assert state["job"]["title"] == task["title"]
    assert state["job"]["requirements"] == [f"Requirement number {i}" for i in range(8)]
    assert "description" not in json.dumps(state) and task["description"][:200] not in json.dumps(state)
    assert state["item"] == {"title": "Machine Learning Engineer @ Nimbus Analytics"}
    (wire,) = call["questions"].values()
    assert wire["type"] == "choice"
    assert wire["criteria"] == {**dict(zip(ids, THREE)), "no_match": None}      # text is the description
    assert "best fits this job" in wire["instructions"]


def test_the_gate_is_the_mass_on_any_variant_so_a_low_mass_is_no_match_and_reports_the_distribution(env, auto):
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)
    auto.use(FakeTransport(jev(variant=lambda s, i: ids[0], variant_p=0.3, catch_p=0.6)))   # mass 0.4
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["source"] == "jev" and item["match"] == "no_match" and item["variant"] is None
    assert item["best_score"] == pytest.approx(0.3, abs=1e-3)         # the variant Jev liked best
    assert item["propensity"][ids[0]] == pytest.approx(0.3, abs=1e-3)
    assert item["propensity"]["no_match"] == pytest.approx(0.6, abs=1e-3)
    assert set(item["propensity"]) == {*ids, "no_match"}


def test_jev_saying_no_match_is_no_match(env, auto):
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)
    auto.use(FakeTransport(jev(variant=lambda s, i: "no_match", variant_p=0.95)))
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["match"] == "no_match" and item["variant"] is None and item["source"] == "jev"
    assert item["propensity"]["no_match"] == 0.95
    assert item["best_score"] < 0.1 and set(item["propensity"]) == {*ids, "no_match"}


def test_two_close_phrasings_each_under_a_half_are_picked_when_together_they_clear_it(env, auto):
    """The reason for the gate: Jev splits its mass between two good phrasings of one bullet, so neither
    reaches 0.5 alone while `no_match` stays low."""
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)

    def split(state, wire):
        probs = {ids[0]: 0.38, ids[1]: 0.36, ids[2]: 0.01, "no_match": 0.25}
        return choice_answer(ids[0], probs, confidence=0.38)
    auto.use(FakeTransport(split))
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["match"] == "variant" and item["variant"]["variant_id"] == ids[0]
    assert item["variant"]["score"] == 0.38 and item["best_score"] == 0.38     # its own probability
    assert item["propensity"][ids[1]] == 0.36


def test_a_no_match_that_wins_the_argmax_narrowly_still_picks_the_likeliest_variant(env, auto):
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)
    probs = {ids[0]: 0.1, ids[1]: 0.41, ids[2]: 0.0, "no_match": 0.49}                # mass 0.51
    auto.use(FakeTransport(lambda s, w: choice_answer("no_match", probs, confidence=0.49)))
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["match"] == "variant" and item["variant"]["variant_id"] == ids[1]
    assert item["variant"]["score"] == 0.41


def test_a_mass_exactly_at_the_threshold_is_taken(env, auto):
    uid, job_id, _ = env
    ids = _variants(uid, EXP, THREE)
    auto.use(FakeTransport(jev(variant=lambda s, i: ids[2], variant_p=jl.TAU_VARIANT,
                               catch_p=1.0 - jl.TAU_VARIANT)))
    item = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert item["match"] == "variant" and item["variant"]["variant_id"] == ids[2]


def test_a_track_is_still_gated_on_its_own_probability(env, auto):
    """No close call to pool for a track: Jev's own choice must reach the threshold."""
    uid, job_id, _ = env
    _two_tracks(uid, job_id)
    probs = {"machine_learning": 0.4, "data_science": 0.4, "none": 0.2}              # mass 0.8, none above 0.5
    auto.use(FakeTransport(lambda s, w: choice_answer("machine_learning", probs, confidence=0.4)))
    out = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models.",
                                   "metadata": {"title": "ML Engineer", "company": "Second Co"}})
    assert out["baseline"] is None


def test_one_request_per_item_that_has_approved_variants_and_none_for_the_rest(env, auto):
    uid, job_id, _ = env
    _variants(uid, EXP, THREE)
    _variants(uid, PROJ, ["Built an arcade game engine with a physics loop"])
    transport = FakeTransport(jev())
    auto.use(transport)
    out = _items(invoke("suggest_actions", uid, {"job_id": job_id}))
    assert len(transport.calls) == 2                                  # EXP and PROJ; EXP2 has no variant
    assert {c["state"]["item"]["title"] for c in transport.calls} == {
        "Machine Learning Engineer @ Nimbus Analytics", out[PROJ]["title"]}
    assert out[EXP2]["source"] == "fallback" and out[EXP2]["match"] == "no_match"
    assert out[EXP2]["approved_variants"] == 0 and out[EXP2]["propensity"] is None


def test_an_item_with_no_approved_variants_asks_nothing(env, auto):
    uid, job_id, _ = env
    transport = FakeTransport(jev())
    auto.use(transport)
    out = invoke("suggest_actions", uid, {"job_id": job_id})
    assert transport.calls == []
    assert all(i["source"] == "fallback" and i["match"] == "no_match" and i["propensity"] is None
               for i in out["items"])
    # a draft is not approved, so it is not offered either
    from harness.library import promote_bullet
    n0 = _committed(uid, job_id)
    from harness import tree
    page = tree.get_node(uid, n0)["content"]
    bullet = page["experiences"][0]["bullets"][0]
    assert "variant" in promote_bullet(uid, n0, bullet)
    invoke("suggest_actions", uid, {"job_id": job_id})
    assert transport.calls == []


def test_a_second_run_replays_from_the_cache_and_a_changed_variant_set_asks_again(env, auto):
    uid, job_id, _ = env
    _variants(uid, EXP, THREE)
    transport = FakeTransport(jev())
    auto.use(transport)
    first = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert first["source"] == "jev" and len(transport.calls) == 1
    again = _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]
    assert again["source"] == "cache" and len(transport.calls) == 1
    assert {k: v for k, v in again.items() if k != "source"} == {k: v for k, v in first.items() if k != "source"}
    _import(uid, EXP, "Trained and monitored recommendation models in production")
    assert _items(invoke("suggest_actions", uid, {"job_id": job_id}))[EXP]["source"] == "jev"
    assert len(transport.calls) == 2


# ── the fallbacks, unchanged ─────────────────────────────────────────────────

def _overlap_expectation(uid, job_id, key):
    """What #229 computes on its own: the overlap pick and its score for one item."""
    from database.models import JobDescription
    import services

    with Session(db.engine) as s:
        text = s.get(JobDescription, UUID(job_id)).description
    terms = library.job_terms(services.resolve_keyword_weights(UUID(job_id), uid, text, persist=False), text)
    chosen, best = library.best_variant(library.approved_by_item(uid).get(key, []), terms)
    return chosen, best


@pytest.mark.parametrize("how", ["no_key", "mode_off", "api_error"])
def test_with_no_jev_answer_suggest_actions_is_exactly_the_overlap_fallback(env, auto, monkeypatch, how):
    uid, job_id, _ = env
    top = _job_terms(uid, job_id)[:6]
    strong = "Built " + " ".join(top) + " pipelines"
    for text in ("Organized the annual office holiday celebration party", strong):
        _import(uid, EXP, text)
    _import(uid, PROJ, "Built an arcade game engine with a physics loop")
    if how == "mode_off":
        monkeypatch.setenv("ART_JEV_MODE", "off")
        auto.use(Boom())
    elif how == "api_error":
        auto.use(FakeTransport(script=[(401, {"message": "nope"})] * 8))
    out = invoke("suggest_actions", uid, {"job_id": job_id})
    assert out["floor"] == library.VARIANT_MATCH_FLOOR
    for item in out["items"]:
        chosen, best = _overlap_expectation(uid, job_id, item["item_key"])
        assert item["source"] == "fallback" and item["propensity"] is None
        assert item["variant"] == (None if chosen is None else {
            k: v for k, v in chosen.items()})                          # byte for byte, score included
        assert item["best_score"] == best
        assert item["match"] == ("variant" if chosen else "no_match")
    assert _items(out)[EXP]["variant"]["text"] == strong
    # the library-level result has exactly #229's keys: nothing added for the fallback
    raw = library.suggest_actions(uid, job_id)
    assert all(set(i) == OLD_ITEM_KEYS for i in raw["items"]) and set(raw) == {"job_id", "node_id", "floor", "items"}


def test_an_api_error_costs_one_request_and_everything_after_it_falls_back(env, auto):
    uid, job_id, _ = env
    _variants(uid, EXP, THREE)
    transport = FakeTransport(script=[(500, {"message": "boom"})])
    auto.use(transport)
    out = invoke("suggest_actions", uid, {"job_id": job_id})
    assert _items(out)[EXP]["source"] == "fallback"
    assert engine.stats()[jl.VARIANT_POINT]["reasons"] == {"api_error:500": 1}


# ── track baseline ───────────────────────────────────────────────────────────

def _two_tracks(uid, job_id):
    """The env job's version saved as machine_learning, and a curated one as data_science."""
    ml, _ = _crafted_baseline(uid, job_id, "machine_learning", lambda c: None)
    ds, _ = _crafted_baseline(uid, job_id, "data_science", _curate)
    return ml, ds


def test_open_job_picks_the_baseline_with_jev_and_records_which_one_ran(env, auto):
    uid, job_id, task = env
    ml, ds = _two_tracks(uid, job_id)
    transport = FakeTransport(jev(track=lambda s, names: "data_science", track_p=0.88))
    auto.use(transport)
    # a machine learning title: the role-family lookup would say machine_learning
    out = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models in python.",
                                   "metadata": {"title": "ML Engineer", "company": "Second Co"}})
    assert out["role_family"] == "machine_learning" and out["role_family_source"] == "title"
    assert out["baseline"] == {"track": "data_science", "node_id": ds, "applies": True,
                               "source": "jev", "p": 0.88}
    (call,) = asked(transport, jl.BASELINE_POINT)
    assert call["state"]["title"] == "ML Engineer" and "requirements" in call["state"]
    (wire,) = call["questions"].values()
    assert set(wire["criteria"]) == {"machine_learning", "data_science", "none"} and wire["criteria"]["none"] is None
    assert task["title"] in wire["criteria"]["machine_learning"]       # the title of the job the baseline came from
    assert "machine learning roles" in wire["criteria"]["machine_learning"]
    # asked again: the cache answers, and says so
    again = invoke("open_job", uid, {"job_id": out["job_id"]})
    assert again["baseline"]["source"] == "cache" and again["baseline"]["track"] == "data_science"
    assert again["baseline"]["p"] == 0.88 and len(asked(transport, jl.BASELINE_POINT)) == 1


def test_the_first_plan_starts_from_the_track_jev_chose(env, auto):
    from harness import tree

    uid, job_id, _ = env
    _, ds = _two_tracks(uid, job_id)
    auto.use(FakeTransport(jev(track=lambda s, names: "data_science")))
    other = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models in python.",
                                     "metadata": {"title": "ML Engineer", "company": "Second Co"}})
    out = _run(uid, {"job_id": other["job_id"], "nodes": []})
    assert out["committed"] and out["baseline"]["node_id"] == ds
    assert out["baseline"]["track"] == "data_science" and out["baseline"]["source"] in ("jev", "cache")
    assert tree.get_node(uid, out["node_id"])["provenance"]["baseline"]["node_id"] == ds


def test_jev_saying_none_or_a_pick_below_the_threshold_is_no_baseline(env, auto):
    uid, job_id, _ = env
    _two_tracks(uid, job_id)
    body = {"jd_text": "Machine learning engineer: build ranking models in python.",
            "metadata": {"title": "ML Engineer", "company": "Second Co"}}
    auto.use(FakeTransport(jev(track=lambda s, names: "none", track_p=0.97)))
    assert invoke("open_job", uid, body)["baseline"] is None          # even though machine_learning is saved
    auto.use(FakeTransport(jev(track=lambda s, names: "machine_learning", track_p=jl.TAU_BASELINE - 0.05)))
    body["metadata"] = {"title": "ML Engineer II", "company": "Second Co"}
    assert invoke("open_job", uid, {**body, "jd_text": body["jd_text"] + " Also pytorch."})["baseline"] is None


def test_the_host_role_family_wins_for_an_exact_track_name_without_asking_jev(env, auto):
    uid, job_id, _ = env
    ml, ds = _two_tracks(uid, job_id)
    transport = FakeTransport(jev(track=lambda s, names: "data_science"))
    auto.use(transport)
    out = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models in python.",
                                   "metadata": {"title": "ML Engineer", "company": "Second Co",
                                                "role_family": "machine_learning"}})
    assert out["baseline"] == {"track": "machine_learning", "node_id": ml, "applies": True,
                               "source": "host", "p": None}
    assert asked(transport, jl.BASELINE_POINT) == []
    # a host family that is no saved track is not a baseline: Jev is asked
    out = invoke("open_job", uid, {"jd_text": "Research scientist: study ranking.",
                                   "metadata": {"title": "Research Scientist", "company": "Third Co",
                                                "role_family": "research"}})
    assert out["baseline"]["source"] == "jev" and out["baseline"]["track"] == "data_science"
    assert len(asked(transport, jl.BASELINE_POINT)) == 1


def test_a_job_with_no_saved_baseline_asks_jev_nothing(env, auto):
    uid, job_id, _ = env
    transport = FakeTransport(jev())
    auto.use(transport)
    out = invoke("open_job", uid, {"job_id": job_id})
    assert out["baseline"] is None and transport.calls == []
    _task_job(uid, 2)
    assert transport.calls == []
    assert jl.BASELINE_POINT not in engine.stats()


@pytest.mark.parametrize("how", ["no_key", "mode_off", "api_error"])
def test_with_no_jev_answer_open_job_is_the_role_family_lookup(env, auto, monkeypatch, how):
    uid, job_id, _ = env
    ml, ds = _two_tracks(uid, job_id)
    if how == "mode_off":
        monkeypatch.setenv("ART_JEV_MODE", "off")
        auto.use(Boom())
    elif how == "api_error":
        auto.use(FakeTransport(script=[(503, {"message": "down"})] * 8))
    # the role family is machine_learning by title: that track, whatever else is saved
    out = invoke("open_job", uid, {"jd_text": "Machine learning engineer: build ranking models in python.",
                                   "metadata": {"title": "ML Engineer", "company": "Second Co"}})
    assert out["baseline"] == {"track": "machine_learning", "node_id": ml, "applies": True,
                               "source": "fallback", "p": None}
    # no track of that name: none, as in #229
    out = invoke("open_job", uid, {"jd_text": "Pour coffee all day.",
                                   "metadata": {"title": "Barista", "company": "Beans"}})
    assert out["baseline"] is None
    chosen = library.choose_baseline(uid, UUID(job_id))
    assert chosen["track"] == "machine_learning" and chosen["source"] == "fallback"
    assert chosen["role_family"] == "machine_learning" and chosen["family_source"] == "title"


# ── the cache replays both decisions ─────────────────────────────────────────

def test_a_rerun_replays_both_decisions_from_the_cache_at_a_100_percent_hit_rate(env, auto, monkeypatch):
    """The acceptance criterion: record a run through the scripted host, wipe the cache, import the
    recordings, and run it again in replay mode with a transport that fails if called."""
    from eval.scripted_host import run_task
    from eval.tailoring_benchmark import load_tasks

    uid, job_id, task = env
    _two_tracks(uid, job_id)
    _variants(uid, EXP, _source(uid, EXP)[:2])
    _variants(uid, PROJ, _source(uid, PROJ)[:1])
    auto.use(FakeTransport(jev(track=lambda s, names: "data_science",
                               variant=lambda s, ids: ids[0])))
    first = run_task(uid, load_tasks(limit=5)[2])                      # a new job: opens, plans, executes
    assert first["result"]["committed"], first["result"]
    assert any(n["id"].startswith("variant:") for n in first["program"]["nodes"])   # Jev's pick led the plan
    assert first["result"]["baseline"]["track"] == "data_science"
    assert first["result"]["baseline"]["source"] in ("jev", "cache")
    live = engine.stats()
    assert live[jl.VARIANT_POINT]["jev"] >= 1 and live[jl.BASELINE_POINT]["jev"] == 1

    doc = recordings.export_recordings()
    assert {d["point"] for d in doc["decisions"]} >= {jl.VARIANT_POINT, jl.BASELINE_POINT}
    with Session(db.engine) as s:
        for row in s.exec(select(JevDecision)).all():
            s.delete(row)
        s.commit()
    recordings.import_recordings(doc)

    monkeypatch.setenv("ART_JEV_MODE", "replay")
    monkeypatch.setattr(engine, "get_client", lambda: pytest.fail("replay reached for a client"))
    engine.reset_stats()
    again = run_task(uid, load_tasks(limit=5)[2])
    assert again["program"]["nodes"] == first["program"]["nodes"] and again["result"]["committed"]
    st = engine.stats()
    for point in (jl.VARIANT_POINT, jl.BASELINE_POINT):
        assert st[point]["hit_rate"] == 1.0 and st[point]["jev"] == 0 and st[point]["fallback"] == 0
        assert st[point]["requests"] == 0


def test_replay_mode_raises_on_a_decision_it_never_recorded(env, auto, monkeypatch):
    uid, job_id, _ = env
    _variants(uid, EXP, THREE)
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    with pytest.raises(JevReplayMiss):
        invoke("suggest_actions", uid, {"job_id": job_id})


# ── the state and the questions ──────────────────────────────────────────────

def test_requirements_in_the_state_are_the_top_eight_by_criticality_then_posting_order():
    reqs = [{"text": f"Requirement {i}", "type": "required", "criticality": c}
            for i, c in enumerate([2, 5, 3, 5, 4, 4, 1, 3, 5, 2, 4])]
    reqs.insert(3, {"text": "Mentioned in passing", "type": "incidental", "criticality": 5})
    reqs.append({"text": "x" * 500, "type": "preferred", "criticality": 5})
    top = jl.top_requirements(reqs)
    assert len(top) == jl.MAX_REQUIREMENTS == 8
    assert top[:4] == ["Requirement 1", "Requirement 3", "Requirement 8", "x" * jl.REQUIREMENT_MAX_CHARS]
    assert "Mentioned in passing" not in top
    assert jl.top_requirements([]) == [] and jl.top_requirements(None) == []


def test_a_variant_question_needs_no_variant_to_be_skipped_and_keys_options_by_id():
    q = jl.variant_question([{"variant_id": "id-1", "text": " two   spaces "},
                             {"variant_id": "id-2", "text": "other"}])
    assert q.version == jl.VARIANT_VERSION == "variant_choice@v1"
    assert q.wire()["criteria"] == {"id-1": "two spaces", "id-2": "other", "no_match": None}


def test_a_track_called_none_cannot_collide_with_the_catch_all():
    q, track_of = jl.track_question([{"track": "none", "title": "Odd"}, {"track": "data_science", "title": ""}])
    assert q.version == jl.BASELINE_VERSION == "track_baseline@v1"
    assert set(q.wire()["criteria"]) == {"none (track)", "data_science", "none"}
    assert track_of["none (track)"] == "none"
    assert q.wire()["criteria"]["data_science"] == "A saved resume track for data science roles."


def test_the_decisions_never_ask_for_an_empty_option_set(auto):
    transport = FakeTransport(jev())
    auto.use(transport)
    assert jl.ask_variant("t", [], "item", []) is None and jl.ask_track("t", [], []) is None
    assert transport.calls == []
