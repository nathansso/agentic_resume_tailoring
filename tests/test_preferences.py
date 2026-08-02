"""Preference capture, compile, and supersession (issue #129).

The extraction half of the preference tier: chat transcript -> typed
preferences -> a persisted, correctable, never-deleted profile. Arbitration
against the JD lives in `test_arbitration.py`; the planner gate and the
end-to-end pipeline wiring live in `test_preference_gate.py`.
"""
from uuid import UUID, uuid4

import pytest
from sqlmodel import Session, select

import services
from agents.extraction_schemas import PreferenceNote, PreferenceNoteList
from agents.preferences import (
    EXTRACTION_VERSION, STATUS_ACTIVE, STATUS_RETRACTED, STATUS_SUPERSEDED,
    build_transcript, compile_preferences, extract_preference_notes,
    preferences_in_scope, resolve_against_existing,
)
from database.models import User, UserPreference
from tests.conftest import _seed_user_and_skill  # noqa: F401


CATALOG = [
    {"key": "proj:recipe app", "label": "project: Recipe App", "target_type": "project"},
    {"key": "exp:barista|coffee co", "label": "experience: Barista @ Coffee Co",
     "target_type": "experience"},
    {"key": "skill:machine learning", "label": "skill: Machine Learning",
     "target_type": "skill"},
    {"key": "section:projects", "label": "section: projects", "target_type": "section"},
]


class _StubExtractor:
    """Stands in for the #142 get_extractor seam."""

    def __init__(self, notes):
        self._notes = notes
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return PreferenceNoteList(notes=self._notes)


def _note(**kwargs):
    base = {
        "text": "Do not lead with the Recipe App, it was coursework.",
        "polarity": "suppress",
        "target_type": "project",
        "target_label": "project: Recipe App",
        "scope_type": "global",
        "strength": 3,
        "evidence": "that one was just a class project",
        "confidence": 0.8,
    }
    base.update(kwargs)
    return PreferenceNote(**base)


# ── transcript ───────────────────────────────────────────────────────────────

def test_the_transcript_keeps_both_roles_and_drops_system_turns():
    transcript = build_transcript([
        {"role": "user", "content": "skip the recipe app"},
        {"role": "system", "content": "internal"},
        {"role": "assistant", "content": "noted"},
    ])
    assert "User: skip the recipe app" in transcript
    assert "Assistant: noted" in transcript
    assert "internal" not in transcript


# ── extraction ───────────────────────────────────────────────────────────────

def test_extraction_returns_typed_notes():
    stub = _StubExtractor([_note()])
    notes = extract_preference_notes("User: that was just a class project",
                                     CATALOG, extractor=stub)
    assert stub.calls == 1
    assert notes[0]["polarity"] == "suppress"
    assert notes[0]["evidence"] == "that one was just a class project"


def test_a_note_without_evidence_is_dropped():
    """Same rule #21 applies to knowledge artifacts, and it binds harder here:
    an ungrounded preference *removes* resume content when honored."""
    stub = _StubExtractor([_note(evidence=None), _note()])
    assert len(extract_preference_notes("x", CATALOG, extractor=stub)) == 1


def test_an_extraction_failure_returns_no_notes():
    class _Boom:
        def invoke(self, _messages):
            raise RuntimeError("provider down")

    assert extract_preference_notes("x", CATALOG, extractor=_Boom()) == []


def test_an_empty_transcript_never_reaches_the_model():
    stub = _StubExtractor([_note()])
    assert extract_preference_notes("   ", CATALOG, extractor=stub) == []
    assert stub.calls == 0


# ── compile ──────────────────────────────────────────────────────────────────

def test_compile_binds_a_target_to_its_knowledge_graph_key():
    compiled = compile_preferences([_note().model_dump()], CATALOG)
    assert compiled[0]["target_key"] == "proj:recipe app"
    assert compiled[0]["target_type"] == "project"
    assert compiled[0]["extraction_version"] == EXTRACTION_VERSION


def test_a_loosely_named_target_still_binds():
    compiled = compile_preferences(
        [_note(target_label="the recipe app").model_dump()], CATALOG)
    assert compiled[0]["target_key"] == "proj:recipe app"


def test_an_unresolvable_target_is_kept_as_a_topic():
    """'Never mention my current employer' is a real preference about something
    deliberately not on the resume. Dropping it would lose the user's clearest
    instructions."""
    compiled = compile_preferences(
        [_note(target_label="my current employer", target_type="topic").model_dump()],
        CATALOG)
    assert compiled[0]["target_key"] is None
    assert compiled[0]["target_term"] == "my current employer"


def test_strength_is_clamped_not_rejected():
    compiled = compile_preferences([_note(strength=99).model_dump()], CATALOG)
    assert compiled[0]["strength"] == 5


