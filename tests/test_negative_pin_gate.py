"""The negative-pin check and its gate (issue #232).

The term match (#198) refuses a bullet that contains a pinned word. This asks Jev
whether a changed bullet or item field mentions the pinned topic in other words, and adds
a `negative_pin:` violation to the same `preferences` gate. Every answer here is scripted by
a fake transport, so nothing touches the network; the real recorded answers are exercised in
`tests/test_negative_pin_labels.py`.
"""

import json

import pytest

from harness.acceptance import Context, metric_vector, negative_pin_violations, preference_violations
from harness.decisions import engine, negative_pins
from harness.decisions.client import JevReplayMiss
from harness.decisions.negative_pins import (
    TAU_BLOCK, TAU_REVIEW, VERSION, build_state, make_pin_checker, pin_topic, question_for,
    reviews, violations,
)
from test_executor import EXP2, _run, _strip, _woven, env  # noqa: F401  (fixture)
from test_jev import Boom, FakeTransport, auto, choice_answer  # noqa: F401  (fixture)

KEY = "exp:engineer|acme"
WOVEN = "Led the migration from a monolith to event-driven services using Kafka to integrate billing and search."


def noul(p):
    return {"type": "noul", "noul": p}


def scripted(by_topic, default=0.02):
    """The scripted Jev: `p` by a phrase in the question's topic."""
    def answer_for(state, wire):
        if wire["type"] == "choice":                       # the support check shares the transport
            return choice_answer("supported", {"supported": 0.97, "adds_unsupported": 0.02, "contradicts": 0.01})
        for phrase, p in by_topic.items():
            if phrase in wire["instructions"]:
                return noul(p)
        return noul(default)
    return answer_for


def pin_calls(transport):
    """The requests that asked a pin question (the support check shares the transport)."""
    return [c for c in transport.calls if any(w["type"] == "noul" for w in c["questions"].values())]


def page(bullets, **fields):
    return {"experiences": [{"title": "Engineer", "company": "Acme", "bullets": list(bullets), **fields}]}


def pin(term, statement=None):
    return {"term": term, "statement": statement or f"Never mention {term}"}


# ── the question and the topic ───────────────────────────────────────────────

def test_the_question_is_versioned_positive_and_a_noul():
    q = question_for("message brokers")
    assert q.version == VERSION == "negative_pin@v1" and q.point == "negative_pin" and q.kind == "noul"
    assert q.instructions.startswith("Does this text mention or refer to message brokers?")
    assert "avoid" not in q.instructions.lower()                # Jev reads negation literally
    assert q.wire()["criteria"]["true"] and q.wire()["criteria"]["false"]
    assert 0.0 < TAU_REVIEW < TAU_BLOCK <= 1.0


@pytest.mark.parametrize("term,statement,topic", [
    ("kafka", "Never mention Kafka", "Kafka"),
    ("kafka", "Please don't include Kafka", "Kafka"),
    ("amazon", "Never mention Amazon or its products", "Amazon or its products"),
    ("kafka", "Skip anything about Kafka", "Kafka"),
    ("kafka", "Avoid mentioning Kafka", "Kafka"),
    ("my first job", "My first data engineering job, at Corvid", "My first data engineering job, at Corvid"),
    ("kafka", None, "kafka"),
    ("kafka", "", "kafka"),
    ("kafka", "Don't talk about Kafka, I used it far less than Airflow", "kafka"),    # a comparison
    ("tensorflow", "Never mention TensorFlow; I only worked in PyTorch", "tensorflow"),
    ("java", "Don't say I know Java, I'd rather be seen as a Go developer", "java"),
    ("deep learning", "Do not describe this as deep learning, it was classical ML", "deep learning"),
    ("kafka", "Never mention " + "x" * 200, "kafka"),                                # too long to be a topic
])
def test_a_pins_topic_is_its_statement_without_the_directive_and_never_a_negation(term, statement, topic):
    assert pin_topic(term, statement) == topic


# ── what is checked, and what is sent ────────────────────────────────────────

def test_all_of_a_texts_pins_go_in_one_request_with_the_text_as_the_state(auto):
    t = FakeTransport(answer_for=scripted({"message brokers": 0.95}))
    auto.use(t)
    checker = make_pin_checker([pin("message brokers"), pin("rabbitmq")], base_content=page(["Old."]))
    findings = checker(page(["Old.", "Moved events through Kafka."]))
    assert [(f["pin"], f["p"], f["status"]) for f in findings] == [
        ("message brokers", 0.95, "checked"), ("rabbitmq", 0.02, "checked")]
    (call,) = t.calls
    assert call["state"] == build_state("Moved events through Kafka.", "bullet")
    assert set(call["state"]) == {"text", "kind"}
    assert sorted(w["instructions"] for w in call["questions"].values()) == sorted(
        [question_for("message brokers").wire()["instructions"], question_for("rabbitmq").wire()["instructions"]])
    assert all(f["item"] == KEY for f in findings)


