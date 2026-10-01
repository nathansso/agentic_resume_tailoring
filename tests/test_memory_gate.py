"""The memory gate (issue #202): the prefilter, the routing, `observe`, `record_preference`,
`art_pins` and the `user-prompt` hook.

Jev is scripted here with a fake transport (`harness/decisions/client.py`), the way
`tests/test_support_gate.py` does it, so nothing reaches the network. The real answers, and what the
gate does with them, are `tests/test_memory_gate_labels.py`.
"""

import io
import json
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from database.models import UserPreference
from harness import hooks, memory, tools
from harness.contract import invoke
from harness.decisions import engine, memory_gate as mg
from harness.decisions.client import JevClient
from harness.decisions.questions import Answer

SESSION = "gate-test"
PYTHON = "skill: Python"
PROJECT = "project: Next-Item Recommendation"
EXPERIENCE = "experience: Data Science Intern @ IDX Exchange"


# ── a scripted Jev ───────────────────────────────────────────────────────────

class ScriptedJev:
    """Answers the gate's four questions per message from a table, and counts its requests."""

    def __init__(self):
        self.table = {}
        self.calls = []
        self.status = 200

    def say(self, message, p=0.95, direction="emphasize", level=2.0, target="no_match", target_p=0.99,
            hard=0.0):
        self.table[message] = dict(p=p, direction=direction, level=level, target=target,
                                   target_p=target_p, hard=hard)
        return message

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(payload)
        if self.status != 200:
            return self.status, {"message": "boom"}
        spec = self.table.get(payload["state"]["message"],
                              dict(p=0.02, direction="none", level=0.0, target="no_match", target_p=1.0, hard=0.0))
        answers = {}
        for qid, q in payload["questions"].items():
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": spec["p"]}
            elif q["type"] == "score":
                answers[qid] = {"type": "score", "score": spec["level"], "confidence": 0.9,
                                "probabilities": {"4": spec["hard"]}}
            elif "emphasize" in q["criteria"]:
                answers[qid] = {"type": "choice", "choice": spec["direction"], "confidence": 0.9,
                                "probabilities": {spec["direction"]: 0.9}}
            else:
                answers[qid] = {"type": "choice", "choice": spec["target"], "confidence": spec["target_p"],
                                "probabilities": {spec["target"]: spec["target_p"]}}
        return 200, {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}


@pytest.fixture()
def jev(monkeypatch, isolated_engine):
    fake = ScriptedJev()
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: JevClient("k", transport=fake, sleep=lambda s: None))
    return fake


def _prefs(engine_, uid, **where):
    with Session(engine_) as s:
        rows = s.exec(select(UserPreference).where(UserPreference.user_id == uid)).all()
    return [r for r in rows if all(getattr(r, k) == v for k, v in where.items())]


def _guess(**kw):
    base = dict(p=0.95, direction="emphasize", direction_p=0.9, strength=3, strength_confidence=0.9,
                hard_p=0.0, target_key="skill:python", target_label=PYTHON, target_p=0.99,
                target_named=True, source="jev")
    base.update(kw)
    return mg.Guess(**base)


def _route(text, guess, **kw):
    return mg.route(text, mg.prefilter(text), guess, **kw)


# ── the prefilter ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["thanks, that looks great!", "hello again", "ok", "", "   ",
                                  "Can you shorten the second bullet?", "I led the forecasting rollout."])
def test_the_prefilter_drops_chit_chat_questions_and_facts_with_no_cue(text):
    pre = mg.prefilter(text)
    assert not pre.candidate and pre.reason in ("no_cue", "empty")
    assert mg.route_prefilter(text, pre)["action"] == "drop"


@pytest.mark.parametrize("text,negated", [
    ("Always lead with the Transit Pulse project.", False),
    ("Never mention Excel.", True),
    ("Don't stop mentioning Docker.", True),
    ("I don't want you to leave out Postgres.", True),
    ("Leave the Ballot Tally project off my resume from now on.", True),
    ("I prefer concise bullet points.", False),
    ("Please skip the achievements section.", True),
    ("Make sure A/B testing is near the top.", False),
])
def test_the_prefilter_marks_candidates_and_reads_negation(text, negated):
    pre = mg.prefilter(text)
    assert pre.candidate and pre.negated is negated and pre.cues


