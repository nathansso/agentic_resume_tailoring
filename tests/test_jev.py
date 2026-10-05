"""The Jev decisions engine (issue #193): client, cache, modes, recordings.

No test here touches the network. The client takes an injected transport, and
`tests/conftest.py` sets `ART_JEV_MODE=off` for every test so a developer's
`TYPESAFE_API_KEY` (a developer's, from the shell or `.env`) can never reach the API;
tests that exercise the engine choose their own mode.
"""

import json

import pytest
from sqlmodel import Session, select

import config  # noqa: F401
import database.db as db
from database.models import JevDecision
from harness.decisions import cache, engine, recordings
from harness.decisions.client import (
    DEFAULT_MODEL, JevClient, JevError, JevReplayMiss, resolve_api_key,
)
from harness.decisions.questions import (
    MAX_OPTIONS, Answer, AnswerError, Choice, Noul, QuestionError, Score, canonical,
)

FRUIT = {"apple": "A red or green fruit", "pear": None}


def fruit(text="Pick a fruit.", version="fruit@v1", **kw):
    return Choice(version, text, FRUIT, **kw)


def choice_answer(pick, probs=None, confidence=0.9):
    return {"type": "choice", "choice": pick, "confidence": confidence,
            "probabilities": probs or {pick: 1.0}}


class FakeTransport:
    """Stands in for `requests`: records every payload, answers from `answer_for`,
    or plays back `script` (a list of `(status, body)`) first."""

    def __init__(self, answer_for=None, model=DEFAULT_MODEL, script=None):
        self.answer_for = answer_for or (lambda state, wire: choice_answer("apple"))
        self.model, self.script, self.calls, self.headers = model, list(script or []), [], []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(payload)
        self.headers.append(headers)
        if self.script:
            return self.script.pop(0)
        answers = {qid: self.answer_for(payload["state"], wire)
                   for qid, wire in payload["questions"].items()}
        n = len(answers)
        return 200, {"model": self.model, "answers": answers,
                     "usage": {"input_tokens": 100 * n, "output_tokens": 10 * n}}

    @property
    def asked(self):
        return [[w["instructions"] for w in c["questions"].values()] for c in self.calls]


class Boom:
    """A transport that fails the test if anything calls it."""
    def __call__(self, *a, **k):
        raise AssertionError("the transport was called")


def client_with(transport, **kw):
    return JevClient("test-key-not-real", transport=transport, sleep=lambda s: None, **kw)


@pytest.fixture()
def auto(isolated_engine, monkeypatch):
    """Engine in auto mode on an empty store, with no key until `.use(transport)`
    installs a client."""
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)
    engine.reset_stats()

    class Env:
        def use(self, transport, **kw):
            client = client_with(transport, **kw)
            monkeypatch.setattr(engine, "get_client", lambda: client)
            return client
    return Env()


def rows():
    with Session(db.engine) as s:
        return s.exec(select(JevDecision)).all()


# ── questions and the wire format ────────────────────────────────────────────

def test_questions_serialize_to_the_documented_json():
    assert Noul("urgent@v1", "Does this convey urgency?", true="Time-sensitive",
                false="No urgency").wire() == {
        "type": "noul", "instructions": "Does this convey urgency?",
        "criteria": {"true": "Time-sensitive", "false": "No urgency"}}
    assert Noul("urgent@v1", "Urgent?").wire() == {"type": "noul", "instructions": "Urgent?"}
    assert fruit(no_match="none").wire() == {
        "type": "choice", "instructions": "Pick a fruit.",
        "criteria": {"apple": "A red or green fruit", "pear": None, "none": None}}
    assert Score("sev@v1", "How severe?", ["low", "mid", "high"]).wire() == {
        "type": "score", "instructions": "How severe?", "criteria": ["low", "mid", "high"]}


