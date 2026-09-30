"""Semantic requirement coverage (issue #126): a Jev-backed target beside the literal `coverage`.

For each (bullet, requirement) Jev answers "does this bullet show the candidate meets the
requirement?"; a requirement is covered when some bullet scores at least `TAU_COVER`, and
`semantic_coverage` is the criticality-weighted share of required and preferred requirements
covered. Every answer here is scripted by a fake transport, so nothing touches the network; the
real recorded answers are exercised in `tests/test_coverage_labels.py`.
"""

import json
from uuid import UUID

import pytest

from harness.acceptance import Context, TARGETS, accept, metric_vector
from harness.decisions import coverage, engine
from harness.decisions.client import JevReplayMiss
from harness.decisions.coverage import (
    TAU_COVER, VERSION, build_state, disagreement, eligible_requirements, make_coverage_checker,
    node_detail, question_for, score_of, summary,
)
from test_executor import EXP, EXP2, _run, _strip, env  # noqa: F401  (fixture)
from test_jev import Boom, FakeTransport, auto, choice_answer  # noqa: F401  (fixture)

ETL = "Built ETL pipelines processing 2M records daily."
DATA_ENG = "Experience with data engineering at scale."
EAGER = "Eager to learn Kubernetes and apply it to the team's training jobs."
K8S = "Experience with Kubernetes."


def noul(p):
    return {"type": "noul", "noul": p}


def scripted(yes=(), default=0.02):
    """The scripted Jev: p = 0.95 when a bullet contains `bullet_part` and the question names
    `requirement_part`, else `default`. Other points that share the transport get a benign answer."""
    def answer_for(state, wire):
        if wire["type"] == "choice":                                   # the support check
            return choice_answer("supported", {"supported": 0.97, "adds_unsupported": 0.02, "contradicts": 0.01})
        if not wire["instructions"].startswith("Does this bullet show"):
            return noul(default)
        for bullet_part, requirement_part in yes:
            if bullet_part in state["bullet"] and requirement_part in wire["instructions"]:
                return noul(0.95)
        return noul(default)
    return answer_for


def cover_calls(transport):
    """The requests that asked a coverage question (the support check shares the transport)."""
    return [c for c in transport.calls
            if any(w["instructions"].startswith("Does this bullet show") for w in c["questions"].values())]


def page(*bullets, skills=()):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": list(bullets)}],
            "projects": [], "skills_ranked": [{"name": s} for s in skills]}


def req(text, rtype="required", criticality=3, terms=(), ordinal=None):
    out = {"text": text, "type": rtype, "criticality": criticality, "terms": list(terms)}
    if ordinal is not None:
        out["ordinal"] = ordinal
    return out


# ── the question ─────────────────────────────────────────────────────────────

def test_the_question_is_versioned_positive_a_noul_and_says_interest_does_not_count():
    q = question_for(DATA_ENG)
    assert q.version == VERSION == "requirement_covered@v1" and q.point == "requirement_covered" and q.kind == "noul"
    assert q.instructions.startswith(
        'Does this bullet show that the candidate meets this requirement: "Experience with data engineering at scale"?')
    assert "eager to learn" in q.instructions and "do not meet a requirement" in q.instructions
    assert "avoid" not in q.instructions.lower()                     # Jev reads negation literally
    assert q.wire()["criteria"]["true"] and q.wire()["criteria"]["false"]
    assert 0.0 < TAU_COVER < 1.0


def test_the_state_is_the_bullet_and_nothing_else():
    assert build_state("  Built   ETL\npipelines ") == {"bullet": "Built ETL pipelines"}


# ── which requirements, and the formula ──────────────────────────────────────

def test_incidental_requirements_are_excluded_and_a_missing_type_is_required():
    rows = eligible_requirements([
        req("Required one.", "required", 5), req("Preferred one.", "preferred", 1),
        req("Mentioned in passing.", "incidental", 5), {"text": "No type given.", "criticality": 9},
        req("", "required"), {"type": "required"}])
    assert [(r["text"], r["type"], r["criticality"]) for r in rows] == [
        ("Required one.", "required", 5), ("Preferred one.", "preferred", 1), ("No type given.", "required", 5)]
    assert make_coverage_checker([req("Only incidental.", "incidental")]) is None     # nothing to ask
    assert make_coverage_checker([]) is None