def test_the_prefilter_reads_scope_and_how_many_things_a_message_states():
    assert mg.prefilter("For this job, lead with Transit Pulse.").scope == "job"
    assert mg.prefilter("This time, don't mention Streamlit.").scope == "job"
    assert mg.prefilter("For ML roles, always lead with research.").scope == "role"
    assert mg.prefilter("Always lead with Python.").scope is None
    two = mg.prefilter("Never mention Excel. Always lead with Python.")
    assert two.statements == 2 and mg.route_prefilter("x", two)["reason"] == "multiple_statements"
    assert mg.route_prefilter("x", mg.prefilter("For data science roles, never list PHP."))["reason"] == "role_scope"


def test_a_message_over_the_limit_is_pasted_material_not_a_candidate():
    pre = mg.prefilter("Always " + "x " * mg.MAX_CHARS)
    assert not pre.candidate and pre.reason == "too_long"


def test_the_prefilters_heuristic_direction_is_suppress_when_negated():
    assert mg.heuristic_guess(mg.prefilter("Never mention Excel."))["direction"] == "suppress"
    assert mg.heuristic_guess(mg.prefilter("Always lead with Python."))["direction"] == "emphasize"
    assert mg.heuristic_guess(mg.prefilter("hello"))["is_preference"] is False


# ── the questions ────────────────────────────────────────────────────────────

def test_the_questions_are_versioned_positive_and_carry_the_catalog():
    catalog = mg.make_catalog(skills=["Python", "SQL"], experiences=[("Intern", "Acme")], projects=["Recipe App"])
    qs = mg.questions_for(catalog)
    assert [q.kind for q in qs] == ["noul", "choice", "score", "choice"]
    assert {q.version for q in qs} == {mg.VERSION} and all(q.point == mg.POINT for q in qs)
    standing = qs[0].wire()["instructions"]
    assert standing.startswith("Does this message state a lasting preference")
    assert "right now" in standing and "question" in standing
    assert set(qs[1].options) == set(mg.DIRECTIONS)
    assert "left out or never mentioned" in qs[1].options["suppress"]       # worded positively
    assert len(qs[2].levels) == 5 and "never" in qs[2].levels[4]            # #129's scale
    target = qs[3]
    assert mg.NO_MATCH in target.options and target.options[mg.NO_MATCH] is None
    assert "skill: Python" in target.options and "experience: Intern @ Acme" in target.options
    assert any(o.startswith("section: ") for o in target.options)


def test_the_state_is_the_message_alone_unless_a_previous_turn_is_given():
    assert mg.build_state("  Never   mention Excel. ") == {"message": "Never mention Excel."}
    assert mg.build_state("Drop it.", "Here is the Ballot Tally project.") == {
        "message": "Drop it.", "previous_assistant": "Here is the Ballot Tally project."}


def test_the_catalog_is_capped_at_the_choice_limit_and_repeated_labels_stay_distinct():
    many = mg.make_catalog(skills=[f"Skill{i}" for i in range(300)])
    options, _ = mg.catalog_options(many)
    assert len(options) == mg.MAX_CATALOG
    twice = [{"key": "skill:a", "label": "skill: A", "target_type": "skill"},
             {"key": "skill:a2", "label": "skill: A", "target_type": "skill"}]
    names = list(mg.catalog_options(twice)[0])
    assert len(set(names)) == 2


@pytest.mark.parametrize("text,entry,named", [
    ("Never mention Excel.", {"key": "skill:excel", "label": "skill: Excel", "target_type": "skill"}, True),
    ("Leave my GPA off.", {"key": "section:education", "label": "section: education", "target_type": "section"}, False),
    ("Skip the education section.", {"key": "section:education", "label": "section: education", "target_type": "section"}, True),
    ("Lead with A/B testing.", {"key": "skill:a/b testing", "label": "skill: A/B testing", "target_type": "skill"}, True),
    ("Give the Rivermount internship more room.",
     {"key": "exp:x|y", "label": "experience: Data Science Intern @ Rivermount College", "target_type": "experience"}, True),
    ("Show my first job more.",
     {"key": "exp:x|y", "label": "experience: Data Science Intern @ Rivermount College", "target_type": "experience"}, False),
    ("Drop the Ballot Tally pipeline.", {"key": "proj:ballot tally", "label": "project: Ballot Tally", "target_type": "project"}, True),
    ("Drop that weekend project.", {"key": "proj:ballot tally", "label": "project: Ballot Tally", "target_type": "project"}, False),
    ("I don't want Rust.", {"key": "skill:r", "label": "skill: R", "target_type": "skill"}, False),
])
def test_a_target_must_be_named_in_the_message(text, entry, named):
    assert mg.names_target(text, entry) is named


