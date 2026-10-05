"""The memory gate reads the previous assistant turn for a context-dependent message (issue #244).

Detection (`unresolved_reference`), the transcript reader (`harness/transcript.py`), `memory_gate@v2` and
how `observe` and the `user-prompt` hook pick between v1 and v2. Jev is scripted with a fake transport, as in
`tests/test_memory_gate.py`; the real answers and the thresholds fitted on them are
`tests/test_memory_gate_context_labels.py`. Nothing reaches the network.
"""

import io
import json
import time
from pathlib import Path

import pytest

from harness import hooks, memory, transcript
from harness.decisions import engine, memory_gate as mg
from harness.decisions.client import JevClient

SESSION = "ctx-test"
PYTHON = "skill: Python"
NEVER = "Never list that again."
PREV = "I added Hadoop to your skills line."


# ── a scripted Jev that records what it was sent ─────────────────────────────

class ScriptedJev:
    def __init__(self):
        self.calls = []
        self.p = 0.9

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(payload)
        answers = {}
        for qid, q in payload["questions"].items():
            if q["type"] == "noul":
                answers[qid] = {"type": "noul", "noul": self.p}
            elif q["type"] == "score":
                answers[qid] = {"type": "score", "score": 3.0, "confidence": 0.9, "probabilities": {"4": 0.0}}
            elif "emphasize" in q["criteria"]:
                answers[qid] = {"type": "choice", "choice": "suppress", "confidence": 0.9,
                                "probabilities": {"suppress": 0.9}}
            else:
                answers[qid] = {"type": "choice", "choice": "no_match", "confidence": 0.9,
                               "probabilities": {"no_match": 0.9}}
        return 200, {"model": "jev-1.13.0", "answers": answers, "usage": {"input_tokens": 10, "output_tokens": 2}}


@pytest.fixture()
def jev(monkeypatch, isolated_engine):
    fake = ScriptedJev()
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: JevClient("k", transport=fake, sleep=lambda s: None))
    return fake


def _line(role, content, **extra):
    return json.dumps({"type": role, "message": {"role": role, "content": content}, **extra})


def _write(path, lines):
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _turn(text):
    return _line("assistant", [{"type": "text", "text": text}])


# ── detection ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "Never list that again.", "Drop it from now on.", "Keep it like that.", "Do the same on every resume.",
    "Good, always do it that way.", "Don't leave it off again.", "Yes, always.", "Don't, ever.", "No, never.",
    "Never remove the last one.", "Always lead with the first one.", "Never mention it as before.",
])
def test_a_message_with_an_unresolved_reference_is_detected(text):
    assert mg.unresolved_reference(text, mg.make_catalog(skills=["Python"]))


@pytest.mark.parametrize("text", [
    "Always lead with the Transit Pulse project.", "Never mention Excel on my resume.",
    "Make sure that my GPA is off every resume.", "No more than four bullets per role.",
    "Never go above three per role.", "I prefer concise bullet points.", "", "   ",
    "Please make sure that skills I list are real.", "Good morning, ready to work on my resume?",
])
def test_a_message_that_reads_on_its_own_is_not(text):
    assert mg.unresolved_reference(text, mg.make_catalog(skills=["Python"])) == []


def test_a_pronoun_with_its_antecedent_in_the_message_is_resolved_but_an_anaphoric_phrase_is_not():
    catalog = mg.make_catalog(skills=["Looker", "Excel"])
    assert mg.unresolved_reference("Don't ever list Looker, I barely used it.", catalog) == []
    assert mg.unresolved_reference("Skip the Rivermount internship for this one.", catalog) == ["this one"]
    # "again" and "the same" point back whatever else the message says
    assert "again" in mg.unresolved_reference("Never put Excel there again.", catalog)
    assert mg.unresolved_reference("Do the same for Looker.", catalog)
    # with no catalog a bare pronoun cannot be resolved, so it is flagged
    assert mg.unresolved_reference("Don't ever list Looker, I barely used it.")


def test_the_detector_flags_few_of_the_self_contained_messages_of_the_main_set():
    from eval import fit_memory_gate_threshold as fit

    catalog = fit.profile_catalog()
    flagged = [p for p in fit.load_pairs() if mg.unresolved_reference(p["message"], catalog)]
    candidates = [p["id"] for p in flagged if mg.prefilter(p["message"]).candidate]
    assert len(flagged) <= 10 and len(candidates) <= 5, candidates       # a flag there is a false detection
    assert not [p for p in flagged if p["is_preference"]]                  # none is a preference: no true one is turned to v2