def test_only_changed_text_is_checked(auto):
    t = FakeTransport(answer_for=scripted({}))
    auto.use(t)
    base = page(["Kept one.", "Kept two."], description="A description.")
    same = page(["Kept two.", "Kept one."], description="A description.")          # reordered, nothing new
    assert make_pin_checker([pin("rabbitmq")], base)(same) == [] and t.calls == []
    edited = page(["Kept one.", "Kept  two.", "A brand new bullet."], description="A new description.")
    got = make_pin_checker([pin("rabbitmq")], base)(edited)
    assert [(f["kind"], f["text"]) for f in got] == [("description", "A new description."),
                                                     ("bullet", "A brand new bullet.")]


def test_a_new_item_is_all_new_text_and_no_base_means_everything_is_checked(auto):
    auto.use(FakeTransport(answer_for=scripted({})))
    content = page(["One."])
    replaced = make_pin_checker([pin("rabbitmq")], {"experiences": []})(content)
    assert {f["kind"] for f in replaced} == {"title", "company", "bullet"}
    assert len(make_pin_checker([pin("rabbitmq")], None)(content)) == 3


def test_a_text_the_term_match_already_catches_is_never_sent(auto):
    t = FakeTransport(answer_for=scripted({}))
    auto.use(t)
    checker = make_pin_checker([pin("kafka"), pin("rabbitmq")], base_content=page([]))
    findings = checker(page(["Built Kafka consumers."]))
    assert [f["pin"] for f in findings] == ["rabbitmq"]                # kafka is the term match's
    (call,) = t.calls
    assert len(call["questions"]) == 1


def test_an_answer_is_cached_per_text_and_pin_and_the_checker_asks_once(auto):
    t = FakeTransport(answer_for=scripted({}))
    auto.use(t)
    checker = make_pin_checker([pin("rabbitmq")], page([]))
    content = page(["Moved events through Kafka."])
    for _ in range(3):
        checker(content)
    assert len(t.calls) == 1
    again = make_pin_checker([pin("rabbitmq")], page([]))                # a new checker: the engine's cache
    assert again(content)[0]["source"] == "cache" and len(t.calls) == 1
    assert engine.stats()["negative_pin"]["cache"] == 1


# ── thresholds ───────────────────────────────────────────────────────────────

def _f(p, status="checked", kind="bullet", text="Moved events through Kafka.", pin_="message broker"):
    return {"item": KEY, "kind": kind, "text": text, "pin": pin_, "topic": pin_, "status": status, "p": p}


def test_thresholds_block_at_tau_block_and_review_in_the_band():
    assert violations([_f(TAU_BLOCK)]) == [f'negative_pin:message broker@{KEY} :: "Moved events through Kafka."']
    just_under = round(TAU_BLOCK - 0.01, 4)
    assert violations([_f(just_under)]) == [] and reviews([_f(just_under)])[0]["p"] == just_under
    assert reviews([_f(TAU_REVIEW)]) != [] and reviews([_f(round(TAU_REVIEW - 0.01, 4))]) == []
    assert reviews([_f(TAU_BLOCK)]) == []                              # blocked, not "review"
    assert violations([_f(0.99, status="unchecked", )]) == [] and reviews([_f(0.99, status="unchecked")]) == []
    assert violations([_f(0.0)]) == [] and reviews([_f(0.0)]) == []


def test_a_field_hit_names_the_field_like_the_term_match_does():
    assert violations([_f(0.95, kind="company", text="Corvid Logistics")]) == [
        f"negative_pin:message broker@{KEY} :: company"]
    review = reviews([_f(0.5, kind="name", text="Slate Ingest")])
    assert review == [{"check": "negative_pin", "item": KEY, "where": "name", "bullet": "Slate Ingest",
                       "pin": "message broker", "label": "mentions", "p": 0.5}]


def test_a_jev_hit_reads_exactly_like_the_term_match():
    content = page(["Built Kafka consumers."])
    (term_hit,) = negative_pin_violations(content, {"kafka"})
    jev_hit = violations([_f(0.99, text="Built Kafka consumers.", pin_="kafka")])[0]
    assert jev_hit == term_hit


# ── through the gate ─────────────────────────────────────────────────────────