# ── routing ──────────────────────────────────────────────────────────────────

CLEAR = "Always lead with Python in my skills."


def test_a_clear_preference_is_written_at_strength_four_or_less():
    for strength in (1, 2, 3, 4):
        d = _route(CLEAR, _guess(strength=strength))
        assert d["action"] == "write" and d["reason"] == "auto" and d["guess"]["strength"] == strength


@pytest.mark.parametrize("direction", ["emphasize", "suppress"])
@pytest.mark.parametrize("text", ["Never mention Python anywhere.", "Always lead with Python."])
def test_the_gate_never_writes_a_strength_five_preference_or_a_negative_pin(direction, text):
    for g in (_guess(strength=5, direction=direction, p=1.0),
              _guess(strength=4, hard_p=0.4, direction=direction, p=1.0)):
        d = _route(text, g)
        assert d["action"] == "host" and d["reason"] == "hard_preference"
        assert d["guess"]["strength"] in (4, 5)
    line = mg.explain(d)
    assert mg.HOST_LINE in line and "strength-5" in line


def test_a_confident_strength_five_never_reaches_the_write_path_at_any_threshold():
    text = "Never mention Excel."
    for tau in (0.0, 0.05, 0.5, 0.65, 0.95):
        d = _route(text, _guess(strength=5, direction="suppress", p=1.0), tau_lo=0.0, tau_hi=tau, tau_target=0.0)
        assert d["action"] != "write"


def test_a_negation_that_disagrees_with_jevs_direction_goes_to_the_host():
    # "don't stop mentioning" is negated, but Jev (rightly) reads emphasize: a backstop for its weakness.
    d = _route("Don't stop mentioning Python.", _guess(direction="emphasize"))
    assert d["action"] == "host" and d["reason"] == "negation_disagrees"
    # suppress with no negation cue in the message
    d = _route("Please lead with Python.", _guess(direction="suppress"))
    assert d["action"] == "host" and d["reason"] == "negation_disagrees"
    # agreement writes
    assert _route("Never mention Python.", _guess(direction="suppress", strength=3))["action"] == "write"
    assert _route("Please lead with Python.", _guess(direction="emphasize"))["action"] == "write"
    # the check can be turned off only for the fit's ablation
    assert _route("Don't stop mentioning Python.", _guess(), backstop=False)["action"] == "write"


@pytest.mark.parametrize("guess,reason", [
    (_guess(target_key=None, target_label=None, target_named=False), "no_target"),
    (_guess(target_named=False), "no_target"),
    (_guess(target_p=0.4), "no_target"),
    (_guess(direction="format_rule"), "format_rule"),
    (_guess(direction="none"), "no_direction"),
])
def test_an_unresolved_unnamed_or_unsure_target_and_a_format_rule_go_to_the_host(guess, reason):
    d = _route(CLEAR, guess)
    assert d["action"] == "host" and d["reason"] == reason


def test_p_decides_between_drop_host_and_write():
    assert _route(CLEAR, _guess(p=mg.TAU_LO - 0.05))["action"] == "drop"
    assert _route(CLEAR, _guess(p=mg.TAU_LO))["action"] == "host"
    assert _route(CLEAR, _guess(p=mg.TAU_HI - 0.01))["reason"] == "uncertain"
    assert _route(CLEAR, _guess(p=mg.TAU_HI))["action"] == "write"
    assert _route(CLEAR, _guess(p=0.5), tau_lo=0.6, tau_hi=0.9)["action"] == "drop"