def test_limits_are_enforced_when_a_question_is_built():
    with pytest.raises(QuestionError):
        Choice("many@v1", "Which?", {f"o{i}": None for i in range(MAX_OPTIONS + 1)})
    Choice("many@v1", "Which?", {f"o{i}": None for i in range(MAX_OPTIONS)})    # 255 is fine
    with pytest.raises(QuestionError):
        Choice("one@v1", "Which?", {"only": None})
    with pytest.raises(QuestionError):
        Choice("dup@v1", "Which?", FRUIT, no_match="apple")
    for levels in (["one"], [str(i) for i in range(11)]):
        with pytest.raises(QuestionError):
            Score("sev@v1", "How severe?", levels)
    with pytest.raises(QuestionError):
        Noul("noversion", "Yes?")


def test_the_canonical_form_ignores_order_and_carries_the_version():
    a = Choice("fruit@v1", "Pick.", {"apple": None, "pear": "x"})
    b = Choice("fruit@v1", "Pick.", {"pear": "x", "apple": None})
    assert a.canonical() == b.canonical()
    assert a.canonical() != Choice("fruit@v2", "Pick.", {"apple": None, "pear": "x"}).canonical()
    assert canonical({"b": 1, "a": [1, {"d": 1, "c": 2}]}) == '{"a":[1,{"c":2,"d":1}],"b":1}'


def test_documented_responses_parse_into_normalized_answers():
    q = Choice("dept@v1", "Which team?", {"returns": None, "shipping": None, "billing": None})
    a = q.parse({"type": "choice", "choice": "returns", "confidence": 1.0,
                 "probabilities": {"shipping": 0.0, "returns": 1.0, "billing": 0.0}},
                model="jev-1.13.0")
    assert (a.value, a.p, a.p_of("shipping"), a.model, a.source) == (
        "returns", 1.0, 0.0, "jev-1.13.0", "jev")
    n = Noul("rep@v1", "Repeat?").parse({"type": "noul", "noul": 0.93})
    assert (n.value, n.p, n.confidence) == (0.93, 0.93, None)     # no confidence on a noul
    s = Score("sev@v1", "Severity?", ["a", "b", "c"]).parse(
        {"type": "score", "score": 1.43, "confidence": 0.35,
         "legend": {"0": "a"}, "probabilities": {"0": 0.0, "1": 0.57, "2": 0.43}})
    assert (s.value, s.p, s.probabilities["1"]) == (1.43, 0.35, 0.57)
    for bad in ({"type": "choice", "choice": "ships"}, {"type": "noul", "noul": 0.5},
                None, {"type": "choice"}):
        with pytest.raises(AnswerError):
            q.parse(bad)


# ── the client ───────────────────────────────────────────────────────────────

def test_a_request_is_the_documented_body_with_the_pinned_model(monkeypatch):
    monkeypatch.delenv("ART_JEV_MODEL", raising=False)
    t = FakeTransport()
    resp = client_with(t).ask({"text": "hi"}, {"q0": fruit(), "q1": fruit("Another.")})
    assert t.calls == [{"state": {"text": "hi"}, "model": "jev-1.13.0",
                        "questions": {"q0": fruit().wire(), "q1": fruit("Another.").wire()}}]
    assert t.headers[0]["Authorization"] == "Bearer test-key-not-real"
    assert resp.model == "jev-1.13.0" and set(resp.answers) == {"q0", "q1"}
    assert resp.usage == {"input_tokens": 200, "output_tokens": 20}


def test_the_model_is_pinned_and_overridable(monkeypatch):
    monkeypatch.delenv("ART_JEV_MODEL", raising=False)
    assert DEFAULT_MODEL == "jev-1.13.0" and JevClient("k").model == "jev-1.13.0"
    monkeypatch.setenv("ART_JEV_MODEL", "jev-preview")
    assert JevClient("k").model == "jev-preview"