def test_the_gate_adds_a_jev_hit_and_dedupes_it_with_the_term_match(auto):
    auto.use(FakeTransport(answer_for=scripted({"message broker": 0.95})))
    content = page(["Moved events through a broker.", "Built Kafka consumers."])
    checker = make_pin_checker([pin("message broker"), pin("kafka")], page([]))
    ctx = Context(jd_text="x", negative_terms={"message broker", "kafka"}, pin_checker=checker)
    got = preference_violations(content, ctx)
    term_only = preference_violations(content, Context(jd_text="x", negative_terms={"message broker", "kafka"}))
    assert term_only == [f'negative_pin:kafka@{KEY} :: "Built Kafka consumers."']
    assert got == term_only + [f'negative_pin:message broker@{KEY} :: "Built Kafka consumers."',
                               f'negative_pin:message broker@{KEY} :: "Moved events through a broker."']
    assert len(got) == len(set(got))
    # And the same hit reported twice (a term match and a checker that ignores it) is one violation.
    dup = Context(jd_text="x", negative_terms={"kafka"},
                  pin_checker=lambda c: [_f(0.99, text="Built Kafka consumers.", pin_="kafka")])
    assert preference_violations(content, dup) == [f'negative_pin:kafka@{KEY} :: "Built Kafka consumers."']
    assert metric_vector(content, ctx)["gates"]["preferences"] == got


@pytest.mark.parametrize("how", ["off", "no_key", "api_error"])
def test_without_jev_the_gate_is_exactly_the_term_match(auto, monkeypatch, how):
    content = page(["Moved events through Kafka.", "Built Kafka consumers."])
    if how == "off":
        monkeypatch.setenv("ART_JEV_MODE", "off")
        auto.use(Boom())
    elif how == "no_key":
        monkeypatch.setattr(engine, "get_client", lambda: None)
    else:
        auto.use(FakeTransport(script=[(500, {"message": "down"})] * 5), max_retries=0)
    checker = make_pin_checker([pin("message broker"), pin("kafka")], page([]))
    assert all(f["status"] == "unchecked" and f["p"] is None for f in checker(content))
    with_jev = Context(jd_text="x", negative_terms={"message broker", "kafka"}, pin_checker=checker)
    without = Context(jd_text="x", negative_terms={"message broker", "kafka"})
    assert preference_violations(content, with_jev) == preference_violations(content, without)
    assert reviews(checker(content)) == [] and violations(checker(content)) == []


# ── through the executor ─────────────────────────────────────────────────────

def _pin_pref(isolated_engine, uid, term, text=None):
    from sqlmodel import Session

    from database.models import UserPreference
    with Session(isolated_engine) as s:
        pref = UserPreference(user_id=uid, text=text or f"Never mention {term}", polarity="suppress",
                              target_term=term.lower(), scope_type="global", strength=5)
        s.add(pref)
        s.commit()


def run_weave(env, **kw):
    """The #197 weave plan: one reworded bullet, citing its source."""
    from harness.executor import _KG

    uid, job_id, _ = env
    program = {"job_id": job_id, "nodes": [
        {"id": "weave", "op": "revise", "item_key": EXP2, "strategy": "keyword_weave",
         "keywords": ["integrate"], "bullets": _woven(_KG(uid).source_bullets[EXP2])}]}
    return uid, program, _run(uid, program, **kw)


def test_a_paraphrased_mention_of_a_pinned_topic_is_refused_through_a_recorded_answer(auto, env, isolated_engine):
    uid, *_ = env
    _pin_pref(isolated_engine, uid, "message broker")
    t = FakeTransport(answer_for=scripted({"message broker": 0.95}))
    auto.use(t)
    _, _, out = run_weave(env)
    node = out["nodes"][0]
    assert node["status"] == "reverted"
    assert node["reason"].startswith("hard_gate: preferences")
    assert f'negative_pin:message broker@{EXP2} :: "Led the migration from a monolith to event-driven serv' in node["reason"]
    (call,) = pin_calls(t)
    assert call["state"] == build_state(WOVEN, "bullet")                # the changed bullet, nothing else
    assert out["committed"] and "negative_pins" not in out              # the revert left the page as it was


def test_a_near_miss_topic_is_not_refused(auto, env, isolated_engine):
    uid, *_ = env
    _pin_pref(isolated_engine, uid, "rabbitmq")
    auto.use(FakeTransport(answer_for=scripted({"rabbitmq": 0.03})))
    _, _, out = run_weave(env)
    node = out["nodes"][0]
    assert node["status"] == "accepted" and out["committed"], node
    assert "review" not in node
    assert out["negative_pins"] == {"checked": 1, "review": []}
    assert out["metrics"]["final"]["gates"]["preferences"] == []


def test_the_uncertain_band_is_surfaced_as_review_and_does_not_block(auto, env, isolated_engine):
    uid, *_ = env
    _pin_pref(isolated_engine, uid, "message broker")
    auto.use(FakeTransport(answer_for=scripted({"message broker": 0.5})))
    _, _, out = run_weave(env)
    node = out["nodes"][0]
    entry = {"check": "negative_pin", "item": EXP2, "where": "bullet", "bullet": WOVEN[:79] + "…",
             "pin": "message broker", "label": "mentions", "p": 0.5}
    assert node["status"] == "accepted" and node["review"] == [entry]
    assert out["negative_pins"] == {"checked": 1, "review": [entry]}
    assert out["metrics"]["final"]["gates"]["preferences"] == []