def test_a_job_scoped_message_needs_a_known_job_and_is_written_scoped():
    text = "For this job, always lead with Python."
    assert _route(text, _guess())["reason"] == "job_unknown"
    d = _route(text, _guess(), job_known=True)
    assert d["action"] == "write" and d["scope"] == "job"


def test_a_long_message_and_a_read_only_store_do_not_write():
    long_text = "Always lead with Python " + "and so on " * 40
    assert _route(long_text, _guess())["reason"] == "long_message"
    assert _route(CLEAR, _guess(), write_ok=False)["reason"] == "read_only"


def test_a_non_candidate_is_dropped_whatever_jev_would_say():
    assert _route("thanks!", _guess(p=1.0))["action"] == "drop"


def test_with_no_jev_answer_the_prefilter_alone_routes_the_host_and_nothing_is_written():
    d = _route(CLEAR, None, unavailable="no_key")
    assert (d["action"], d["reason"], d["source"], d["p"]) == ("host", "jev_unavailable", "prefilter", None)
    assert d["unavailable"] == "no_key" and d["candidate"]
    assert "record_preference" in mg.explain(d) and '"always"' in mg.explain(d)
    assert _route("thanks", None)["action"] == "drop"


def test_the_hosts_line_carries_the_guess_and_a_write_says_what_was_saved():
    host = mg.explain(_route(CLEAR, _guess(p=0.5)))
    assert host.startswith(mg.HOST_LINE) and "emphasize skill:python" in host and "strength 3" in host
    wrote = mg.explain({**_route(CLEAR, _guess()), "text": CLEAR})
    assert wrote.startswith("ART saved a standing preference") and CLEAR in wrote
    assert mg.explain(_route("thanks", None)) == ""


def test_jevs_answers_become_a_guess_and_a_fallback_means_none():
    catalog = mg.make_catalog(skills=["Python"])
    ok = [Answer("noul", 0.9, p=0.9), Answer("choice", "suppress", p=0.8),
          Answer("score", 3.2, p=0.7, confidence=0.7, probabilities={"3": 0.8, "4": 0.2}),
          Answer("choice", "skill: Python", p=0.97)]
    g = mg.parse_answers(ok, catalog, "Never mention Python.")
    assert (g.p, g.direction, g.strength, g.target_key, g.target_named, g.hard_p) == (
        0.9, "suppress", 4, "skill:python", True, 0.2)
    assert mg.parse_answers([*ok[:3], Answer("choice", "no_match", p=0.9)], catalog, "x").target_key is None
    down = [Answer.fallback("noul", None, "no_key")] + ok[1:]
    assert mg.parse_answers(down, catalog, "x") is None and mg.unavailable_reason(down) == "no_key"
    assert mg.strength_of(Answer("score", 0.0)) == 1 and mg.strength_of(Answer("score", 4.0)) == 5
    assert mg.strength_of(Answer("score", 3.6)) == 5


# ── observe, end to end ──────────────────────────────────────────────────────

def test_observe_writes_a_clear_low_stakes_preference_and_logs_it(kg, jev, isolated_engine):
    msg = jev.say("Please lead with Python in my skills.", p=0.95, direction="emphasize", level=3.0, target=PYTHON)
    d = memory.observe(kg, msg, SESSION)
    assert (d["action"], d["reason"], d["source"], d["p"]) == ("write", "auto", "jev", 0.95)
    [pref] = _prefs(isolated_engine, kg, target_key="skill:python")
    assert (pref.polarity, pref.strength, pref.scope_type, pref.text) == ("emphasize", 4, "global", msg)
    assert pref.provenance["source"] == "memory_gate" and pref.provenance["gate"]["p"] == 0.95
    assert d["preference_id"] == str(pref.preference_id)
    assert d["note"].startswith("ART saved a standing preference")
    # one request carried the four questions, and the same message is not asked or written twice
    assert len(jev.calls) == 1 and len(jev.calls[0]["questions"]) == 4
    again = memory.observe(kg, msg, SESSION)
    assert again["action"] == "drop" and again["reason"] == "already_held" and len(jev.calls) == 1
    assert len(_prefs(isolated_engine, kg, target_key="skill:python")) == 1
    rows = [json.loads(l) for l in (isolated_engine._test_profile_file.parent / "memory_gate.jsonl").read_text().splitlines()]
    assert [r["action"] for r in rows] == ["write", "drop"] and rows[0]["source"] == "jev" and rows[0]["p"] == 0.95
    assert not any(msg in json.dumps(r) for r in rows)                  # the message is not logged