def test_incidental_requirements_are_never_sent(auto):
    t = FakeTransport(answer_for=scripted())
    auto.use(t)
    checker = make_coverage_checker([req("Has Python experience.", "required"), req("Free lunch is provided.", "incidental"),
                                     req("Knows Go.", "preferred")])
    result = checker(page("Wrote a Go service."))
    assert result["of"] == 2 and [r["text"] for r in result["requirements"]] == ["Has Python experience.", "Knows Go."]
    (call,) = t.calls
    assert len(call["questions"]) == 2
    assert not any("lunch" in w["instructions"] for w in call["questions"].values())


def test_the_score_is_the_criticality_weighted_share_of_covered_requirements():
    rows = [{"criticality": 5, "covered": True}, {"criticality": 3, "covered": False}, {"criticality": 2, "covered": True}]
    assert score_of(rows) == 70.0                                    # 100 * (5 + 2) / 10
    assert score_of([{"criticality": 3, "covered": False}]) == 0.0
    assert score_of([]) is None


def test_a_requirement_is_covered_when_any_one_bullet_reaches_the_threshold(auto):
    def answer_for(state, wire):
        if "data engineering" in wire["instructions"]:
            return noul(0.90 if state["bullet"].startswith("Built ETL") else 0.1)
        return noul(TAU_COVER - 0.01 if state["bullet"].startswith("Wrote docs") else 0.1)
    auto.use(FakeTransport(answer_for=answer_for))
    checker = make_coverage_checker([req(DATA_ENG, criticality=4, ordinal=7), req("Writes documentation.", criticality=1, ordinal=8)])
    result = checker(page(ETL, "Wrote docs for the API.", "Reviewed pull requests."))
    assert [(r["requirement"], r["covered"]) for r in result["requirements"]] == [(7, True), (8, False)]
    assert result["score"] == 80.0 and result["covered"] == 1 and result["of"] == 2
    assert result["requirements"][1]["p"] == round(TAU_COVER - 0.01, 4)     # the best bullet's score is reported
    at_boundary = make_coverage_checker([req(DATA_ENG)], tau=0.90)(page(ETL))
    assert at_boundary["requirements"][0]["covered"] is True                 # `>=`, not `>`


# ── the acceptance cases ─────────────────────────────────────────────────────

def test_the_etl_bullet_covers_data_engineering_at_scale_though_the_literal_score_is_zero(auto):
    auto.use(FakeTransport(answer_for=scripted([("ETL pipelines", "data engineering at scale")])))
    checker = make_coverage_checker([req(DATA_ENG, terms=["data engineering", "scale"])])
    ctx = Context(jd_text=DATA_ENG, coverage_checker=checker)
    content = page(ETL)
    vector = metric_vector(content, ctx)
    assert vector["targets"]["coverage"] == 0.0                      # substring matching sees nothing
    assert vector["targets"]["semantic_coverage"] == 100.0           # and the requirement is plainly met
    (entry,) = disagreement(checker(content), content)["semantic_only"]
    assert entry["missing"] == ["data engineering", "scale"]         # keyword-weave candidates: the claim is true


def test_eager_to_learn_does_not_cover_experience_with_it(auto):
    auto.use(FakeTransport(answer_for=scripted()))                   # Jev answers low for the aspiration
    checker = make_coverage_checker([req(K8S, terms=["kubernetes"], ordinal=0)])
    content = page(EAGER)
    result = checker(content)
    assert result["requirements"][0]["covered"] is False and result["score"] == 0.0
    vector = metric_vector(content, Context(jd_text=K8S, coverage_checker=checker))
    assert vector["targets"]["coverage"] > 0 and vector["targets"]["semantic_coverage"] == 0.0


