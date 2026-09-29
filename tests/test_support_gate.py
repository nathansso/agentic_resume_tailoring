"""The cited-bullet support check and its gate (issue #193).

The `citations` gate proves a bullet cites real evidence; this asks Jev whether
the evidence says what the bullet says. Every answer here is scripted: a fake
transport plays a synthetic labelled set (`tests/fixtures/support_pairs.json`),
so nothing touches the network and the recordings are stand-ins until a
TypeSafe key is available to record real ones.
"""

import json
from pathlib import Path

import pytest

from harness.acceptance import Context, metric_vector, support_violations
from harness.decisions import engine, support
from harness.decisions.client import JevReplayMiss
from harness.decisions.support import (
    QUESTION, TAU_BLOCK, TAU_REVIEW, build_state, make_support_checker, resolve_evidence,
    reviews, violations,
)
from test_executor import EXP2, _run, _strip, _woven, env  # noqa: F401  (fixture)
from test_jev import Boom, FakeTransport, auto, choice_answer  # noqa: F401  (fixture)

PAIRS = json.loads((Path(__file__).parent / "fixtures" / "support_pairs.json")
                   .read_text(encoding="utf-8"))["pairs"]
BY_BULLET = {p["bullet"]: p for p in PAIRS}
KEY = "exp:engineer|acme"


def oracle(state, wire):
    """The scripted Jev: answers each synthetic pair with its recorded probabilities."""
    probs = BY_BULLET[state["bullet"]]["probabilities"]
    pick = max(probs, key=probs.get)
    return choice_answer(pick, probs, confidence=probs[pick])


def page(bullets, cites=None):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": list(bullets),
                             "cites": cites or {}}]}


def pair_page(pair):
    n = len(pair["evidence"])
    return (page([pair["bullet"]], {pair["bullet"]: [f"{KEY}#b{i}" for i in range(n)]}),
            {KEY: pair["evidence"]})


# ── the question and the fixture ─────────────────────────────────────────────

def test_the_question_is_versioned_and_offers_the_three_labels():
    assert QUESTION.version == "support@v1" and QUESTION.point == "support"
    assert set(QUESTION.options) == {"supported", "adds_unsupported", "contradicts"}
    assert "evidence" in QUESTION.instructions and "only what the bullet claims" in QUESTION.instructions
    assert 0.0 < TAU_REVIEW < TAU_BLOCK <= 1.0
    assert (TAU_BLOCK, TAU_REVIEW) == (0.85, 0.35)       # fitted (#237); eval/support_labels/REPORT.md


def test_the_synthetic_set_covers_weaves_inflation_invented_outcomes_and_contradictions():
    kinds = {p["kind"] for p in PAIRS}
    assert {"keyword weave grounded in a cite", "role inflation", "invented outcome",
            "contradiction", "uncertain band"} <= kinds
    assert {p["label"] for p in PAIRS} == set(QUESTION.options)
    assert all(max(p["probabilities"], key=p["probabilities"].get) == p["label"]
               for p in PAIRS)


# ── recorded answers land on the right label and gate outcome ────────────────

@pytest.mark.parametrize("pair", PAIRS, ids=[p["id"] for p in PAIRS])
def test_each_pair_lands_on_its_label_and_gate_outcome(auto, pair):
    t = FakeTransport(answer_for=oracle)
    auto.use(t)
    content, sources = pair_page(pair)
    base = page([pair["original"]]) if pair.get("original") else None
    checker = make_support_checker(sources, base)

    (finding,) = checker(content)
    assert finding["status"] == "checked" and finding["label"] == pair["label"]
    assert finding["source"] == "jev" and finding["item"] == KEY

    # Jev sees the cited evidence, the bullet and the original: nothing else.
    (call,) = t.calls
    assert call["state"] == build_state(pair["evidence"], pair["bullet"], pair.get("original"))
    assert set(call["state"]) == {"evidence", "bullet"} | ({"original"} if pair.get("original") else set())
    assert list(call["questions"].values()) == [QUESTION.wire()]

    findings = [finding]
    expect = pair["outcome"]
    if expect == "pass":
        assert violations(findings) == [] and reviews(findings) == []
    elif expect.startswith("block:"):
        (v,) = violations(findings)
        assert v.startswith(expect.split(":")[1] + ":" + KEY) and reviews(findings) == []
    else:
        assert violations(findings) == [] and [r["label"] for r in reviews(findings)] == [pair["label"]]

    # Through the gate: the same outcome lands in `faithfulness`.
    ctx = Context(jd_text="python", support_checker=checker)
    assert support_violations(content, ctx) == violations(findings)
    assert metric_vector(content, ctx)["gates"]["faithfulness"] == violations(findings)
    assert len(t.calls) == 1                     # memoized: the gate did not ask again