def test_observe_sends_a_strength_five_to_the_host_and_writes_nothing(kg, jev, isolated_engine):
    msg = jev.say("Never mention Python on my resume.", p=0.99, direction="suppress", level=4.0, target=PYTHON, hard=0.9)
    d = memory.observe(kg, msg, SESSION)
    assert (d["action"], d["reason"]) == ("host", "hard_preference")
    assert d["guess"]["direction"] == "suppress" and d["guess"]["strength"] == 5
    assert d["note"].startswith(mg.HOST_LINE) and "suppress skill:python" in d["note"]
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_observe_sends_an_unsure_message_or_an_unresolved_target_to_the_host(kg, jev, isolated_engine):
    maybe = jev.say("Maybe lead with Python, I think.", p=0.4, target=PYTHON)
    lost = jev.say("Always keep the resume to one theme.", p=0.97, target="no_match")
    assert memory.observe(kg, maybe)["reason"] == "uncertain"
    d = memory.observe(kg, lost)
    assert (d["action"], d["reason"]) == ("host", "no_target")
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_observe_sends_a_negation_disagreement_to_the_host(kg, jev, isolated_engine):
    msg = jev.say("Don't stop mentioning Python on my resume.", p=0.97, direction="emphasize", level=3.0, target=PYTHON)
    d = memory.observe(kg, msg)
    assert (d["action"], d["reason"], d["negated"]) == ("host", "negation_disagrees", True)
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_observe_drops_chit_chat_without_asking_jev(kg, jev):
    for text in ("thanks!", "ok", "Can you shorten the first bullet?"):
        d = memory.observe(kg, text, SESSION)
        assert (d["action"], d["source"], d["candidate"]) == ("drop", "prefilter", False)
    assert jev.calls == []


def test_without_a_key_or_with_mode_off_the_prefilter_alone_routes_and_nothing_is_written(kg, isolated_engine, monkeypatch):
    msg = "Please lead with Python in my skills."
    monkeypatch.setattr(engine, "get_client", lambda: None)
    for mode in ("auto", "off"):
        monkeypatch.setenv("ART_JEV_MODE", mode)
        d = memory.observe(kg, msg, SESSION)
        assert (d["action"], d["reason"], d["source"]) == ("host", "jev_unavailable", "prefilter")
        assert d["p"] is None and d["note"].startswith(mg.HOST_LINE)
        assert memory.observe(kg, "thanks!")["action"] == "drop"
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_an_api_error_is_the_same_as_no_key(kg, jev, isolated_engine):
    jev.status = 500
    d = memory.observe(kg, "Please lead with Python in my skills.", SESSION)
    assert (d["action"], d["reason"], d["source"]) == ("host", "jev_unavailable", "prefilter")
    assert d["unavailable"].startswith("api_error")
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_observe_never_replaces_a_preference_the_user_holds(kg, jev, isolated_engine):
    # the fixture holds "Never mention coursework projects" (suppress); a clear emphasize of the same
    # subject would reverse it, which is the user's call
    memory.record_preference(kg, "Mention Python", "suppress", "Python", 3)
    msg = jev.say("Please lead with Python in my skills.", p=0.97, direction="emphasize", level=2.0, target=PYTHON)
    d = memory.observe(kg, msg, SESSION)
    assert (d["action"], d["reason"]) == ("host", "would_supersede")
    assert [p.polarity for p in _prefs(isolated_engine, kg, target_key="skill:python")] == ["suppress"]