@pytest.mark.parametrize("status", [429, 529])
def test_it_backs_off_and_retries_on_429_and_529(status):
    sleeps = []
    t = FakeTransport(script=[(status, {"message": "slow down", "error_type": "rate"}),
                              (status, {"message": "slow down"})])
    client = JevClient("k", transport=t, sleep=sleeps.append, backoff=0.5)
    assert client.ask("s", {"q0": fruit()}).answers["q0"]["choice"] == "apple"
    assert len(t.calls) == 3 and sleeps == [0.5, 1.0]         # exponential


def test_retries_are_bounded_and_the_last_error_is_raised():
    t = FakeTransport(script=[(429, {"message": "no", "error_type": "rate_limit"})] * 9)
    sleeps = []
    client = JevClient("k", transport=t, sleep=sleeps.append, max_retries=3,
                       backoff=1.0, max_delay=2.5)
    with pytest.raises(JevError) as err:
        client.ask("s", {"q0": fruit()})
    assert err.value.code == 429 and err.value.error_type == "rate_limit"
    assert len(t.calls) == 4 and sleeps == [1.0, 2.0, 2.5]    # capped at max_delay


@pytest.mark.parametrize("status", [401, 422, 500])
def test_it_does_not_retry_401_422_or_anything_else(status):
    t = FakeTransport(script=[(status, {"message": "bad", "error_type": "e"})])
    with pytest.raises(JevError) as err:
        client_with(t).ask("s", {"q0": fruit()})
    assert err.value.code == status and len(t.calls) == 1


def test_an_oversized_request_is_refused_before_sending():
    t = FakeTransport()
    with pytest.raises(JevError) as err:
        client_with(t).ask("x" * 250_000, {"q0": fruit()})
    assert err.value.code == "request_too_large" and t.calls == []


def test_the_key_never_appears_in_repr_or_errors(monkeypatch):
    t = FakeTransport(script=[(401, {"message": "invalid key", "error_type": "auth"})])
    client = client_with(t)
    assert "test-key-not-real" not in repr(client)
    with pytest.raises(JevError) as err:
        client.ask("s", {"q0": fruit()})
    assert "test-key-not-real" not in str(err.value)


def test_the_key_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setitem(__import__("sys").modules, "keyring", None)   # not installed
    assert resolve_api_key() is None
    monkeypatch.setenv("TYPESAFE_API_KEY", "  abc  ")
    assert resolve_api_key() == "abc"


def test_the_keyring_is_the_fallback_when_installed(monkeypatch):
    import types

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    fake = types.SimpleNamespace(get_password=lambda service, user: "from-keyring")
    monkeypatch.setitem(__import__("sys").modules, "keyring", fake)
    assert resolve_api_key() == "from-keyring"


# ── the cache, per question ──────────────────────────────────────────────────

def test_a_request_with_one_new_question_sends_only_that_question(auto):
    t = FakeTransport()
    auto.use(t)
    first = engine.decide("fruit", {"s": 1}, [fruit("One?"), fruit("Two?")])
    assert [a.source for a in first] == ["jev", "jev"] and len(t.calls) == 1
    again = engine.decide("fruit", {"s": 1}, [fruit("One?"), fruit("Two?"), fruit("Three?")])
    assert [a.source for a in again] == ["cache", "cache", "jev"]
    assert t.asked == [["One?", "Two?"], ["Three?"]]           # the cached two were not re-sent
    assert len(rows()) == 3


def test_a_cache_hit_skips_the_transport(auto):
    t = FakeTransport()
    auto.use(t)
    engine.decide("fruit", {"s": 1}, [fruit()])
    auto.use(Boom())
    (hit,) = engine.decide("fruit", {"s": 1}, [fruit()])
    assert hit.source == "cache" and hit.value == "apple"