def test_literal_and_semantic_are_separately_inspectable_and_never_combined(auto):
    auto.use(FakeTransport(answer_for=scripted([("ETL", "data engineering")])))
    checker = make_coverage_checker([req(DATA_ENG, terms=["data engineering"])])
    with_jev = metric_vector(page(ETL), Context(jd_text=DATA_ENG, coverage_checker=checker))
    without = metric_vector(page(ETL), Context(jd_text=DATA_ENG))
    assert with_jev["targets"]["coverage"] == without["targets"]["coverage"]     # the literal one is untouched
    assert with_jev["targets"]["semantic_coverage"] != with_jev["targets"]["coverage"]
    assert "semantic_coverage" not in with_jev["report"] and "semantic_coverage" not in with_jev["guards"]
    assert TARGETS == ("coverage", "relevance_density", "semantic_coverage")
    assert with_jev["report"] == without["report"]                   # the ATS composite does not move with it


def test_per_requirement_disagreement_is_exposed_both_ways(auto):
    auto.use(FakeTransport(answer_for=scripted([("ETL", "data engineering")])))
    reqs = [req(DATA_ENG, terms=["data engineering", "scale"], ordinal=0),       # met, in none of its words
            req(K8S, terms=["kubernetes"], ordinal=1),                            # the word is there, the work is not
            req("Experience with Go.", terms=["go"], ordinal=2),                  # neither
            req("Some requirement with no terms.", ordinal=3)]
    checker = make_coverage_checker(reqs)
    content = page(ETL, EAGER, skills=["Kubernetes"])
    result = checker(content)
    dis = disagreement(result, content)
    assert [e["requirement"] for e in dis["semantic_only"]] == [0]
    assert dis["semantic_only"][0] == {"requirement": 0, "text": DATA_ENG, "p": 0.95, "missing": ["data engineering", "scale"]}
    assert [e["requirement"] for e in dis["literal_only"]] == [1]              # the stuffing signature
    assert dis["literal_only"][0]["present"] == ["kubernetes"]
    block = summary(result, content)
    assert block["covered"] == 1 and block["of"] == 4 and block["tau_cover"] == TAU_COVER
    assert block["semantic_only"] == dis["semantic_only"] and block["literal_only"] == dis["literal_only"]
    assert summary({"status": "unchecked"}, content) is None and disagreement({"status": "none"}, content) == {
        "semantic_only": [], "literal_only": []}


def test_the_skills_line_counts_for_literal_presence_but_never_as_evidence(auto):
    auto.use(FakeTransport(answer_for=scripted()))
    checker = make_coverage_checker([req(K8S, terms=["kubernetes"])])
    content = page("Wrote a Go service.", skills=["Kubernetes"])
    assert checker(content)["requirements"][0]["covered"] is False
    assert disagreement(checker(content), content)["literal_only"][0]["present"] == ["kubernetes"]


def test_node_detail_reports_what_a_node_gained_and_lost(auto):
    auto.use(FakeTransport(answer_for=scripted([("ETL", "data engineering"), ("Go service", "Experience with Go")])))
    checker = make_coverage_checker([req(DATA_ENG, ordinal=0), req("Experience with Go.", terms=["go"], ordinal=1)])
    before, after = page(ETL, "Wrote docs."), page("Wrote a Go service.", "Wrote docs.")
    detail = node_detail(checker(before), checker(after), after)
    assert detail == {"covered": 1, "of": 2, "gained": [1], "lost": [0], "semantic_only": [], "literal_only": []}
    assert node_detail(checker(before), {"status": "unchecked"}, after) is None


# ── the target in the acceptance rule ────────────────────────────────────────

def _vector(literal, relevance, semantic=None):
    targets = {"coverage": literal, "relevance_density": relevance}
    if semantic is not None:
        targets["semantic_coverage"] = semantic
    return {"gates": {}, "guards": {}, "targets": targets, "report": {}}