def test_a_job_scoped_preference_is_written_scoped_when_the_job_is_known(kg, jev, isolated_engine):
    msg = jev.say("For this job, lead with Python in my skills.", p=0.95, direction="emphasize", level=3.0, target=PYTHON)
    assert memory.observe(kg, msg, "no-job-yet")["reason"] == "job_unknown"
    job = str(uuid4())
    hooks.save_state(SESSION, {"cursor": 0, "job_id": job})
    d = memory.observe(kg, msg, SESSION)
    assert (d["action"], d["scope"]) == ("write", "job")
    [pref] = _prefs(isolated_engine, kg, target_key="skill:python")
    assert (pref.scope_type, pref.scope_value) == ("job", job)
    assert pref.provenance["job_id"] == job


def test_a_read_only_store_is_never_written_by_the_gate(kg, jev, isolated_engine):
    msg = jev.say("Please lead with Python in my skills.", p=0.95, level=3.0, target=PYTHON)
    d = memory.observe(kg, msg, write=False)
    assert (d["action"], d["reason"]) == ("host", "read_only")
    assert _prefs(isolated_engine, kg, target_key="skill:python") == []


def test_the_memory_catalog_has_the_planners_keys_and_the_sections(kg):
    from agents.preferences import SECTION_NAMES, target_catalog

    mine = memory.memory_catalog(kg)
    assert {c["key"] for c in mine} == {c["key"] for c in target_catalog(kg)}
    assert {c["label"] for c in mine} == {c["label"] for c in target_catalog(kg)}
    assert {c["key"] for c in mine if c["target_type"] == "section"} == {f"section:{s}" for s in SECTION_NAMES}
    assert "skill:python" in {c["key"] for c in mine}


# ── record_preference ────────────────────────────────────────────────────────

def test_record_preference_stores_a_strength_five_pin_and_art_pins_returns_it_verbatim(kg, isolated_engine):
    text = "Never mention Kafka, not even in passing."
    out = memory.record_preference(kg, text, "suppress", "Kafka", 5, quote="never mention kafka")
    assert out["status"] == "stored" and out["pinned"] and out["negative_pin"]
    assert out["strength"] == 5 and out["target_resolved"] is False and out["target_term"] == "Kafka"
    [pref] = _prefs(isolated_engine, kg, target_term="Kafka")
    assert pref.provenance["source"] == "host" and pref.provenance["quote"] == "never mention kafka"
    pins = tools.art_pins(kg)["pins"]
    assert text in [p["text"] for p in pins]
    kafka = next(p for p in pins if p["text"] == text)
    assert kafka["negative_pin"] and kafka["polarity"] == "suppress" and kafka["target_term"] == "Kafka"


def test_record_preference_resolves_an_exact_target_and_never_a_partial_one(kg):
    exact = memory.record_preference(kg, "Lead with Python", "emphasize", "skill:python", 3)
    assert exact["target_key"] == "skill:python" and exact["target_resolved"]
    by_name = memory.record_preference(kg, "Lead with the recommender", "emphasize", "Next-Item Recommendation", 3)
    assert by_name["target_key"] == "proj:next-item recommendation"
    section = memory.record_preference(kg, "Skip achievements", "suppress", "section:achievements", 3)
    assert section["target_key"] == "section:achievements"
    loose = memory.record_preference(kg, "Skip the recommender", "suppress", "recommend", 3)
    assert loose["target_key"] is None and loose["target_resolved"] is False
    assert "proj:next-item recommendation" in loose["suggestions"]
    keylike = memory.record_preference(kg, "Skip the old repo", "suppress", "proj:old-repo", 3)
    assert keylike["target_key"] is None and keylike["target_term"] == "old-repo"


@pytest.mark.parametrize("kwargs,why", [
    (dict(text="  ", polarity="suppress", target="x"), "text is empty"),
    (dict(text="t", polarity="shrug", target="x"), "polarity must be"),
    (dict(text="t", polarity="suppress", target="x", strength=7), "strength must be"),
    (dict(text="t", polarity="suppress", target="x", strength=0), "strength must be"),
    (dict(text="t", polarity="suppress"), "name what to suppress"),
    (dict(text="t", polarity="emphasize"), "name what to emphasize"),
    (dict(text="t", polarity="suppress", target="x", scope="job"), "job id"),
    (dict(text="t", polarity="suppress", target="x", scope="job", scope_value="not-a-uuid"), "job id"),
    (dict(text="t", polarity="suppress", target="x", scope="role_family"), "role family"),
    (dict(text="t", polarity="suppress", target="x", scope="galaxy"), "scope must be"),
])
def test_record_preference_refuses_with_a_reason_and_stores_nothing(kg, isolated_engine, kwargs, why):
    before = len(_prefs(isolated_engine, kg))
    out = memory.record_preference(kg, **kwargs)
    assert out["status"] == "refused" and why in out["reason"]
    assert len(_prefs(isolated_engine, kg)) == before