def test_the_key_covers_the_state_the_question_its_version_and_the_model(auto, monkeypatch):
    monkeypatch.delenv("ART_JEV_MODEL", raising=False)
    t = FakeTransport()
    auto.use(t)
    engine.decide("fruit", {"s": 1}, [fruit()])
    engine.decide("fruit", {"s": 2}, [fruit()])                       # other state
    engine.decide("fruit", {"s": 1}, [fruit("Reworded.")])            # other question
    engine.decide("fruit", {"s": 1}, [fruit(version="fruit@v2")])     # other version
    monkeypatch.setenv("ART_JEV_MODEL", "jev-preview")
    auto.use(t)
    engine.decide("fruit", {"s": 1}, [fruit()])                       # other requested model
    assert len(t.calls) == 5 and len(rows()) == 5


def test_the_resolved_model_version_is_stored(auto):
    auto.use(FakeTransport(model="jev-1.13.1"))         # the alias moved under us
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()])
    (row,) = rows()
    assert (row.requested_model, row.resolved_model) == ("jev-1.13.0", "jev-1.13.1")
    assert a.model == "jev-1.13.1" and row.point == "fruit" and row.question_version == "fruit@v1"
    assert row.question == fruit().wire() and row.answer["choice"] == "apple"
    assert row.input_tokens == 100 and row.output_tokens == 10 and row.created_at
    auto.use(Boom())
    assert engine.decide("fruit", {"s": 1}, [fruit()])[0].model == "jev-1.13.1"


def test_usage_is_shared_across_the_questions_of_one_request(auto):
    auto.use(FakeTransport())
    engine.decide("fruit", "s", [fruit("A?"), fruit("B?")])
    assert {r.input_tokens for r in rows()} == {100.0} and len(rows()) == 2   # 200 / 2


def test_a_failed_cache_write_is_logged_not_raised(auto, monkeypatch, caplog):
    auto.use(FakeTransport())

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("read-only database")
    monkeypatch.setattr(cache, "Session", Broken)
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()])
    assert a.source == "jev" and a.value == "apple"
    assert "could not store" in caplog.text


def test_a_bad_answer_falls_back_and_is_not_cached(auto):
    auto.use(FakeTransport(answer_for=lambda s, w: {"type": "choice", "choice": "kiwi"}))
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()], fallback="none")
    assert (a.source, a.reason, a.value) == ("fallback", "bad_answer", "none")
    assert rows() == []


def test_identical_questions_in_one_call_are_sent_once(auto):
    t = FakeTransport()
    auto.use(t)
    answers = engine.decide("fruit", {"s": 1}, [fruit("Same?"), fruit("Same?")])
    assert [a.value for a in answers] == ["apple", "apple"] and t.asked == [["Same?"]]


def test_a_question_that_belongs_to_another_point_is_a_bug():
    with pytest.raises(ValueError):
        engine.decide("support", {"s": 1}, [fruit()])


# ── modes ────────────────────────────────────────────────────────────────────

def test_replay_raises_on_a_miss_and_never_calls_the_transport(auto, monkeypatch):
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    with pytest.raises(JevReplayMiss) as err:
        engine.decide("fruit", {"s": 1}, [fruit()], fallback="none")   # no silent fallback
    assert "fruit@v1" in str(err.value)
    assert engine.stats()["fruit"]["replay_miss"] == 1


def test_replay_serves_recorded_decisions_and_reports_a_full_hit_rate(auto, monkeypatch):
    auto.use(FakeTransport())
    engine.decide("fruit", {"s": 1}, [fruit("One?"), fruit("Two?")])
    engine.reset_stats()
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    answers = engine.decide("fruit", {"s": 1}, [fruit("One?"), fruit("Two?")])
    assert [a.source for a in answers] == ["cache", "cache"]
    assert engine.stats()["fruit"]["hit_rate"] == 1.0
    with pytest.raises(JevReplayMiss):                    # one new question is enough to fail
        engine.decide("fruit", {"s": 1}, [fruit("One?"), fruit("New?")])