def test_a_node_that_only_improves_semantic_coverage_is_accepted():
    before, after = _vector(40.0, 0.06, 50.0), _vector(40.0, 0.06, 75.0)
    verdict = accept(before, after)
    assert verdict["accepted"] and verdict["improved"] == ["semantic_coverage"] and verdict["reason"] is None
    assert verdict["deltas"] == {"coverage": 0.0, "relevance_density": 0.0, "semantic_coverage": 25.0}
    assert accept(before, after, improves=["semantic_coverage"])["accepted"]
    assert not accept(before, after, improves=["coverage"])["accepted"]            # a node may name its own targets
    assert not accept(before, _vector(40.0, 0.06, 50.0))["accepted"]                # flat: nothing improved
    stuffed = accept(before, _vector(40.0, 0.06, 40.0))
    assert not stuffed["accepted"] and stuffed["reason"] == (
        "no_target_improved: coverage, relevance_density, semantic_coverage")


def test_with_no_semantic_answer_the_target_is_absent_not_zero():
    before, after = _vector(40.0, 0.06), _vector(40.0, 0.06)
    verdict = accept(before, after)
    assert verdict["reason"] == "no_target_improved: coverage, relevance_density"            # exactly main's text
    assert set(verdict["deltas"]) == {"coverage", "relevance_density"}
    # A node that names only the absent target is judged on the literal ones, not reverted for it.
    assert accept(before, _vector(41.0, 0.06), improves=["semantic_coverage"])["accepted"]
    # An answer on one side only is not a comparison.
    assert set(accept(_vector(40.0, 0.06, 50.0), _vector(40.0, 0.06))["deltas"]) == {"coverage", "relevance_density"}
    assert not accept(_vector(40.0, 0.06), _vector(40.0, 0.06, 90.0))["accepted"]


@pytest.mark.parametrize("how", ["off", "no_key", "api_error"])
def test_without_jev_the_target_is_absent_and_the_vector_is_main(auto, monkeypatch, how):
    content = page(ETL, "Wrote docs.")
    if how == "off":
        monkeypatch.setenv("ART_JEV_MODE", "off")
        auto.use(Boom())
    elif how == "no_key":
        monkeypatch.setattr(engine, "get_client", lambda: None)
    else:
        t = FakeTransport(script=[(500, {"message": "down"})] * 9)
        auto.use(t, max_retries=0)
    checker = make_coverage_checker([req(DATA_ENG), req(K8S)])
    result = checker(content)
    assert result["status"] == "unchecked" and result["score"] is None and result["requirements"] == []
    with_checker = metric_vector(content, Context(jd_text=DATA_ENG, coverage_checker=checker))
    assert with_checker == metric_vector(content, Context(jd_text=DATA_ENG))
    assert "semantic_coverage" not in with_checker["targets"]
    assert summary(result, content) is None and node_detail(result, result, content) is None


def test_after_the_first_unanswered_bullet_the_checker_stays_down_and_asks_once(auto):
    t = FakeTransport(script=[(500, {"message": "down"})] * 9)
    auto.use(t, max_retries=0)
    checker = make_coverage_checker([req(DATA_ENG)])
    for _ in range(3):
        assert checker(page(ETL, "Wrote docs."))["status"] == "unchecked"
    assert len(t.calls) == 1                                           # not one failing request per node


def test_a_page_with_no_bullets_asks_nothing_and_adds_no_target(auto):
    t = FakeTransport(answer_for=scripted())
    auto.use(t)
    result = make_coverage_checker([req(DATA_ENG)])({"experiences": [], "projects": []})
    assert result["status"] == "none" and t.calls == []
    assert "semantic_coverage" not in metric_vector({"experiences": [], "projects": []},
                                                    Context(jd_text="x", coverage_checker=lambda c: result))["targets"]


# ── what is sent, and what is cached ─────────────────────────────────────────