def test_thresholds_block_at_tau_block_and_review_in_the_band():
    def f(label, p, status="checked"):
        return {"item": KEY, "bullet": "b", "status": status, "label": label, "p": p,
                "probabilities": {label: p}}

    assert violations([f("contradicts", TAU_BLOCK)]) == [f'contradicts:{KEY}: "b"']
    assert violations([f("adds_unsupported", TAU_BLOCK)]) == [f'unsupported:{KEY}: "b"']
    just_under = round(TAU_BLOCK - 0.01, 4)
    assert violations([f("contradicts", just_under)]) == []
    assert reviews([f("contradicts", just_under)])[0]["p"] == just_under
    assert reviews([f("adds_unsupported", TAU_REVIEW)]) != []
    assert reviews([f("adds_unsupported", round(TAU_REVIEW - 0.01, 4))]) == []
    assert reviews([f("contradicts", TAU_BLOCK)]) == []          # blocked, not "review"
    assert violations([f("supported", 0.99)]) == [] and reviews([f("supported", 0.99)]) == []
    assert violations([f("contradicts", 0.99, status="unchecked")]) == []


def _split(adds, contra, sup=None, pick=None):
    probs = {"supported": round(1 - adds - contra, 4) if sup is None else sup,
             "adds_unsupported": adds, "contradicts": contra}
    pick = pick or max(probs, key=probs.get)
    return {"item": KEY, "bullet": "b", "status": "checked", "label": pick, "p": probs[pick],
            "probabilities": probs}


def test_an_answer_split_across_the_blocking_labels_blocks_on_their_sum():
    """`contra_r_python`: Jev said adds_unsupported 0.51 / contradicts 0.49. Each label alone is
    under the threshold, but the bullet is not supported either way, so it blocks (#237)."""
    split = _split(0.51, 0.49)
    assert violations([split]) == [f'unsupported:{KEY}: "b"']       # the stronger label names it
    assert violations([_split(0.40, 0.60)]) == [f'contradicts:{KEY}: "b"']
    assert reviews([split]) == []                                     # blocked, not "review"
    # Each label alone under the threshold, the sum over it.
    assert 0.5 < TAU_BLOCK and max(0.51, 0.49) < TAU_BLOCK <= 0.51 + 0.49


def test_a_supported_answer_does_not_block_and_a_hedged_one_lands_in_review():
    assert violations([_split(0.04, 0.01)]) == [] and reviews([_split(0.04, 0.01)]) == []
    assert violations([_split(0.15, 0.0)]) == [] and reviews([_split(0.15, 0.0)]) == []
    hedged = _split(0.60, 0.0)                                         # p(supported) 0.4
    assert violations([hedged]) == []
    assert [r["label"] for r in reviews([hedged])] == ["adds_unsupported"]
    assert reviews([hedged])[0]["p"] == 0.6
    # The score is 1 - p(supported): rounding keeps 0.84 + 0.01 equal to 0.85.
    assert violations([_split(0.84, 0.01)]) != [] and TAU_BLOCK == 0.85


def test_a_finding_without_probabilities_is_scored_by_the_picks_p():
    def f(label, p):
        return {"item": KEY, "bullet": "b", "status": "checked", "label": label, "p": p,
                "probabilities": None}
    assert violations([f("contradicts", 0.9)]) != [] and violations([f("supported", 0.99)]) == []
    assert violations([f("contradicts", None)]) == []


# ── what is (not) sent to Jev ────────────────────────────────────────────────

def test_a_verbatim_source_bullet_is_never_checked(auto):
    auto.use(Boom())
    sources = {KEY: ["Built the ingest service.", "Cut latency by half."]}
    content = page(sources[KEY], {b: [f"{KEY}#b{i}"] for i, b in enumerate(sources[KEY])})
    assert make_support_checker(sources)(content) == []
    # Whitespace does not make it a new bullet, and neither does dropping its cite:
    # a user's own edit in the editor is theirs.
    assert make_support_checker(sources)(page(["Built the  ingest service."])) == []


def test_an_uncited_bullet_is_the_citations_gates_job_and_never_sent(auto):
    auto.use(Boom())
    checker = make_support_checker({KEY: ["Built the ingest service."]})
    assert checker(page(["Scaled the team to 12 engineers."])) == []


def test_cites_that_name_no_bullet_text_are_unchecked_not_guessed(auto):
    auto.use(Boom())
    checker = make_support_checker({KEY: ["Built the ingest service."]})
    (f,) = checker(page(["Used Python daily."], {"Used Python daily.": ["skill:python"]}))
    assert (f["status"], f["reason"]) == ("unchecked", "no_evidence_text")
    assert violations([f]) == [] and reviews([f]) == []