def test_a_format_rule_needs_no_target_and_is_stored_as_a_reframe(kg):
    out = memory.record_preference(kg, "Keep every bullet to one line.", "reframe", None, 4)
    assert out["status"] == "stored" and out["polarity"] == "reframe" and out["target_key"] is None
    assert not out["pinned"]


def test_record_preference_replaces_a_changed_subject_keeps_the_old_row_and_is_idempotent(kg, isolated_engine):
    first = memory.record_preference(kg, "Lead with Python", "emphasize", "skill:python", 3)
    assert memory.record_preference(kg, "Lead with Python", "emphasize", "skill:python", 3)["status"] == "already_recorded"
    raised = memory.record_preference(kg, "Always lead with Python, no exceptions", "emphasize", "skill:python", 5)
    assert raised["status"] == "superseded" and raised["supersedes"] == "Lead with Python" and raised["pinned"]
    flipped = memory.record_preference(kg, "Never mention Python", "suppress", "skill:python", 5)
    assert flipped["status"] == "superseded" and flipped["negative_pin"]
    rows = _prefs(isolated_engine, kg, target_key="skill:python")
    assert sorted(r.status for r in rows) == ["active", "superseded", "superseded"]
    assert first["preference_id"] in {str(r.preference_id) for r in rows}      # nothing was deleted


def test_record_preference_scopes_a_job_and_a_role_family(kg, isolated_engine):
    job = str(uuid4())
    scoped = memory.record_preference(kg, "Lead with Python here", "emphasize", "skill:python", 5, "job", job)
    assert (scoped["scope_type"], scoped["scope_value"]) == ("job", job)
    family = memory.record_preference(kg, "Lead with SQL for DS", "emphasize", "SQL", 5, "role_family", "Data_Science")
    assert (family["scope_type"], family["scope_value"]) == ("role_family", "data_science")
    assert [p["text"] for p in tools.art_pins(kg)["pins"]] == ["Never mention coursework projects"]
    assert "Lead with Python here" in [p["text"] for p in tools.art_pins(kg, job_id=job)["pins"]]
    assert "Lead with SQL for DS" in [p["text"] for p in tools.art_pins(kg, role_family="data_science")["pins"]]


def test_art_pins_is_the_briefings_pins_word_for_word(kg):
    memory.record_preference(kg, "Never mention Java.", "suppress", "Java", 5)
    memory.record_preference(kg, "Lead with SQL", "emphasize", "SQL", 3)               # strength 3: not a pin
    pins = tools.art_pins(kg)
    assert pins["count"] == 2 and pins["pins"] == tools.art_briefing(kg)["pins"]
    assert [p["text"] for p in pins["pins"]] == ["Never mention coursework projects", "Never mention Java."]
    assert all(p["text"] != "Lead with SQL" for p in pins["pins"])


def test_the_new_tools_are_in_the_contract_and_observe_and_record_are_writes(kg, jev):
    from harness.contract import BY_NAME

    assert BY_NAME["art_pins"].read_only
    assert not BY_NAME["observe"].read_only and not BY_NAME["record_preference"].read_only
    out = invoke("observe", kg, {"text": "thanks!"})
    assert out["action"] == "drop" and out["error"] is None
    out = invoke("record_preference", kg, {"text": "Never mention Kafka.", "polarity": "suppress",
                                           "target": "Kafka", "strength": 5})
    assert out["status"] == "stored" and out["pinned"]
    assert invoke("art_pins", kg, {})["count"] == 2
    assert invoke("record_preference", kg, {"text": "x", "polarity": "suppress", "target": "y"},
                  allow_writes=False)["error"]["code"] == "read_only"


# ── the user-prompt hook ─────────────────────────────────────────────────────

