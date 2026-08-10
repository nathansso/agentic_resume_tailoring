"""Cassette record/replay at the `llm.get_llm` seam (issue #171, chunk 1.3).

The benchmark's only affordable high-fidelity mode is one that runs the real
pipeline against recorded model responses. These tests cover the properties
that mode depends on: an exact round trip on both surfaces the app uses, an
occurrence counter that survives one prompt legitimately returning two
different samples, per-task scoping, and a miss that is fatal and never reaches
a provider.
"""
import json

import pytest
from pydantic import BaseModel

from eval.cassettes import (
    Cassette,
    CassetteMiss,
    CassetteSession,
    MODE_RECORD,
    MODE_REPLAY,
    SETUP_SCOPE,
    render_prompt,
)


class _Schema(BaseModel):
    """Stand-in for the #142 extraction schemas."""
    items: list[str] = []
    note: str = ""


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeStructured:
    def __init__(self, results):
        # Shares the caller's list on purpose: a retry builds a fresh runnable
        # but must draw the *next* sample, as a real provider would.
        self._results = results

    def invoke(self, _prompt_value):
        value = self._results.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class _FakeLLM:
    """A real model stand-in that can return a different sample each call."""

    def __init__(self, responses=(), structured=()):
        self._responses = list(responses)
        self._structured = list(structured)
        self.calls = 0

    def invoke(self, _input, config=None, **kwargs):
        self.calls += 1
        value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return _FakeMessage(value)

    def with_structured_output(self, schema, **kwargs):
        self.calls += 1
        return _FakeStructured(self._structured)


def _factory_for(llm):
    def _factory(role="chat", temperature=0.0):
        return llm
    return _factory


def _record_session(llm):
    return CassetteSession(Cassette(), MODE_RECORD, factory=_factory_for(llm))


def _replay_session(cassette):
    # No factory at all: a replay run cannot reach a provider even with keys set.
    return CassetteSession(cassette, MODE_REPLAY)


# ── prompt rendering ───────────────────────────────────────────────────────────

def test_render_prompt_covers_every_shape_the_app_passes():
    from langchain_core.prompts import ChatPromptTemplate

    pv = ChatPromptTemplate.from_messages([("user", "hello {name}")]).invoke(
        {"name": "world"})
    assert "hello world" in render_prompt(pv)
    # agents/tailor_planner.py invokes with a list of raw dicts.
    assert "hi" in render_prompt([{"role": "user", "content": "hi"}])
    assert render_prompt("plain") == "plain"


# ── .invoke round trip ─────────────────────────────────────────────────────────

def test_invoke_round_trips_through_the_cassette():
    llm = _FakeLLM(responses=['{"ok": true}'])
    session = _record_session(llm)
    recorded = session.llm_factory()(role="tailor", temperature=0.3).invoke("prompt")
    assert recorded.content == '{"ok": true}'
    assert llm.calls == 1

    replay = _replay_session(session.cassette)
    played = replay.llm_factory()(role="tailor", temperature=0.3).invoke("prompt")
    assert played.content == '{"ok": true}'
    assert replay.misses == []


def test_same_prompt_twice_replays_both_samples_in_order():
    """The occurrence counter, not the prompt hash, is what makes this work.

    `agents/tailor.py` runs at temperature=0.3 with MAX_RETRIES=2, so one
    rendered prompt can legitimately be invoked twice and return two different
    samples. Keyed on the hash alone, the second call would replay the first
    sample and the recording would silently misrepresent the run.
    """
    llm = _FakeLLM(responses=["first sample", "second sample"])
    session = _record_session(llm)
    model = session.llm_factory()(role="tailor", temperature=0.3)
    assert model.invoke("same prompt").content == "first sample"
    assert model.invoke("same prompt").content == "second sample"
    assert [e["occurrence"] for e in session.cassette._order] == [0, 1]

    replay = _replay_session(session.cassette)
    played = replay.llm_factory()(role="tailor", temperature=0.3)
    assert played.invoke("same prompt").content == "first sample"
    assert played.invoke("same prompt").content == "second sample"


# ── structured-output surface (the #142 extraction seam) ──────────────────────

def test_structured_output_round_trips_as_a_validated_model():
    llm = _FakeLLM(structured=[_Schema(items=["a", "b"], note="n")])
    session = _record_session(llm)
    runnable = session.llm_factory()().with_structured_output(_Schema)
    assert runnable.invoke("extract this").items == ["a", "b"]

    replay = _replay_session(session.cassette)
    played = replay.llm_factory()().with_structured_output(_Schema).invoke(
        "extract this")
    assert isinstance(played, _Schema)
    assert played.items == ["a", "b"] and played.note == "n"


def test_both_surfaces_are_recorded_distinguishably():
    llm = _FakeLLM(responses=["msg"], structured=[_Schema(note="s")])
    session = _record_session(llm)
    model = session.llm_factory()(role="extract")
    model.invoke("p1")
    model.with_structured_output(_Schema).invoke("p2")
    surfaces = {e["surface"] for e in session.cassette._order}
    assert surfaces == {"invoke", "structured"}
    assert {e["schema"] for e in session.cassette._order} == {None, "_Schema"}