def test_one_request_per_bullet_carries_every_requirement_and_only_the_bullet(auto):
    t = FakeTransport(answer_for=scripted())
    auto.use(t)
    checker = make_coverage_checker([req(DATA_ENG), req(K8S, "preferred"), req("Knows Go.")])
    checker(page(ETL, "Wrote docs."))
    assert len(t.calls) == 2                                           # one per bullet, not per (bullet, requirement)
    for call, bullet in zip(t.calls, (ETL, "Wrote docs.")):
        assert call["state"] == build_state(bullet) and set(call["state"]) == {"bullet"}
        assert len(call["questions"]) == 3                             # all the requirements, as questions
    assert sorted(w["instructions"] for w in t.calls[0]["questions"].values()) == sorted(
        question_for(r).wire()["instructions"] for r in (DATA_ENG, K8S, "Knows Go."))


def test_an_unchanged_bullet_makes_no_new_call_and_a_changed_one_makes_one(auto):
    t = FakeTransport(answer_for=scripted())
    auto.use(t)
    checker = make_coverage_checker([req(DATA_ENG), req(K8S)])
    base = page(ETL, "Wrote docs.", "Reviewed pull requests.")
    checker(base)
    assert len(t.calls) == 3
    checker(base), checker(page("Wrote docs.", ETL, "Reviewed pull requests."))            # reordered: nothing new
    assert len(t.calls) == 3
    checker(page(ETL, "Wrote docs.", "Reviewed pull requests carefully."))                 # one bullet changed
    assert len(t.calls) == 4 and t.calls[-1]["state"] == build_state("Reviewed pull requests carefully.")
    again = make_coverage_checker([req(DATA_ENG), req(K8S)])                               # a new checker: the engine's cache
    again(base)
    assert len(t.calls) == 4
    assert engine.stats()["requirement_covered"]["cache"] >= 6


def test_a_new_requirement_is_the_only_new_question_for_a_bullet_already_asked(auto):
    t = FakeTransport(answer_for=scripted())
    auto.use(t)
    make_coverage_checker([req(DATA_ENG)])(page(ETL))
    make_coverage_checker([req(DATA_ENG), req(K8S)])(page(ETL))
    assert len(t.calls) == 2 and len(t.calls[1]["questions"]) == 1
    assert K8S.rstrip(".") in next(iter(t.calls[1]["questions"].values()))["instructions"]


# ── through the executor ─────────────────────────────────────────────────────

REQS = [req("Experience building and deploying event-driven services.", "required", 4, ["event-driven", "kafka"]),
        req("Experience with Kubernetes.", "preferred", 2, ["kubernetes"]),
        req("Experience monitoring machine learning systems.", "required", 3, ["observability"]),
        req("Free lunch is provided.", "incidental", 1, ["lunch"])]
# Replaces the feature-store bullet with one that runs jobs on an orchestration platform, in none of the
# posting's words. The page's skills line already lists Kubernetes, so the word is there and only Jev sees the work.
ORCH = "Ran the team's retraining jobs on a container orchestration platform."
YES = [("Led the migration from a monolith", "event-driven services"), ("container orchestration", "Kubernetes"),
       ("Set up model monitoring", "monitoring machine learning")]


def _open(env, requirements=REQS):
    from harness.contract import invoke

    uid, job_id, _ = env
    out = invoke("open_job", uid, {"job_id": job_id, "requirements": requirements})
    assert out["error"] is None and out["requirements"] == len(requirements)
    return uid, job_id


def _reword(uid, job_id, text=ORCH):
    """A plan that rewrites one machine-learning bullet, adding none of the posting's words."""
    from harness.executor import _KG

    bullets = [{"text": b, "cites": [f"{EXP}#b{i}"]} for i, b in enumerate(_KG(uid).source_bullets[EXP])]
    bullets[1]["text"] = text
    return {"job_id": job_id, "nodes": [{"id": "reword", "op": "revise", "item_key": EXP, "strategy": "reframe",
                                          "bullets": bullets}]}