def test_evidence_is_the_cited_bullets_only_sorted_and_deduplicated():
    sb = {"exp:a|b": ["Zero.", "One.", "Two."], "proj:p": ["Alpha.", "Beta."]}
    assert resolve_evidence(["exp:a|b#b2", "exp:a|b#b0", "exp:a|b#b2"], sb) == ["Zero.", "Two."]
    assert resolve_evidence(["proj:p"], sb) == ["Alpha.", "Beta."]      # an item cite: all its bullets
    assert resolve_evidence(["exp:a|b#b9", "skill:x", "edu:y", "", None], sb) == []
    assert resolve_evidence(["EXP:A|B#B1"], sb) == ["One."]


def test_the_same_evidence_in_any_order_is_one_cached_decision(auto):
    t = FakeTransport(answer_for=oracle)
    auto.use(t)
    pair = PAIRS[0]
    sources = {KEY: ["Other.", pair["evidence"][0]]}
    for cites in ([f"{KEY}#b0", f"{KEY}#b1"], [f"{KEY}#b1", f"{KEY}#b0"]):
        content = page([pair["bullet"]], {pair["bullet"]: cites})
        assert make_support_checker(sources)(content)[0]["label"] == "supported"
    assert len(t.calls) == 1 and engine.stats()["support"]["cache"] == 1


def test_original_is_the_most_similar_base_bullet_when_it_is_similar_enough():
    orig = support._original("Led the migration from a monolith to services.",
                             ["Wrote unit tests.", "Contributed to the migration from a monolith to services."])
    assert orig == "Contributed to the migration from a monolith to services."
    assert support._original("Led the migration.", ["Wrote unit tests."]) is None
    assert support._original("Same.", ["Same."]) is None                # itself is not the original


# ── no key, mode off, API error: unchecked, no violation ─────────────────────

@pytest.mark.parametrize("how", ["off", "no_key", "api_error"])
def test_without_jev_the_finding_is_unchecked_and_adds_nothing(auto, monkeypatch, how):
    pair = PAIRS[3]                                    # an inflation that would block
    content, sources = pair_page(pair)
    if how == "off":
        monkeypatch.setenv("ART_JEV_MODE", "off")
        auto.use(Boom())
    elif how == "no_key":
        monkeypatch.setattr(engine, "get_client", lambda: None)
    else:
        auto.use(FakeTransport(script=[(500, {"message": "down"})] * 5), max_retries=0)
    (f,) = make_support_checker(sources)(content)
    assert f["status"] == "unchecked" and f["label"] is None
    assert support_violations(content, Context(jd_text="x", support_checker=make_support_checker(sources))) == []


# ── through the executor ─────────────────────────────────────────────────────

WOVEN = "Led the migration from a monolith to event-driven services using Kafka to integrate billing and search."


def run_weave(env, probs=None, **kw):
    """The #197 weave plan (one reworded bullet citing its source), with Jev's
    answer for that bullet scripted as `probs`."""
    from harness.executor import _KG

    uid, job_id, _ = env
    program = {"job_id": job_id, "nodes": [
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "keywords": ["integrate"], "bullets": _woven(_KG(uid).source_bullets[EXP2])}]}
    return uid, program, _run(uid, program, **kw)


def scripted(probs):
    def answer_for(state, wire):
        p = probs if state["bullet"] == WOVEN else {"supported": 0.97, "adds_unsupported": 0.02,
                                                     "contradicts": 0.01}
        return choice_answer(max(p, key=p.get), p)
    return answer_for


def test_a_supported_weave_is_kept_and_reports_what_was_checked(auto, env):
    from harness.executor import _KG

    t = FakeTransport(answer_for=scripted({"supported": 0.95, "adds_unsupported": 0.04,
                                           "contradicts": 0.01}))
    auto.use(t)
    uid, _, out = run_weave(env)
    node = out["nodes"][0]
    assert node["status"] == "accepted" and out["committed"], node
    assert "review" not in node and out["support"] == {"checked": 1, "review": []}
    (call,) = t.calls
    assert call["state"]["evidence"] == [_KG(uid).source_bullets[EXP2][3]]     # the cited bullet
    assert call["state"]["bullet"] == WOVEN
    assert out["metrics"]["final"]["gates"]["faithfulness"] == []