def test_off_never_calls_the_transport_and_never_reads_the_cache(auto, monkeypatch):
    auto.use(FakeTransport())
    engine.decide("fruit", {"s": 1}, [fruit()])
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "off")
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()], fallback=lambda q: q.point.upper())
    assert (a.source, a.reason, a.value) == ("fallback", "mode=off", "FRUIT")


def test_an_unrecognized_mode_is_off_not_auto(auto, monkeypatch):
    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "aotu")
    assert engine.mode() == "off"
    assert engine.decide("fruit", {"s": 1}, [fruit()])[0].source == "fallback"


def test_auto_without_a_key_uses_the_fallback(auto, monkeypatch):
    monkeypatch.setattr(engine, "get_client", lambda: None)
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()], fallback="none")
    assert (a.source, a.reason, a.value) == ("fallback", "no_key", "none")
    assert rows() == []


@pytest.mark.parametrize("status", [401, 422, 429, 500, 529])
def test_an_api_error_uses_the_fallback_and_caches_nothing(auto, status):
    t = FakeTransport(script=[(status, {"message": "x"})] * 9)
    auto.use(t, max_retries=1)
    (a,) = engine.decide("fruit", {"s": 1}, [fruit()], fallback="none")
    assert (a.source, a.reason) == ("fallback", f"api_error:{status}") and rows() == []


def test_a_question_too_large_for_the_limit_falls_back(auto):
    t = FakeTransport()
    auto.use(t)
    (a,) = engine.decide("fruit", "x" * 110_000, [fruit()])          # > 32k tokens estimated
    assert (a.source, a.reason) == ("fallback", "too_large") and t.calls == []


def test_stats_count_answers_by_source_per_point(auto, monkeypatch):
    auto.use(FakeTransport())
    engine.decide("fruit", {"s": 1}, [fruit("A?"), fruit("B?")])
    engine.decide("fruit", {"s": 1}, [fruit("A?")])
    monkeypatch.setenv("ART_JEV_MODE", "off")
    engine.decide("fruit", {"s": 1}, [fruit("A?")])
    st = engine.stats()["fruit"]
    assert (st["jev"], st["cache"], st["fallback"], st["requests"]) == (2, 1, 1, 1)
    assert st["input_tokens"] == 200 and st["reasons"] == {"mode=off": 1}
    assert st["hit_rate"] == 0.25


# ── recordings ───────────────────────────────────────────────────────────────

def test_recordings_round_trip_into_another_store(auto, monkeypatch, tmp_path):
    auto.use(FakeTransport(model="jev-1.13.1"))
    engine.decide("fruit", {"s": 1}, [fruit("A?"), fruit("B?")])
    doc = recordings.export_recordings()
    assert doc["format"] == "art-jev-recordings" and doc["version"] == 1
    assert len(doc["decisions"]) == 2 and all("state" not in d for d in doc["decisions"])
    assert {d["resolved_model"] for d in doc["decisions"]} == {"jev-1.13.1"}

    with Session(db.engine) as s:
        for r in s.exec(select(JevDecision)).all():
            s.delete(r)
        s.commit()
    assert recordings.import_recordings(doc) == {"added": 2, "replaced": 0, "skipped": 0}
    assert recordings.import_recordings(doc) == {"added": 0, "replaced": 0, "skipped": 2}
    assert recordings.import_recordings(doc, overwrite=True)["replaced"] == 2

    auto.use(Boom())
    monkeypatch.setenv("ART_JEV_MODE", "replay")
    assert [a.source for a in engine.decide("fruit", {"s": 1}, [fruit("A?"), fruit("B?")])] == [
        "cache", "cache"]


def test_a_recording_in_an_unknown_format_or_version_is_refused():
    for doc in ({"format": "other", "version": 1, "decisions": []},
                {"format": "art-jev-recordings", "version": 2, "decisions": []},
                {"format": "art-jev-recordings", "version": 1, "decisions": [{"cache_key": "k"}]},
                []):
        with pytest.raises(recordings.RecordingError):
            recordings.import_recordings(doc)