def test_a_role_family_scope_with_no_family_narrows_to_the_job():
    """It cannot be filtered against a future job, so left alone it would behave
    as global — the one direction a scope must never drift."""
    compiled = compile_preferences(
        [_note(scope_type="role_family", scope_value=None).model_dump()],
        CATALOG, provenance={"job_id": "job-1"})
    assert compiled[0]["scope_type"] == "job"
    assert compiled[0]["scope_value"] == "job-1"


def test_a_job_scope_takes_the_job_from_provenance():
    compiled = compile_preferences(
        [_note(scope_type="job").model_dump()], CATALOG,
        provenance={"job_id": "job-7"})
    assert compiled[0]["scope_value"] == "job-7"


def test_the_evidence_quote_is_carried_into_provenance():
    compiled = compile_preferences([_note().model_dump()], CATALOG,
                                   provenance={"job_id": "job-1"})
    assert compiled[0]["provenance"]["quote"] == "that one was just a class project"
    assert compiled[0]["provenance"]["job_id"] == "job-1"


def test_the_same_preference_said_twice_compiles_once():
    compiled = compile_preferences(
        [_note().model_dump(), _note(text="Skip the recipe app.").model_dump()],
        CATALOG)
    assert len(compiled) == 1


def test_compile_is_deterministic():
    notes = [_note().model_dump()]
    provenance = {"job_id": "j", "extracted_at": "2026-08-02T00:00:00"}
    assert (compile_preferences(notes, CATALOG, provenance)
            == compile_preferences(notes, CATALOG, provenance))


# ── supersession ─────────────────────────────────────────────────────────────

def _existing(**kwargs):
    base = {
        "preference_id": "old-1",
        "text": "Do not lead with the Recipe App.",
        "polarity": "suppress",
        "target_key": "proj:recipe app",
        "target_term": "project: Recipe App",
        "scope_type": "global",
        "scope_value": None,
        "status": STATUS_ACTIVE,
    }
    base.update(kwargs)
    return base


def test_the_same_preference_again_is_a_no_op():
    resolved = resolve_against_existing(
        compile_preferences([_note().model_dump()], CATALOG), [_existing()])
    assert resolved[0]["decision"] == "no_op"


def test_a_reversal_supersedes_the_earlier_preference():
    """'Actually, put it back' — the opposed case the whole tier exists for."""
    reversal = _note(polarity="emphasize", text="Lead with the Recipe App.")
    resolved = resolve_against_existing(
        compile_preferences([reversal.model_dump()], CATALOG), [_existing()])
    assert resolved[0]["decision"] == "supersede"
    assert resolved[0]["supersedes_id"] == "old-1"


def test_a_preference_about_something_else_is_an_add():
    other = _note(target_label="skill: Machine Learning", target_type="skill")
    resolved = resolve_against_existing(
        compile_preferences([other.model_dump()], CATALOG), [_existing()])
    assert resolved[0]["decision"] == "add"


def test_a_different_scope_does_not_supersede():
    """A job-scoped reversal must not silently cancel a global rule."""
    reversal = _note(polarity="emphasize", scope_type="job")
    resolved = resolve_against_existing(
        compile_preferences([reversal.model_dump()], CATALOG,
                            provenance={"job_id": "job-1"}),
        [_existing()])
    assert resolved[0]["decision"] == "add"


def test_an_already_superseded_preference_is_not_matched_against():
    resolved = resolve_against_existing(
        compile_preferences([_note().model_dump()], CATALOG),
        [_existing(status=STATUS_SUPERSEDED)])
    assert resolved[0]["decision"] == "add"


# ── scope filter ─────────────────────────────────────────────────────────────

def _row(**kwargs):
    base = {
        "preference_id": "p1", "status": STATUS_ACTIVE,
        "scope_type": "global", "scope_value": None,
    }
    base.update(kwargs)
    return base


def test_a_global_preference_binds_on_every_job():
    assert len(preferences_in_scope([_row()], job_id="any", role_family="research")) == 1


def test_a_job_preference_binds_only_on_its_own_job():
    rows = [_row(scope_type="job", scope_value="job-1")]
    assert len(preferences_in_scope(rows, job_id="job-1")) == 1
    assert preferences_in_scope(rows, job_id="job-2") == []


def test_a_role_family_preference_binds_across_jobs_in_that_family():
    rows = [_row(scope_type="role_family", scope_value="machine_learning")]
    assert len(preferences_in_scope(
        rows, job_id="job-9", role_family="machine_learning")) == 1
    assert preferences_in_scope(rows, job_id="job-9", role_family="design") == []


def test_inactive_preferences_never_bind():
    for status in (STATUS_SUPERSEDED, STATUS_RETRACTED):
        assert preferences_in_scope([_row(status=status)], job_id="j") == []


# ── persistence ──────────────────────────────────────────────────────────────