def test_the_contract_carries_the_pin_review_and_block(auto, env, isolated_engine):
    from harness.contract import invoke

    uid, *_ = env
    _pin_pref(isolated_engine, uid, "message broker")
    auto.use(FakeTransport(answer_for=scripted({"message broker": 0.5})))
    _, program, _ = run_weave(env, dry_run=True)
    out = invoke("execute_plan", uid, {"program": program, "dry_run": True})
    assert out["nodes"][0]["review"][0]["check"] == "negative_pin"
    assert out["negative_pins"]["checked"] == 1


def test_the_pins_own_statement_is_the_topic_jev_is_asked_about(auto, env, isolated_engine):
    uid, *_ = env
    _pin_pref(isolated_engine, uid, "amazon", text="Never mention Amazon or its products")
    t = FakeTransport(answer_for=scripted({}))
    auto.use(t)
    run_weave(env, dry_run=True)
    (call,) = pin_calls(t)
    (wire,) = call["questions"].values()
    assert wire["instructions"].startswith("Does this text mention or refer to Amazon or its products?")


def test_no_pins_means_no_question_and_no_block_in_the_result(auto, env):
    def no_pin_question(state, wire):
        assert wire["type"] != "noul", "a pin question was asked with no pins"
        return scripted({})(state, wire)
    auto.use(FakeTransport(answer_for=no_pin_question))
    _, _, out = run_weave(env, dry_run=True)
    assert "negative_pins" not in out and "review" not in out["nodes"][0]


def test_key_unset_leaves_the_plan_exactly_as_the_term_match_alone_would(env, isolated_engine, monkeypatch):
    """Acceptance 3: with no key (or `off`) the result equals main's."""
    from harness import executor

    uid, *_ = env
    _pin_pref(isolated_engine, uid, "message broker")
    _pin_pref(isolated_engine, uid, "rabbitmq")
    with monkeypatch.context() as m:
        m.setattr(executor, "make_pin_checker", lambda *a, **k: (lambda content: []))
        _, program, before_jev = run_weave(env, dry_run=True)                # what main computes

    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)                  # the key is unset
    no_key = _run(uid, program, dry_run=True)
    monkeypatch.setenv("ART_JEV_MODE", "off")
    off = _run(uid, program, dry_run=True)
    dumps = {json.dumps(_strip(r), sort_keys=True) for r in (before_jev, no_key, off)}
    assert len(dumps) == 1
    assert "negative_pins" not in no_key and all("review" not in n for n in no_key["nodes"])


def test_a_recorded_run_replays_at_a_full_hit_rate_with_no_live_call(auto, env, isolated_engine, monkeypatch):
    """Acceptance 4: record once with scripted answers, then replay with a transport that
    fails if it is called."""
    uid, *_ = env
    _pin_pref(isolated_engine, uid, "rabbitmq")
    _pin_pref(isolated_engine, uid, "message broker")
    auto.use(FakeTransport(answer_for=scripted({"message broker": 0.5, "rabbitmq": 0.04})))
    _, program, recorded = run_weave(env, dry_run=True)
    assert recorded["negative_pins"]["checked"] == 2 and engine.stats()["negative_pin"]["jev"] == 2

    engine.reset_stats()
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    replayed = _run(uid, program, dry_run=True)
    assert json.dumps(_strip(replayed), sort_keys=True) == json.dumps(_strip(recorded), sort_keys=True)
    st = engine.stats()["negative_pin"]
    assert st["hit_rate"] == 1.0 and st["cache"] == 2 and st["jev"] == 0 and st["fallback"] == 0

    program["nodes"][0]["bullets"][3]["text"] += " And more."               # a text the recording never saw
    with pytest.raises(JevReplayMiss):
        _run(uid, program, dry_run=True)


def test_finalize_hints_work_for_a_jev_hit(env):
    from harness.executor import _KG, _finalize
    from harness.program import Program

    uid, *_ = env
    content = page(["Moved events through Kafka."])
    hit = _f(0.99, pin_="message broker")
    ctx = Context(jd_text="x", negative_terms={"message broker"}, pin_checker=lambda c: [hit])
    program = Program.model_validate({"job_id": "j", "nodes": []}).model_dump(mode="json")
    violations_, _, _ = _finalize(content, program, ctx, _KG(uid), None)
    pins = [v for v in violations_ if v["check"] == "preferences"]
    assert pins == [{"check": "preferences", "hint": f"revise {KEY} so it no longer mentions 'message broker'",
                     "detail": f'negative_pin:message broker@{KEY} :: "Moved events through Kafka."'}]