@pytest.mark.parametrize("probs,gate", [
    ({"supported": 0.05, "adds_unsupported": 0.93, "contradicts": 0.02}, "unsupported:"),
    ({"supported": 0.02, "adds_unsupported": 0.08, "contradicts": 0.90}, "contradicts:"),
])
def test_an_unsupported_or_contradicted_bullet_reverts_its_node(auto, env, probs, gate):
    auto.use(FakeTransport(answer_for=scripted(probs)))
    _, _, out = run_weave(env)
    node = out["nodes"][0]
    assert node["status"] == "reverted"
    assert node["reason"].startswith("hard_gate: faithfulness") and gate in node["reason"]
    assert WOVEN[:30] in node["reason"]
    # The revert leaves the page as it was, so the plan still commits and Jev's
    # verdict on the reverted bullet is not reported as a page-level check.
    assert out["committed"] and "support" not in out


def test_the_uncertain_band_is_surfaced_as_review_not_blocked(auto, env):
    auto.use(FakeTransport(answer_for=scripted(
        {"supported": 0.41, "adds_unsupported": 0.55, "contradicts": 0.04})))
    _, _, out = run_weave(env)
    node = out["nodes"][0]
    assert node["status"] == "accepted"
    assert node["review"] == [{"item": EXP2, "bullet": WOVEN[:79] + "…", "label": "adds_unsupported",
                               "p": 0.59}]        # 0.55 + 0.04: the score is 1 - p(supported)
    assert out["support"]["checked"] == 1 and out["support"]["review"] == node["review"]
    assert out["metrics"]["final"]["gates"]["faithfulness"] == []       # not a gate violation


def test_the_contract_carries_review_and_support(auto, env):
    from harness.contract import invoke

    auto.use(FakeTransport(answer_for=scripted(
        {"supported": 0.41, "adds_unsupported": 0.55, "contradicts": 0.04})))
    uid, program, _ = run_weave(env, dry_run=True)
    out = invoke("execute_plan", uid, {"program": program, "dry_run": True})
    assert out["nodes"][0]["review"][0]["label"] == "adds_unsupported"
    assert out["support"]["checked"] == 1


def test_key_unset_leaves_the_plan_exactly_as_it_was_before_jev(env, monkeypatch):
    """Acceptance 2: with no key (or `off`) the result equals main's."""
    from harness import executor

    uid, job_id, _ = env
    _, program, _ = run_weave(env, dry_run=True)
    with monkeypatch.context() as m:
        m.setattr(executor, "make_support_checker", lambda *a, **k: (lambda content: []))
        before_jev = _run(uid, program, dry_run=True)                   # what main computes

    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)             # the key is unset
    no_key = _run(uid, program, dry_run=True)
    monkeypatch.setenv("ART_JEV_MODE", "off")
    off = _run(uid, program, dry_run=True)

    dumps = {json.dumps(_strip(r), sort_keys=True) for r in (before_jev, no_key, off)}
    assert len(dumps) == 1
    assert "support" not in no_key and all("review" not in n for n in no_key["nodes"])
    assert no_key["nodes"][0]["status"] == "accepted"                   # lexical drift still gates as before


def test_a_recorded_scripted_host_run_replays_at_a_full_hit_rate(auto, env, monkeypatch):
    """Acceptance 1: record once with scripted answers, then replay with a
    transport that fails if it is called."""
    from eval.scripted_host import build_program

    uid, job_id, task = env
    program = build_program(uid, job_id, task["description"])
    program["nodes"] = [n for n in program["nodes"] if n["item_key"] != EXP2]
    from harness.executor import _KG
    program["nodes"].append(
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "keywords": ["integrate"], "bullets": _woven(_KG(uid).source_bullets[EXP2])})

    t = FakeTransport(answer_for=scripted({"supported": 0.95, "adds_unsupported": 0.04,
                                           "contradicts": 0.01}))
    auto.use(t)
    recorded = _run(uid, program, dry_run=True)
    assert recorded["support"]["checked"] >= 1 and len(t.calls) >= 1
    assert all(n["status"] in ("accepted", "kept") for n in recorded["nodes"]), recorded["nodes"]
    assert engine.stats()["support"]["jev"] >= 1

    engine.reset_stats()
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    replayed = _run(uid, program, dry_run=True)
    assert json.dumps(_strip(replayed), sort_keys=True) == json.dumps(_strip(recorded), sort_keys=True)
    st = engine.stats()["support"]
    assert st["hit_rate"] == 1.0 and st["cache"] > 0 and st["jev"] == 0 and st["fallback"] == 0
    committed = _run(uid, program)                     # and a real commit replays too
    assert committed["committed"] and committed["support"] == recorded["support"]

    # A bullet the recording never saw is a miss, not a silent fallback.
    program = {**program, "parent": committed["node_id"]}
    program["nodes"][-1]["bullets"][3]["text"] += " And more."
    with pytest.raises(JevReplayMiss):
        _run(uid, program, dry_run=True)