# ── the transcript reader ────────────────────────────────────────────────────

def test_the_previous_assistant_turn_is_the_last_assistant_text(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        _line("user", [{"type": "text", "text": "tailor this"}]),
        _turn("Which job?"),
        _line("user", [{"type": "text", "text": "the second one"}]),
        _turn("I put Hadoop on your skills line."),
    ])
    assert transcript.previous_assistant_turn(path, "Never list that again.") == "I put Hadoop on your skills line."


def test_tool_calls_and_results_are_dropped_and_only_text_is_kept(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        _line("user", [{"type": "text", "text": "do it"}]),
        _line("assistant", [{"type": "text", "text": "Let me check."},
                            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat secrets"}}]),
        _line("user", [{"type": "tool_result", "tool_use_id": "t1", "content": "TOOL OUTPUT"}]),
        _line("assistant", [{"type": "tool_use", "id": "t2", "name": "Read", "input": {"file_path": "/x"}}]),
        _line("user", [{"type": "tool_result", "tool_use_id": "t2", "content": "MORE OUTPUT"}]),
        _line("assistant", [{"type": "thinking", "thinking": "hidden"}, {"type": "text", "text": "Done: Excel is off."},
                            {"type": "tool_use", "id": "t3", "name": "Bash", "input": {"command": "ls"}}]),
    ])
    turn = transcript.previous_assistant_turn(path)
    assert turn == "Done: Excel is off."
    assert not any(w in turn for w in ("TOOL OUTPUT", "cat secrets", "hidden", "ls"))


def test_a_turn_the_assistant_answered_with_tool_calls_alone_has_no_text_and_is_not_an_older_turn(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        _line("user", [{"type": "text", "text": "first"}]),
        _turn("An old answer."),
        _line("user", [{"type": "text", "text": "second"}]),
        _line("assistant", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {}}]),
        _line("user", [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]),
    ])
    assert transcript.previous_assistant_turn(path) is None


def test_a_trailing_copy_of_the_current_prompt_is_skipped_once(tmp_path):
    lines = [_line("user", [{"type": "text", "text": "hello"}]), _turn("Excel is off."),
             _line("user", [{"type": "text", "text": "Never list that again."}])]
    path = _write(tmp_path / "t.jsonl", lines)
    assert transcript.previous_assistant_turn(path, "Never list that again.") == "Excel is off."
    assert transcript.previous_assistant_turn(path, "something else") is None      # a different, real user message
    # the user repeating themselves: the assistant's turn is last, so the earlier copy is not skipped
    again = _write(tmp_path / "u.jsonl", [_line("user", "No."), _turn("Want me to move skills up?")])
    assert transcript.previous_assistant_turn(again, "No.") == "Want me to move skills up?"
    # a plain-string user content and a system-injected line are understood
    plain = _write(tmp_path / "p.jsonl", [_turn("Real answer."), _line("user", "caveat", isMeta=True)])
    assert transcript.previous_assistant_turn(plain) == "Real answer."


def test_malformed_lines_and_foreign_entries_are_skipped(tmp_path):
    path = _write(tmp_path / "t.jsonl", [
        _turn("The good turn."), "{not json", "[1, 2]", '{"type": "system", "content": "x"}', "",
        json.dumps({"type": "assistant", "isSidechain": True, "message": {"role": "assistant",
                                                                        "content": [{"type": "text", "text": "a subagent"}]}}),
        json.dumps({"type": "assistant", "message": "nope"}), '{"type": "summary", "summary": "s"}'])
    assert transcript.previous_assistant_turn(path) == "The good turn."
    assert transcript.previous_assistant_turn(_write(tmp_path / "bad.jsonl", ["{", "}", "x"])) is None


def test_every_failure_is_none_and_nothing_raises(tmp_path):
    assert transcript.previous_assistant_turn(None) is None
    assert transcript.previous_assistant_turn("") is None
    assert transcript.previous_assistant_turn(123) is None                         # a bad payload value
    assert transcript.previous_assistant_turn(str(tmp_path / "missing.jsonl")) is None
    assert transcript.previous_assistant_turn(str(tmp_path)) is None                # a directory
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    assert transcript.previous_assistant_turn(str(empty)) is None
    binary = tmp_path / "bin.jsonl"
    binary.write_bytes(bytes(range(256)) * 50)
    assert transcript.previous_assistant_turn(str(binary)) is None
    assert transcript.previous_assistant_turn(str(empty), max_seconds=0) is None