def test_a_reword_that_only_makes_a_requirement_evident_is_accepted_on_semantic_coverage_alone(auto, env):
    uid, job_id = _open(env)
    t = FakeTransport(answer_for=scripted(YES))
    auto.use(t)
    out = _run(uid, _reword(uid, job_id), dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "accepted", node
    assert node["improved"] == ["semantic_coverage"]           # the literal targets did not move up: this alone kept it
    assert node["deltas"]["coverage"] <= 0 and node["deltas"]["semantic_coverage"] > 0
    assert node["semantic"] == {"covered": 3, "of": 3, "gained": [1], "lost": [], "semantic_only": [2], "literal_only": []}
    base, final = out["metrics"]["base"]["targets"], out["metrics"]["final"]["targets"]
    assert (base["semantic_coverage"], final["semantic_coverage"]) == (77.7778, 100.0)      # 100 * 7/9, then 9/9
    assert set(base) == {"coverage", "relevance_density", "semantic_coverage"}
    block = out["semantic_coverage"]
    assert block["score"] == 100.0 and block["covered"] == 3 and block["of"] == 3
    # Covered in other words, and its term is not on the page: a keyword-weave candidate.
    assert block["semantic_only"] == [{"requirement": 2, "text": "Experience monitoring machine learning systems.",
                                       "p": 0.95, "missing": ["observability"]}]
    assert block["literal_only"] == []                         # "kubernetes" is on the skills line and now evidenced


def test_the_same_reword_with_no_key_is_reverted_because_no_literal_target_moved(env, monkeypatch):
    uid, job_id = _open(env)
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)
    out = _run(uid, _reword(uid, job_id), dry_run=True)
    node = out["nodes"][0]
    assert node["status"] == "reverted" and node["reason"] == "no_target_improved: coverage, relevance_density"
    assert "semantic" not in node and "semantic_coverage" not in out
    assert set(node["deltas"]) == {"coverage", "relevance_density"}


def test_the_finalize_block_names_a_stuffed_requirement(auto, env):
    uid, job_id = _open(env)
    stuffed = "Retraining jobs on Airflow and Kubernetes, with Kubernetes metrics."
    t = FakeTransport(answer_for=scripted([(m, r) for m, r in YES if "container" not in m]))
    auto.use(t)
    out = _run(uid, _reword(uid, job_id, stuffed), dry_run=True)
    block = out["semantic_coverage"]
    assert [e["requirement"] for e in block["literal_only"]] == [1] and block["literal_only"][0]["present"] == ["kubernetes"]
    assert block["covered"] == 2 and block["of"] == 3 and block["score"] == 77.7778      # the word is on the page, the work is not
    assert out["nodes"][0]["semantic"]["literal_only"] == [1] and out["nodes"][0]["semantic"]["gained"] == []


def test_the_contract_carries_the_node_detail_and_the_finalize_block(auto, env):
    from harness.contract import invoke

    uid, job_id = _open(env)
    auto.use(FakeTransport(answer_for=scripted(YES)))
    out = invoke("execute_plan", uid, {"program": _reword(uid, job_id), "dry_run": True})
    assert out["nodes"][0]["semantic"]["gained"] == [1]
    assert out["semantic_coverage"]["score"] == 100.0 and out["semantic_coverage"]["semantic_only"][0]["requirement"] == 2
    no_jev = invoke("execute_plan", uid, {"program": {"job_id": job_id, "nodes": []}, "dry_run": True})
    assert no_jev["semantic_coverage"] is not None                      # the recorded answers above are cached


def test_a_program_may_name_semantic_coverage_as_a_target():
    from harness.program import Program

    node = {"id": "a", "op": "keep", "item_key": EXP, "accept": {"improves": ["semantic_coverage"]}}
    assert Program.model_validate({"job_id": "j", "nodes": [node]}).nodes[0].accept.improves == ["semantic_coverage"]