def _user(engine) -> User:
    with Session(engine) as session:
        user = User(name="Pref", email=f"pref-{uuid4()}@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def test_an_accepted_proposal_persists_and_survives_a_reload(isolated_engine):
    user = _user(isolated_engine)
    proposal = compile_preferences([_note().model_dump()], CATALOG)[0]
    message = services.apply_preference_decision(user.user_id, proposal)
    assert "Saved" in message

    loaded = services.load_preferences(user.user_id)
    assert len(loaded) == 1
    assert loaded[0]["target_key"] == "proj:recipe app"
    assert loaded[0]["status"] == STATUS_ACTIVE


def test_a_no_op_proposal_writes_nothing(isolated_engine):
    user = _user(isolated_engine)
    proposal = {**compile_preferences([_note().model_dump()], CATALOG)[0],
                "decision": "no_op"}
    services.apply_preference_decision(user.user_id, proposal)
    assert services.load_preferences(user.user_id) == []


def test_superseding_keeps_the_old_row(isolated_engine):
    """Non-deletion is the invariant: a contradicted preference is superseded,
    never removed, because the transition is #51 Phase 2's training signal."""
    user = _user(isolated_engine)
    first = compile_preferences([_note().model_dump()], CATALOG)[0]
    services.apply_preference_decision(user.user_id, first)
    old_id = services.load_preferences(user.user_id)[0]["preference_id"]

    reversal = compile_preferences(
        [_note(polarity="emphasize", text="Lead with the Recipe App.").model_dump()],
        CATALOG)[0]
    reversal["supersedes_id"] = old_id
    services.apply_preference_decision(user.user_id, reversal)

    everything = services.load_preferences(user.user_id, include_inactive=True)
    assert len(everything) == 2
    by_id = {p["preference_id"]: p for p in everything}
    assert by_id[old_id]["status"] == STATUS_SUPERSEDED
    assert services.load_preferences(user.user_id)[0]["polarity"] == "emphasize"


def test_superseding_another_users_preference_is_refused(isolated_engine):
    """The proposal is client-held round-tripped state, so its ids are untrusted."""
    victim, attacker = _user(isolated_engine), _user(isolated_engine)
    services.apply_preference_decision(
        victim.user_id, compile_preferences([_note().model_dump()], CATALOG)[0])
    victim_id = services.load_preferences(victim.user_id)[0]["preference_id"]

    proposal = compile_preferences([_note(polarity="emphasize").model_dump()], CATALOG)[0]
    proposal["supersedes_id"] = victim_id
    services.apply_preference_decision(attacker.user_id, proposal)

    assert services.load_preferences(victim.user_id)[0]["status"] == STATUS_ACTIVE


def test_editing_stamps_the_correction_flag(isolated_engine):
    user = _user(isolated_engine)
    services.apply_preference_decision(
        user.user_id, compile_preferences([_note().model_dump()], CATALOG)[0])
    pid = services.load_preferences(user.user_id)[0]["preference_id"]

    updated = services.update_preference(
        user.user_id, UUID(pid), {"strength": 5, "polarity": "emphasize"})
    assert updated["strength"] == 5
    assert updated["polarity"] == "emphasize"
    assert updated["edited"] is True


def test_an_unknown_edit_field_is_ignored_not_rejected(isolated_engine):
    user = _user(isolated_engine)
    services.apply_preference_decision(
        user.user_id, compile_preferences([_note().model_dump()], CATALOG)[0])
    pid = services.load_preferences(user.user_id)[0]["preference_id"]
    updated = services.update_preference(
        user.user_id, UUID(pid),
        {"target_key": "proj:evil", "strength": 4})
    assert updated["strength"] == 4
    assert updated["target_key"] == "proj:recipe app"


def test_retracting_keeps_the_row(isolated_engine):
    user = _user(isolated_engine)
    services.apply_preference_decision(
        user.user_id, compile_preferences([_note().model_dump()], CATALOG)[0])
    pid = services.load_preferences(user.user_id)[0]["preference_id"]

    services.retract_preference(user.user_id, UUID(pid))
    assert services.load_preferences(user.user_id) == []
    kept = services.load_preferences(user.user_id, include_inactive=True)
    assert kept[0]["status"] == STATUS_RETRACTED
    with Session(isolated_engine) as session:
        assert session.exec(select(UserPreference)).all()


def test_editing_another_users_preference_is_refused(isolated_engine):
    victim, attacker = _user(isolated_engine), _user(isolated_engine)
    services.apply_preference_decision(
        victim.user_id, compile_preferences([_note().model_dump()], CATALOG)[0])
    pid = services.load_preferences(victim.user_id)[0]["preference_id"]

    assert services.update_preference(
        attacker.user_id, UUID(pid), {"strength": 1}) is None
    assert services.retract_preference(
        attacker.user_id, UUID(pid)) is None