def test_a_huge_transcript_is_read_from_the_end_and_stays_fast(tmp_path):
    path = tmp_path / "huge.jsonl"
    filler = (_line("user", [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 2000}]) + "\n").encode()
    with path.open("wb") as fh:
        for _ in range(20_000):                                                    # ~40 MB of old turns
            fh.write(filler)
        fh.write((_turn("The last words.") + "\n").encode())
        for _ in range(50):
            fh.write(filler)
    assert path.stat().st_size > 40_000_000
    start = time.monotonic()
    # the turn is under 50 tool results from the end: found without reading the 40 MB
    assert transcript.previous_assistant_turn(str(path)) == "The last words."
    assert time.monotonic() - start < transcript.MAX_SECONDS + 0.5
    # nothing readable in the last MAX_BYTES (and fewer than MAX_LINES lines): None, and still quick
    far = tmp_path / "far.jsonl"
    with far.open("wb") as fh:
        fh.write((_turn("Too far back.") + "\n").encode())
        for _ in range(2_000):
            fh.write(filler)
    start = time.monotonic()
    assert transcript.previous_assistant_turn(str(far), max_bytes=100_000) is None
    assert time.monotonic() - start < 1.0


def test_one_line_larger_than_the_read_budget_is_not_read(tmp_path):
    path = _write(tmp_path / "t.jsonl", [_turn("An older turn."), _turn("x" * 300_000)])
    assert transcript.previous_assistant_turn(path, max_bytes=100_000) is None
    assert transcript.previous_assistant_turn(path) == "x" * 300_000                # under the default budget, whole
    assert len(mg.clip_previous("x" * 300_000)) == mg.PREVIOUS_MAX                  # the state never carries it


def test_the_previous_turn_is_clipped_to_its_end():
    assert mg.PREVIOUS_MAX == 600
    short = "I put Hadoop on  your\nskills line."
    assert mg.clip_previous(short) == "I put Hadoop on your skills line."
    long = "A " * 400 + "and finally, Excel is off."
    clipped = mg.clip_previous(long)
    assert len(clipped) == 600 and clipped.startswith("…") and clipped.endswith("Excel is off.")