def test_the_hook_adds_one_line_when_a_message_may_be_a_preference(kg, jev, isolated_engine):
    msg = jev.say("Never mention Python on my resume.", p=0.99, direction="suppress", level=4.0, target=PYTHON, hard=0.9)
    payload = {"session_id": SESSION, "prompt": msg}
    line = hooks.user_prompt(kg, payload)
    assert line.startswith(mg.HOST_LINE) and "record_preference" in line and "suppress skill:python" in line
    assert hooks.user_prompt(kg, {"session_id": SESSION, "prompt": "thanks!"}) is None
    assert hooks.user_prompt(kg, {"session_id": SESSION}) is None                       # no prompt: nothing to read


def test_the_hook_tells_the_host_what_it_saved_and_keeps_the_editor_note(kg, jev, isolated_engine):
    saved = jev.say("Please lead with Python in my skills.", p=0.97, direction="emphasize", level=3.0, target=PYTHON)
    hooks.user_prompt(kg, {"session_id": SESSION, "prompt": "hi"})                        # first prompt: cursor only
    line = hooks.user_prompt(kg, {"session_id": SESSION, "prompt": saved})
    assert line.startswith("ART saved a standing preference") and saved in line
    assert len(_prefs(isolated_engine, kg, target_key="skill:python")) == 1


def test_the_hook_does_not_write_on_a_read_only_store(kg, jev, isolated_engine):
    msg = jev.say("Please lead with Python in my skills.", p=0.97, direction="emphasize", level=3.0, target=PYTHON)
    line = hooks.user_prompt(kg, {"session_id": SESSION, "prompt": msg}, allow_writes=False)
    assert line.startswith(mg.HOST_LINE) and _prefs(isolated_engine, kg, target_key="skill:python") == []
    reply = hooks.run("user-prompt", {"session_id": SESSION, "prompt": msg}, kg, allow_writes=False)
    assert reply["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_the_hook_never_breaks_the_prompt_when_the_gate_fails(kg, jev, monkeypatch, capsys):
    monkeypatch.setattr(memory, "observe", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hooks.user_prompt(kg, {"session_id": SESSION, "prompt": "Never mention Python."}) is None

    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (kg, True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(
        {"session_id": SESSION, "prompt": "Never mention Python.", "hook_event_name": "UserPromptSubmit"})))
    assert hooks.main(["user-prompt"]) == 0
    assert capsys.readouterr().out == ""                                  # silent, exit 0


def test_the_hook_prints_claude_codes_json_for_a_host_line(kg, jev, monkeypatch, capsys):
    msg = jev.say("Never mention Python on my resume.", p=0.99, direction="suppress", level=4.0, target=PYTHON, hard=0.9)
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (kg, True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": SESSION, "prompt": msg})))
    assert hooks.main(["user-prompt"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["hookSpecificOutput"]["additionalContext"].startswith(mg.HOST_LINE)


def test_the_hook_bounds_jevs_timeout_so_a_slow_api_cannot_stall_a_prompt(kg, monkeypatch, isolated_engine):
    seen = {}

    class Client(JevClient):
        def ask(self, state, questions):
            seen["timeout"], seen["retries"] = self.timeout, self.max_retries
            raise __import__("harness.decisions.client", fromlist=["JevError"]).JevError("timeout")

    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: Client("k", timeout=30.0, max_retries=3))
    d = memory.observe(kg, "Please lead with Python.", SESSION, bounded=True)
    assert d["reason"] == "jev_unavailable" and seen == {"timeout": memory.HOOK_TIMEOUT, "retries": 1}


def test_the_compact_hook_restores_a_job_scoped_pin_for_the_current_job(kg, isolated_engine):
    job = str(uuid4())
    memory.record_preference(kg, "Never list Looker on this one.", "suppress", "Looker", 5, "job", job)
    hooks.save_state(SESSION, {"cursor": 0, "job_id": job})
    text = hooks.session_start(kg, {"session_id": SESSION, "source": "compact"})
    assert "- Never mention coursework projects" in text and "- Never list Looker on this one." in text
    hooks.save_state("other", {"cursor": 0, "job_id": str(uuid4())})
    assert "Looker" not in hooks.session_start(kg, {"session_id": "other", "source": "compact"})