def test_no_requirements_means_no_question_and_no_block(auto, env):
    uid, job_id, _ = env
    def no_coverage_question(state, wire):
        assert not wire["instructions"].startswith("Does this bullet show"), "a coverage question with no requirements"
        return scripted()(state, wire)
    auto.use(FakeTransport(answer_for=no_coverage_question))
    out = _run(uid, {"job_id": job_id, "nodes": []}, dry_run=True)
    assert "semantic_coverage" not in out and "semantic_coverage" not in out["metrics"]["base"]["targets"]
    uid, job_id = _open(env, [req("Free lunch is provided.", "incidental")])
    out = _run(uid, {"job_id": job_id, "nodes": []}, dry_run=True)
    assert "semantic_coverage" not in out


def test_key_unset_leaves_the_plan_exactly_as_it_was_before_the_target(env, monkeypatch):
    """Acceptance: with no key (or `off`) node and finalize output equal main's, byte for byte."""
    from harness import executor

    uid, job_id = _open(env)
    program = _reword(uid, job_id)
    program["nodes"].append({"id": "drop", "op": "delete", "item_key": "proj:pixel adventure", "because": "user:less games"})
    with monkeypatch.context() as m:
        m.setattr(executor, "make_coverage_checker", lambda *a, **k: None)           # what main computes
        before_jev = _run(uid, program, dry_run=True)
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)                            # the key is unset
    no_key = _run(uid, program, dry_run=True)
    monkeypatch.setenv("ART_JEV_MODE", "off")
    off = _run(uid, program, dry_run=True)
    assert len({json.dumps(_strip(r), sort_keys=True) for r in (before_jev, no_key, off)}) == 1
    assert "semantic_coverage" not in no_key and all("semantic" not in n for n in no_key["nodes"])
    assert "semantic_coverage" not in no_key["metrics"]["final"]["targets"]
    assert all(set(n["deltas"]) == {"coverage", "relevance_density"} for n in no_key["nodes"] if "deltas" in n)


def test_a_recorded_run_replays_at_a_full_hit_rate_with_no_live_call(auto, env, monkeypatch):
    """Acceptance: record once with scripted answers, then replay with a transport that fails if called."""
    uid, job_id = _open(env)
    auto.use(FakeTransport(answer_for=scripted(YES)))
    program = _reword(uid, job_id)
    recorded = _run(uid, program, dry_run=True)
    st = engine.stats()["requirement_covered"]
    assert st["jev"] > 2 and st["fallback"] == 0
    answered = st["jev"] + st["cache"]

    engine.reset_stats()
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    replayed = _run(uid, program, dry_run=True)
    assert json.dumps(_strip(replayed), sort_keys=True) == json.dumps(_strip(recorded), sort_keys=True)
    st = engine.stats()["requirement_covered"]
    assert st["hit_rate"] == 1.0 and st["cache"] == answered and st["jev"] == 0 and st["fallback"] == 0

    program["nodes"][0]["bullets"][3]["text"] += " And more."                           # a bullet the recording never saw
    with pytest.raises(JevReplayMiss):
        _run(uid, program, dry_run=True)


def test_a_second_run_over_the_same_page_makes_no_new_calls(auto, env):
    uid, job_id = _open(env)
    t = FakeTransport(answer_for=scripted(YES))
    auto.use(t)
    program = _reword(uid, job_id)
    first = _run(uid, program, dry_run=True)
    calls = len(cover_calls(t))
    assert calls > 0
    second = _run(uid, program, dry_run=True)
    assert len(cover_calls(t)) == calls                                                  # every (bullet, requirement) cached
    assert json.dumps(_strip(first), sort_keys=True) == json.dumps(_strip(second), sort_keys=True)


def test_a_first_run_asks_once_per_distinct_bullet_with_every_requirement_in_the_request(auto, env):
    uid, job_id = _open(env)
    t = FakeTransport(answer_for=scripted(YES))
    auto.use(t)
    _run(uid, {"job_id": job_id, "nodes": []}, dry_run=True)
    calls = cover_calls(t)
    assert len(calls) == len({c["state"]["bullet"] for c in calls})                     # one request per distinct bullet
    assert all(len(c["questions"]) == 3 for c in calls)                                  # the three eligible requirements