def test_a_codex_rollout_is_read_and_its_unstable_format_fails_to_v1(tmp_path):
    rollout = [
        json.dumps({"timestamp": "t", "type": "session_meta", "payload": {"id": "x"}}),
        json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}),
        json.dumps({"timestamp": "t", "type": "response_item", "payload": {
            "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "Hadoop is on the skills line."}]}}),
        json.dumps({"timestamp": "t", "type": "response_item", "payload": {"type": "function_call", "name": "x"}}),
        json.dumps({"timestamp": "t", "type": "event_msg", "payload": {"type": "token_count"}}),
    ]
    assert transcript.previous_assistant_turn(_write(tmp_path / "rollout.jsonl", rollout)) == "Hadoop is on the skills line."
    changed = [json.dumps({"v": 2, "items": [{"role": "assistant", "text": "a new format"}]})]
    assert transcript.previous_assistant_turn(_write(tmp_path / "new.jsonl", changed)) is None


# ── memory_gate@v2 ───────────────────────────────────────────────────────────

def test_v2_asks_the_same_four_questions_with_a_note_about_the_turn():
    catalog = mg.make_catalog(skills=["Python", "SQL"], experiences=[("Intern", "Acme")], projects=["Recipe App"])
    v1, v2 = mg.questions_for(catalog), mg.questions_for(catalog, mg.VERSION_V2)
    assert [q.kind for q in v2] == [q.kind for q in v1] and {q.version for q in v2} == {mg.VERSION_V2}
    assert all(q.point == mg.POINT for q in v2) and mg.VERSION != mg.VERSION_V2
    for a, b in zip(v1, v2):
        assert b.wire()["instructions"].startswith(a.wire()["instructions"])          # the same question
        assert mg.CONTEXT_NOTE in b.wire()["instructions"] and mg.CONTEXT_NOTE not in a.wire()["instructions"]
        assert a.canonical() != b.canonical()                                         # so a different cache key
    assert "only to work out what the message refers to" in mg.CONTEXT_NOTE
    assert v1[1].options == v2[1].options and v1[2].levels == v2[2].levels and v1[3].options == v2[3].options


def test_the_v2_state_is_the_message_and_the_previous_turn_and_v1_is_unchanged():
    assert mg.build_state_v2("  Never   list that again. ", PREV) == {
        "message": "Never list that again.", "previous_assistant_turn": PREV}
    assert mg.build_state("Never list that again.") == {"message": "Never list that again."}      # v1's cache key
    assert mg.version_for(None) == mg.version_for("   ") == mg.VERSION and mg.version_for(PREV) == mg.VERSION_V2
    assert mg.thresholds(mg.VERSION) == (mg.TAU_LO, mg.TAU_HI, mg.TAU_TARGET) == (0.25, 0.65, 0.75)
    assert mg.thresholds(mg.VERSION_V2) == (mg.TAU_LO_V2, mg.TAU_HI_V2, mg.TAU_TARGET_V2)


def _guess(version, **kw):
    base = dict(p=0.95, direction="emphasize", direction_p=0.9, strength=3, strength_confidence=0.9, hard_p=0.0,
                target_key="skill:python", target_label=PYTHON, target_p=0.99, target_named=True, source="jev")
    base.update(kw)
    return mg.Guess(version=version, **base)


def test_routing_takes_each_versions_thresholds_and_every_code_rule_applies_to_v2():
    text = "Always keep Python there like that."
    pre = mg.prefilter(text)
    g1, g2 = _guess(mg.VERSION, p=0.55), _guess(mg.VERSION_V2, p=0.55)
    assert mg.route(text, pre, g1)["reason"] == "uncertain"                    # v1 needs 0.65
    d = mg.route(text, pre, g2)
    assert d["version"] == mg.VERSION_V2 and (d["action"] == "write") == (0.55 >= mg.TAU_HI_V2 and 0.55 >= mg.TAU_TARGET_V2)
    assert mg.route(text, pre, _guess(mg.VERSION))["version"] == mg.VERSION
    # the code rules hold for v2 whatever its thresholds
    for kw, reason in [(dict(strength=5), "hard_preference"), (dict(hard_p=0.3), "hard_preference"),
                       (dict(target_key="section:skills"), "section_target"), (dict(target_named=False), "no_target"),
                       (dict(direction="suppress"), "negation_disagrees"), (dict(direction="format_rule"), "format_rule")]:
        d = mg.route(text, pre, _guess(mg.VERSION_V2, **kw), tau_lo=0.0, tau_hi=0.0, tau_target=0.0)
        assert (d["action"], d["reason"]) == ("host", reason), kw
    # a decision no Jev answer made has no version
    assert mg.route(text, pre, None, unavailable="no_key")["version"] is None
    assert mg.route("thanks", mg.prefilter("thanks"), None)["version"] is None


# ── observe: when the turn is used ───────────────────────────────────────────

def _states(jev):
    return [c["state"] for c in jev.calls]


def test_a_message_with_a_reference_and_a_previous_turn_runs_v2(kg, jev, isolated_engine):
    d = memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    assert (d["version"], d["context"]) == (mg.VERSION_V2, "used")
    [state] = _states(jev)
    assert state == {"message": NEVER, "previous_assistant_turn": PREV}
    assert d["guess"]["direction"] == "suppress" and d["action"] == "host"           # a strength-3 guess: no target named
    assert len(jev.calls) == 1 and len(jev.calls[0]["questions"]) == 4


def test_the_hook_style_transcript_path_gives_the_same_v2_call(kg, jev, tmp_path):
    path = _write(tmp_path / "t.jsonl", [_line("user", "go"), _turn(PREV)])
    d = memory.observe(kg, NEVER, SESSION, transcript_path=path)
    assert (d["version"], d["context"]) == (mg.VERSION_V2, "used")
    assert _states(jev) == [{"message": NEVER, "previous_assistant_turn": PREV}]


@pytest.mark.parametrize("path", ["missing", "directory", "garbage", "emptyfile", "notext", None])
def test_a_missing_or_unreadable_transcript_falls_back_to_v1_silently(kg, jev, tmp_path, path):
    paths = {"missing": str(tmp_path / "nope.jsonl"), "directory": str(tmp_path), None: None,
             "garbage": _write(tmp_path / "g.jsonl", ["{", "no json here"]),
             "emptyfile": _write(tmp_path / "e.jsonl", [""]),
             "notext": _write(tmp_path / "n.jsonl", [_line("user", "go"), _line("assistant", [{"type": "tool_use", "id": "t"}])])}
    d = memory.observe(kg, NEVER, SESSION, transcript_path=paths[path])
    assert (d["version"], d["context"]) == (mg.VERSION, "missing")
    assert _states(jev) == [{"message": NEVER}]                                      # v1's narrow state


def test_a_message_that_reads_on_its_own_never_gets_the_turn_and_never_opens_the_transcript(kg, jev, monkeypatch):
    monkeypatch.setattr(transcript, "previous_assistant_turn", lambda *a, **k: pytest.fail("the transcript was read"))
    msg = "Always lead with Python in my skills."
    d = memory.observe(kg, msg, SESSION, previous_turn=PREV, transcript_path="/never/read")
    assert (d["version"], d["context"]) == (mg.VERSION, "none")
    assert _states(jev) == [{"message": msg}]


def test_a_non_candidate_never_reads_the_transcript_either(kg, jev, monkeypatch):
    monkeypatch.setattr(transcript, "previous_assistant_turn", lambda *a, **k: pytest.fail("the transcript was read"))
    d = memory.observe(kg, "Thanks, that works.", SESSION, transcript_path="/never/read")
    assert (d["action"], d["source"], d["context"]) == ("drop", "prefilter", "none") and jev.calls == []


def test_v1_answers_for_messages_without_a_reference_still_hit_the_cache(kg, jev):
    msg = "Please lead with Python in my skills."
    first = memory.observe(kg, msg, SESSION, previous_turn=PREV)
    second = memory.observe(kg, msg, SESSION)
    assert len(jev.calls) == 1 and first["version"] == second["version"] == mg.VERSION
    assert second["source"] == "cache"
    # the v2 call for a referring message is cached under its own key, and v1's is not displaced
    memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    memory.observe(kg, NEVER, SESSION)
    assert len(jev.calls) == 3                                                          # v1 (msg), v2 (NEVER), v1 (NEVER)


def test_a_different_previous_turn_is_a_different_v2_question(kg, jev):
    memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    memory.observe(kg, NEVER, SESSION, previous_turn="Here is the Transit Pulse project.")
    assert len(jev.calls) == 2 and jev.calls[0]["state"] != jev.calls[1]["state"]


def test_without_a_key_a_referring_message_is_routed_by_the_prefilter_alone_with_no_version(kg, isolated_engine, monkeypatch):
    monkeypatch.setattr(engine, "get_client", lambda: None)
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    d = memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    assert (d["action"], d["reason"], d["version"], d["context"]) == ("host", "jev_unavailable", None, "used")


def test_a_decision_log_row_records_the_version_and_context_and_not_the_turn(kg, jev, isolated_engine):
    memory.observe(kg, NEVER, SESSION, previous_turn=PREV)
    memory.observe(kg, "Please lead with Python in my skills.", SESSION)
    rows = [json.loads(l) for l in (isolated_engine._test_profile_file.parent / "memory_gate.jsonl").read_text().splitlines()]
    assert [(r["version"], r["context"]) for r in rows] == [(mg.VERSION_V2, "used"), (mg.VERSION, "none")]
    assert "Hadoop" not in json.dumps(rows)


def test_a_write_from_v2_records_the_version_in_its_provenance(kg, jev, isolated_engine, monkeypatch):
    # a message that names its target, leans on the turn, and is a clear low-stakes preference
    from sqlmodel import Session, select
    from database.models import UserPreference

    class Named(ScriptedJev):
        def __call__(self, url, headers, payload, timeout):
            status, body = super().__call__(url, headers, payload, timeout)
            for qid, q in payload["questions"].items():
                if q["type"] == "choice" and "emphasize" not in q["criteria"]:
                    body["answers"][qid] = {"type": "choice", "choice": PYTHON, "confidence": 0.99, "probabilities": {PYTHON: 0.99}}
                elif q["type"] == "choice":
                    body["answers"][qid] = {"type": "choice", "choice": "emphasize", "confidence": 0.9,
                                           "probabilities": {"emphasize": 0.9}}
            return status, body

    fake = Named()
    monkeypatch.setattr(engine, "get_client", lambda: JevClient("k", transport=fake, sleep=lambda s: None))
    msg = "Please keep Python first again."
    d = memory.observe(kg, msg, SESSION, previous_turn="I moved Python down.")
    assert (d["action"], d["version"], d["context"]) == ("write", mg.VERSION_V2, "used")
    with Session(isolated_engine) as s:
        [pref] = [p for p in s.exec(select(UserPreference).where(UserPreference.user_id == kg)).all()
                  if p.target_key == "skill:python"]
    assert pref.provenance["gate"]["version"] == mg.VERSION_V2


# ── the hook ─────────────────────────────────────────────────────────────────

def test_the_hook_passes_the_transcript_path_to_observe(kg, monkeypatch):
    seen = {}

    def fake(user_id, text, session_id=None, **kw):
        seen.update(kw, text=text)
        return {"note": "ART: line"}

    monkeypatch.setattr(memory, "observe", fake)
    assert hooks.user_prompt(kg, {"session_id": SESSION, "prompt": NEVER, "transcript_path": "/t/x.jsonl"}) == "ART: line"
    assert seen["transcript_path"] == "/t/x.jsonl" and seen["bounded"] is True
    seen.clear()
    hooks.user_prompt(kg, {"session_id": SESSION, "prompt": NEVER})                                # no path (Codex may omit it)
    assert seen["transcript_path"] is None
    seen.clear()
    hooks.user_prompt(kg, {"session_id": SESSION, "prompt": NEVER, "transcript_path": None})        # Codex: nullable
    assert seen["transcript_path"] is None


@pytest.mark.parametrize("path", [123, ["a"], {"p": 1}, "\x00bad", "C:\\no\\such\\file.jsonl", "/" * 5000])
def test_the_hook_never_raises_on_a_bad_transcript_path(kg, jev, path):
    line = hooks.user_prompt(kg, {"session_id": SESSION, "prompt": NEVER, "transcript_path": path})
    assert line is None or line.startswith(mg.HOST_LINE)
    assert _states(jev) == [{"message": NEVER}]                                              # fell back to v1


def test_the_hook_reads_the_turn_from_a_claude_code_transcript_end_to_end(kg, jev, tmp_path, monkeypatch, capsys):
    path = _write(tmp_path / "t.jsonl", [_line("user", [{"type": "text", "text": "go"}]), _turn(PREV)])
    monkeypatch.setattr("harness.runtime.bootstrap", lambda *a, **k: (kg, True))
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({
        "session_id": SESSION, "prompt": NEVER, "hook_event_name": "UserPromptSubmit", "transcript_path": path,
        "cwd": str(tmp_path), "permission_mode": "default"})))
    assert hooks.main(["user-prompt"]) == 0
    reply = json.loads(capsys.readouterr().out)
    assert reply["hookSpecificOutput"]["additionalContext"].startswith(mg.HOST_LINE)
    assert _states(jev) == [{"message": NEVER, "previous_assistant_turn": PREV}]