def test_the_cli_exports_imports_and_reports_status_without_the_key(
        auto, monkeypatch, tmp_path, capsys):
    auto.use(FakeTransport())
    engine.decide("fruit", {"s": 1}, [fruit()])
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-secret-value")
    path = tmp_path / "rec.json"

    def run(*argv):
        return recordings.run(recordings._parser().parse_args(list(argv)))

    assert run("export", str(path)) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["decisions"][0]["point"] == "fruit"
    capsys.readouterr()
    assert run("status") == 0
    out = capsys.readouterr().out
    status = json.loads(out)
    assert status["key"] == "set" and status["cache"]["fruit"]["decisions"] == 1
    assert status["mode"] == "auto" and status["model"] == "jev-1.13.0"
    assert "sk-secret-value" not in out
    assert run("import", str(path)) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == 1
    path.write_text("{}", encoding="utf-8")
    assert run("import", str(path)) == 2


def test_export_says_on_stderr_that_the_file_is_personal_data(auto, tmp_path, capsys):
    auto.use(FakeTransport())
    engine.decide("fruit", {"s": 1}, [fruit()])

    def run(*argv):
        return recordings.run(recordings._parser().parse_args(list(argv)))

    path = str(tmp_path / "rec.json")
    assert run("export", path) == 0
    out, err = capsys.readouterr()
    assert recordings.EXPORT_NOTICE in err and "personal data" in err
    assert json.loads(out) == {"exported": 1, "path": path}      # stdout stays one JSON document

    assert run("export", "-") == 0                  # the recording itself on stdout is still JSON
    out, err = capsys.readouterr()
    assert json.loads(out)["format"] == "art-jev-recordings" and "personal data" in err

    assert run("status") == 0                       # no notice for a command that writes no file
    assert "personal data" not in capsys.readouterr().err


def test_art_jev_is_dispatched_by_the_console_script(monkeypatch):
    from harness import entry

    seen = []
    monkeypatch.setattr(recordings, "main", lambda argv: seen.append(list(argv)) or 7)
    assert entry.main(["jev", "status"]) == 7 and seen == [["status"]]


# ── the boundary ─────────────────────────────────────────────────────────────

def test_the_decisions_package_is_the_only_network_call_under_harness():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "harness"
    users = {p.relative_to(root).as_posix() for p in root.rglob("*.py")
             if "import requests" in p.read_text(encoding="utf-8")
             or "from requests" in p.read_text(encoding="utf-8")}
    assert users == {"decisions/client.py"}


# ── live (needs a key; skipped without one) ──────────────────────────────────

@pytest.mark.integration
def test_the_live_api_answers_a_choice_and_a_noul(isolated_engine, monkeypatch, live_secrets):
    from harness.decisions.client import KEY_ENV

    # The developer's key, released only to this integration-marked test (#249):
    # `conftest` removes every key from the environment and holds them privately.
    live_key = live_secrets(KEY_ENV)[KEY_ENV]
    if not live_key:
        pytest.skip(f"{KEY_ENV} is not set")
    monkeypatch.setenv(KEY_ENV, live_key)
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    engine.reset_stats()
    q = Choice("live@v1", "How does the evidence relate to the claim?",
               {"supported": "The evidence states the claim.",
                "says_nothing": "The evidence does not mention the claim.",
                "contradicts": "The evidence says otherwise."})
    n = Noul("live_noul@v1", "Does the text mention Python?")
    state = {"evidence": "Wrote Python ETL jobs.", "claim": "Wrote ETL jobs in Python."}
    a = engine.decide("live", state, [q])[0]
    b = engine.decide("live_noul", state, [n])[0]
    assert a.source == "jev" and a.value == "supported" and a.model.startswith("jev-")
    assert b.source == "jev" and 0.0 <= b.value <= 1.0
    assert engine.decide("live", state, [q])[0].source == "cache"     # second call is cached