def test_replaying_a_different_schema_is_a_miss_not_a_coercion():
    class _Other(BaseModel):
        note: str = ""

    llm = _FakeLLM(structured=[_Schema(note="s")])
    session = _record_session(llm)
    session.llm_factory()().with_structured_output(_Schema).invoke("p")

    replay = _replay_session(session.cassette)
    with pytest.raises(CassetteMiss):
        replay.llm_factory()().with_structured_output(_Other).invoke("p")


# ── failures record nothing and consume no occurrence ──────────────────────────

def test_a_failed_provider_call_records_nothing_and_advances_nothing():
    """Recording on success keeps the replayed stream aligned with reality.

    `llm.StructuredExtractor` retries a failed extraction. If the failure
    consumed an occurrence, replay would ask for occurrence 0 and find only the
    entry recorded at 1.
    """
    llm = _FakeLLM(structured=[RuntimeError("transient 529"), _Schema(note="ok")])
    session = _record_session(llm)
    runnable = session.llm_factory()().with_structured_output(_Schema)
    with pytest.raises(RuntimeError):
        runnable.invoke("p")
    assert runnable.invoke("p").note == "ok"
    assert [e["occurrence"] for e in session.cassette._order] == [0]

    replay = _replay_session(session.cassette)
    assert replay.llm_factory()().with_structured_output(_Schema).invoke(
        "p").note == "ok"


# ── misses are fatal and never fall through to a live call ────────────────────

def test_a_miss_raises_and_is_recorded_on_the_session():
    llm = _FakeLLM(responses=["recorded"])
    session = _record_session(llm)
    session.llm_factory()().invoke("the prompt that was recorded")

    replay = _replay_session(session.cassette)
    with pytest.raises(CassetteMiss):
        replay.llm_factory()().invoke("a prompt nobody recorded")
    assert len(replay.misses) == 1
    assert "no recorded interaction" in replay.misses[0]["reason"]


def test_a_swallowed_miss_still_fails_the_run():
    """Call sites like agents/parser.py catch every exception and return [].

    Without the session-level tally, a miss would reach the metrics as a
    silently empty parse rather than as an error.
    """
    replay = _replay_session(Cassette())
    try:
        replay.llm_factory()().invoke("nothing recorded")
    except Exception:
        pass  # exactly what the degrade-gracefully call sites do
    with pytest.raises(SystemExit, match="cassette miss"):
        replay.assert_no_misses()


def test_no_misses_is_silent():
    _replay_session(Cassette()).assert_no_misses()


# ── scoping ────────────────────────────────────────────────────────────────────

def test_scopes_keep_per_task_counters_independent():
    llm = _FakeLLM(responses=["task-a", "task-b"])
    session = _record_session(llm)
    model = session.llm_factory()()
    with session.scope("task_a"):
        model.invoke("identical prompt")
    with session.scope("task_b"):
        model.invoke("identical prompt")
    assert [e["scope"] for e in session.cassette._order] == ["task_a", "task_b"]
    # Both are occurrence 0: the counter is per scope, so replaying task_b
    # alone does not depend on task_a's call count.
    assert [e["occurrence"] for e in session.cassette._order] == [0, 0]

    replay = _replay_session(session.cassette)
    played = replay.llm_factory()()
    with replay.scope("task_b"):
        assert played.invoke("identical prompt").content == "task-b"


def test_scope_restores_the_previous_scope():
    session = _record_session(_FakeLLM())
    assert session.current_scope == SETUP_SCOPE
    with session.scope("task"):
        assert session.current_scope == "task"
    assert session.current_scope == SETUP_SCOPE


# ── persistence ────────────────────────────────────────────────────────────────

def test_cassette_save_load_round_trip(tmp_path):
    llm = _FakeLLM(responses=["a", "b"], structured=[_Schema(items=["x"])])
    session = _record_session(llm)
    model = session.llm_factory()(role="tailor", temperature=0.3)
    with session.scope("task_a"):
        model.invoke("p")
        model.invoke("p")
        model.with_structured_output(_Schema).invoke("q")
    session.cassette.meta.update({"recorded_at": "20260810T000000"})

    path = session.cassette.save(tmp_path / "c.json")
    reloaded = Cassette.load(path)
    assert len(reloaded) == 3
    assert reloaded.scopes() == ["task_a"]
    assert reloaded.meta["recorded_at"] == "20260810T000000"

    replay = CassetteSession(reloaded, MODE_REPLAY)
    played = replay.llm_factory()(role="tailor", temperature=0.3)
    with replay.scope("task_a"):
        assert played.invoke("p").content == "a"
        assert played.invoke("p").content == "b"
        assert played.with_structured_output(_Schema).invoke("q").items == ["x"]


def test_cassette_rejects_a_foreign_version(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"version": 99, "interactions": []}),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="cassette version"):
        Cassette.load(path)


def test_missing_cassette_names_how_to_record_one(tmp_path):
    with pytest.raises(FileNotFoundError, match="record one first"):
        Cassette.load(tmp_path / "absent.json")


def test_record_mode_requires_a_real_factory():
    with pytest.raises(ValueError, match="real get_llm"):
        CassetteSession(Cassette(), MODE_RECORD)