def test_the_hook_reads_a_codex_rollout_and_codex_with_no_transcript_runs_v1(kg, jev, tmp_path):
    rollout = _write(tmp_path / "rollout.jsonl", [json.dumps({"timestamp": "t", "type": "response_item", "payload": {
        "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": PREV}]}})])
    hooks.user_prompt(kg, {"session_id": SESSION, "prompt": NEVER, "transcript_path": rollout, "model": "gpt"})
    hooks.user_prompt(kg, {"session_id": SESSION, "prompt": "Don't, ever.", "transcript_path": None, "model": "gpt"})
    assert _states(jev) == [{"message": NEVER, "previous_assistant_turn": PREV}, {"message": "Don't, ever."}]


def test_a_transcript_read_that_runs_out_of_time_is_v1(kg, jev, tmp_path, monkeypatch):
    path = _write(tmp_path / "t.jsonl", [_line("user", "go"), _turn(PREV)])
    assert transcript.previous_assistant_turn(path) == PREV
    assert transcript.previous_assistant_turn(path, max_seconds=-1) is None                 # the deadline has passed
    monkeypatch.setattr(transcript, "MAX_SECONDS", -1.0)
    d = memory.observe(kg, NEVER, SESSION, transcript_path=path)
    assert (d["version"], d["context"]) == (mg.VERSION, "missing")


# ── the tool ─────────────────────────────────────────────────────────────────

def test_the_observe_tool_takes_an_optional_previous_turn_and_reports_the_version(kg, jev):
    from harness.contract import BY_NAME, invoke

    props = BY_NAME["observe"].input_model.model_json_schema()["properties"]
    assert "previous_turn" in props and "previous_turn" not in BY_NAME["observe"].input_model.model_json_schema().get("required", [])
    out = invoke("observe", kg, {"text": NEVER, "previous_turn": PREV})
    assert out["error"] is None and (out["version"], out["context"]) == (mg.VERSION_V2, "used")
    alone = invoke("observe", kg, {"text": NEVER})
    assert (alone["version"], alone["context"]) == (mg.VERSION, "missing")
    plain = invoke("observe", kg, {"text": "thanks!"})
    assert (plain["version"], plain["context"]) == (None, "none")
    assert "previous_turn" in BY_NAME["observe"].description
