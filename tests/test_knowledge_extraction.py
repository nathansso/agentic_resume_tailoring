"""Chain-of-Note chat extraction tests (issue #21).

Covers the `extract → reason → decide` pipeline in
`agents/knowledge_extractor.py`, the supersede persistence path in
`services.apply_artifact_decision`, and the invariant that knowledge-graph rows
created from a chat outlive the job and chat they came from.

Every test runs offline: the extractors are scripted stand-ins for the #142
`get_extractor` seam (the same shape `tests/test_extraction_seam.py` uses), so
nothing here touches a provider.
"""
import pytest
from sqlmodel import Session, select

import agents.knowledge_extractor as ke
import services as services_module
from agents.extraction_schemas import ArtifactDecisionList, ChatArtifactNoteList
from database.models import (
    ChatMessage, Experience, JobDescription, Project, Skill, UserSkill,
)

from conftest import _seed_user_and_skill


class _Scripted:
    """A StructuredExtractor stand-in returning a fixed validated model."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def invoke(self, _messages):
        self.calls += 1
        return self.payload


class _Failing:
    def invoke(self, _messages):
        raise RuntimeError("provider is down")


def _notes(*items):
    return _Scripted(ChatArtifactNoteList(notes=list(items)))


def _decisions(*items):
    return _Scripted(ArtifactDecisionList(decisions=list(items)))


# ── extract ───────────────────────────────────────────────────────────────────

def test_extract_notes_returns_grounded_notes():
    extractor = _notes(
        {"type": "skill", "name": "Redis", "category": "Database",
         "evidence": "I built a distributed cache using Redis"},
    )
    notes = ke.extract_notes("User: I built a distributed cache using Redis",
                             [], extractor=extractor)
    assert len(notes) == 1
    assert notes[0]["name"] == "Redis" and notes[0]["type"] == "skill"


@pytest.mark.parametrize("item, why", [
    ({"type": "skill", "name": "Kafka"}, "no evidence quote"),
    ({"type": "skill", "name": "Kafka", "evidence": "  "}, "blank evidence"),
    ({"type": "hobby", "name": "Chess", "evidence": "I play chess"}, "unknown type"),
    ({"type": "skill", "name": "", "evidence": "something"}, "no name"),
])
def test_extract_notes_drops_ungrounded_notes(item, why):
    """A note that can't be traced to something the user said is not offerable."""
    notes = ke.extract_notes("User: hi", [], extractor=_notes(item))
    assert notes == [], f"should have dropped the note ({why})"


def test_extract_notes_returns_empty_on_extractor_failure():
    assert ke.extract_notes("User: hi", [], extractor=_Failing()) == []


def test_extract_notes_skips_llm_on_empty_transcript():
    extractor = _notes({"type": "skill", "name": "X", "evidence": "y"})
    assert ke.extract_notes("   ", [], extractor=extractor) == []
    assert extractor.calls == 0, "an empty transcript must not reach the model"


# ── reason ────────────────────────────────────────────────────────────────────

def test_reason_decisions_keys_by_note_index():
    notes = [{"type": "skill", "name": "Redis", "evidence": "q"}]
    out = ke.reason_decisions(
        notes, [], extractor=_decisions({"note_index": 0, "decision": "add"}))
    assert out[0]["decision"] == "add"


def test_reason_decisions_drops_out_of_range_indices():
    notes = [{"type": "skill", "name": "Redis", "evidence": "q"}]
    out = ke.reason_decisions(
        notes, [], extractor=_decisions({"note_index": 7, "decision": "add"}))
    assert out == {}


def test_reason_decisions_returns_empty_on_failure():
    notes = [{"type": "skill", "name": "Redis", "evidence": "q"}]
    assert ke.reason_decisions(notes, [], extractor=_Failing()) == {}


# ── decide (deterministic layer) ──────────────────────────────────────────────

_KNOWN = [
    {"type": "skill", "name": "Python", "category": "Language", "proficiency": 3,
     "label": "skill: Python"},
    {"type": "experience", "name": "Senior Engineer", "company": "Acme",
     "label": "experience: Senior Engineer @ Acme"},
]


def test_decide_add_for_unknown_artifact():
    notes = [{"type": "skill", "name": "Redis", "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "add"}}, _KNOWN)
    assert out[0]["decision"] == "add" and out[0]["target"] is None


def test_decide_forces_no_op_when_llm_says_add_to_a_known_fact():
    """The duplicate guard: an exact match that changes nothing is never an add."""
    notes = [{"type": "skill", "name": "python", "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "add"}}, _KNOWN)
    assert out[0]["decision"] == "no_op"
    assert out[0]["target"] == "skill: Python"


def test_decide_upgrades_add_to_supersede_when_the_match_changes():
    notes = [{"type": "skill", "name": "Python", "proficiency": 5, "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "add"}}, _KNOWN)
    assert out[0]["decision"] == "supersede"


def test_decide_honors_supersede_against_a_renamed_fact():
    """The promotion case: the new title can't match by equality, so the model's
    resolvable target is what carries the decision."""
    notes = [{"type": "experience", "name": "Staff Engineer", "company": "Acme",
              "evidence": "I got promoted to Staff Engineer"}]
    out = ke.decide(
        notes,
        {0: {"decision": "supersede", "target": "experience: Senior Engineer @ Acme"}},
        _KNOWN,
    )
    assert out[0]["decision"] == "supersede"
    assert out[0]["target"] == "experience: Senior Engineer @ Acme"


def test_decide_falls_back_to_add_when_the_target_does_not_resolve():
    notes = [{"type": "experience", "name": "Staff Engineer", "company": "Globex",
              "evidence": "q"}]
    out = ke.decide(
        notes, {0: {"decision": "supersede", "target": "experience: Nobody @ Nowhere"}},
        _KNOWN,
    )
    assert out[0]["decision"] == "add"


def test_decide_falls_back_to_add_when_no_op_has_nothing_to_match():
    notes = [{"type": "skill", "name": "Rust", "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "no_op"}}, _KNOWN)
    assert out[0]["decision"] == "add"


def test_decide_normalizes_unrecognized_decision_strings():
    notes = [{"type": "skill", "name": "Rust", "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "CREATE-NEW"}}, _KNOWN)
    assert out[0]["decision"] == "add"


def test_decide_ignores_fields_the_note_omits():
    """A note silent about `category` is not a contradiction of the stored one."""
    notes = [{"type": "skill", "name": "Python", "category": None,
              "proficiency": None, "evidence": "q"}]
    out = ke.decide(notes, {0: {"decision": "supersede"}}, _KNOWN)
    assert out[0]["decision"] == "no_op"


# ── the subgraph end to end ───────────────────────────────────────────────────

def test_run_chain_of_note_through_the_subgraph():
    """extract → reason → decide composed, proving state reaches the last node."""
    proposals = ke.run_chain_of_note(
        [{"role": "user", "content": "I got promoted to Staff Engineer at Acme."}],
        _KNOWN,
        extract_extractor=_notes(
            {"type": "experience", "name": "Staff Engineer", "company": "Acme",
             "evidence": "I got promoted to Staff Engineer at Acme"}),
        reason_extractor=_decisions(
            {"note_index": 0, "decision": "supersede",
             "target": "experience: Senior Engineer @ Acme"}),
    )
    assert len(proposals) == 1
    assert proposals[0]["decision"] == "supersede"
    assert proposals[0]["name"] == "Staff Engineer"


def test_run_chain_of_note_degrades_to_empty_on_extractor_failure():
    assert ke.run_chain_of_note(
        [{"role": "user", "content": "hi"}], [],
        extract_extractor=_Failing(), reason_extractor=_Failing(),
    ) == []


def test_run_chain_of_note_survives_a_missing_reason_step():
    """A reasoning outage still yields proposals — decide() judges on its own."""
    proposals = ke.run_chain_of_note(
        [{"role": "user", "content": "I use Redis."}], _KNOWN,
        extract_extractor=_notes(
            {"type": "skill", "name": "Redis", "evidence": "I use Redis"}),
        reason_extractor=_Failing(),
    )
    assert [p["decision"] for p in proposals] == ["add"]


def test_build_transcript_keeps_roles_and_drops_system_turns():
    transcript = ke.build_transcript([
        {"role": "user", "content": "hello"},
        {"role": "system", "content": "ignore me"},
        {"role": "assistant", "content": "hi"},
    ])
    assert "User: hello" in transcript and "Assistant: hi" in transcript
    assert "ignore me" not in transcript


def test_load_known_facts_labels_every_artifact_type(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        session.add(Project(user_id=user.user_id, name="StreamBoard"))
        session.add(Experience(user_id=user.user_id, title="Engineer", company="Acme"))
        session.commit()

    labels = {f["label"] for f in ke.load_known_facts(user.user_id)}
    assert labels == {"skill: Python", "project: StreamBoard",
                      "experience: Engineer @ Acme"}


# ── persistence: apply_artifact_decision ──────────────────────────────────────

def test_apply_decision_add_creates_the_row(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "skill", "name": "Redis", "category": "Database",
        "decision": "add", "evidence": "I built a cache with Redis",
    })
    assert "added" in message.lower()
    with Session(isolated_engine) as session:
        assert session.exec(select(Skill).where(Skill.name == "Redis")).first()


def test_apply_decision_no_op_writes_nothing(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "skill", "name": "Python", "decision": "no_op",
        "evidence": "I use Python",
    })
    assert "already" in message.lower()
    with Session(isolated_engine) as session:
        links = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)).all()
    assert len(links) == 1, "no_op must not add a second link"


def test_apply_decision_supersedes_skill_proficiency_in_place(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)  # seeds Python at proficiency 5
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "skill", "name": "Python", "category": "Language", "proficiency": 2,
        "decision": "supersede", "target": "skill: Python",
        "evidence": "Honestly I'm rusty at Python these days",
    }, source_context="chat:landing")
    assert "updated" in message.lower()

    with Session(isolated_engine) as session:
        links = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)).all()
        assert len(links) == 1, "supersede must update in place, not duplicate"
        assert links[0].proficiency == 2
        assert links[0].source_context == "chat:landing"


def test_apply_decision_supersedes_experience_title_on_promotion(isolated_engine):
    """The LongMemEval case: a later turn revises the role at the same employer."""
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        session.add(Experience(
            user_id=user.user_id, title="Senior Engineer", company="Acme"))
        session.commit()

    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "experience", "name": "Staff Engineer", "company": "Acme",
        "decision": "supersede", "target": "experience: Senior Engineer @ Acme",
        "evidence": "I got promoted to Staff Engineer at Acme last month",
    }, source_context="chat:landing")
    assert "updated" in message.lower()

    with Session(isolated_engine) as session:
        rows = session.exec(
            select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(rows) == 1, "the promotion must move the row, not add a second one"
    assert rows[0].title == "Staff Engineer"
    assert rows[0].source_context == "chat:landing"


def test_apply_decision_does_not_guess_between_two_roles_at_one_employer(
        isolated_engine):
    """With no resolvable target and two roles at the employer, adding beats
    overwriting the wrong one."""
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        session.add(Experience(user_id=user.user_id, title="Intern", company="Acme"))
        session.add(Experience(user_id=user.user_id, title="Engineer", company="Acme"))
        session.commit()

    services_module.apply_artifact_decision(user.user_id, {
        "type": "experience", "name": "Staff Engineer", "company": "Acme",
        "decision": "supersede", "target": "experience: Nobody @ Nowhere",
        "evidence": "I'm a Staff Engineer at Acme now",
    })

    with Session(isolated_engine) as session:
        titles = sorted(e.title for e in session.exec(
            select(Experience).where(Experience.user_id == user.user_id)).all())
    assert titles == ["Engineer", "Intern", "Staff Engineer"], (
        "neither existing role may be silently overwritten")


def test_apply_decision_supersede_keeps_an_existing_description(isolated_engine):
    """An update carrying no description must not clobber a real one with the quote."""
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        session.add(Project(user_id=user.user_id, name="Cache",
                            description="A carefully written blurb."))
        session.commit()

    services_module.apply_artifact_decision(user.user_id, {
        "type": "project", "name": "Cache", "repo_url": "https://example.com/cache",
        "decision": "supersede", "target": "project: Cache",
        "evidence": "the cache repo is at example.com/cache",
    })

    with Session(isolated_engine) as session:
        row = session.exec(select(Project).where(Project.name == "Cache")).first()
    assert row.description == "A carefully written blurb."
    assert row.repo_url == "https://example.com/cache"


def test_apply_decision_supersedes_renamed_project_via_target(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        session.add(Project(user_id=user.user_id, name="Cache Thing",
                            description="old blurb"))
        session.commit()

    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "project", "name": "Distributed Cache",
        "description": "Redis-backed caching layer",
        "decision": "supersede", "target": "project: Cache Thing",
        "evidence": "I renamed it — it's the Distributed Cache project now",
    })
    assert "updated" in message.lower()

    with Session(isolated_engine) as session:
        rows = session.exec(
            select(Project).where(Project.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].name == "Distributed Cache"
    assert rows[0].description == "Redis-backed caching layer"


def test_apply_decision_supersede_with_no_match_creates_instead(isolated_engine):
    """Nothing to update → create it, rather than report a phantom update."""
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "project", "name": "Brand New", "decision": "supersede",
        "target": "project: Does Not Exist", "evidence": "I built Brand New",
    })
    assert "added" in message.lower()
    with Session(isolated_engine) as session:
        assert session.exec(
            select(Project).where(Project.name == "Brand New")).first()


def test_apply_decision_supersede_requires_evidence(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "skill", "name": "Python", "proficiency": 1,
        "decision": "supersede", "target": "skill: Python",
    })
    assert "evidence" in message.lower()
    with Session(isolated_engine) as session:
        link = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)).first()
    assert link.proficiency == 5, "the stored value must be untouched"


def test_apply_decision_rejects_unknown_type(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {
        "type": "hobby", "name": "Chess", "decision": "supersede",
        "target": "hobby: Chess", "evidence": "I play chess",
    })
    assert "unknown artifact type" in message.lower()


def test_apply_decision_never_raises(isolated_engine):
    """Tool-wrapper contract: a malformed proposal returns prose, not a traceback."""
    user = _seed_user_and_skill(isolated_engine)
    message = services_module.apply_artifact_decision(user.user_id, {})
    assert isinstance(message, str) and message


def test_source_context_recorded_on_every_artifact_type(isolated_engine):
    user = _seed_user_and_skill(isolated_engine)
    for atype, data in (
        ("skill", {"name": "Redis", "evidence": "I use Redis"}),
        ("project", {"name": "Cache", "evidence": "I built Cache"}),
        ("experience", {"title": "Engineer", "company": "Acme",
                        "evidence": "I was an Engineer at Acme"}),
    ):
        services_module.create_artifact_from_chat(
            user.user_id, atype, data, source_context="chat:job-42")

    with Session(isolated_engine) as session:
        skill = session.exec(select(Skill).where(Skill.name == "Redis")).first()
        link = session.exec(select(UserSkill).where(
            UserSkill.skill_id == skill.skill_id)).first()
        project = session.exec(select(Project).where(Project.name == "Cache")).first()
        experience = session.exec(
            select(Experience).where(Experience.company == "Acme")).first()

    assert link.source_context == "chat:job-42"
    assert project.source_context == "chat:job-42"
    assert experience.source_context == "chat:job-42"


# ── the non-deletion invariant ────────────────────────────────────────────────

def test_chat_artifacts_survive_job_and_chat_deletion(isolated_engine):
    """Deleting a job and its chat history must not touch the knowledge graph.

    `source_context` is soft metadata, deliberately not a foreign key, so there
    is no cascade to follow. Covers a superseded row as well as added ones —
    superseding rewrites an existing row and must not re-parent it to the job.
    """
    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as session:
        job = JobDescription(title="Temp Job", company="Acme", user_id=user.user_id)
        session.add(job)
        session.add(Experience(
            user_id=user.user_id, title="Senior Engineer", company="Globex"))
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)
        session.add(ChatMessage(
            job_id=job.job_id, user_id=user.user_id, role="user",
            content="I got promoted to Staff Engineer at Globex."))
        session.commit()

    context = f"chat:{job_id}"
    for proposal in (
        {"type": "skill", "name": "ElasticSearch", "category": "Search",
         "decision": "add", "evidence": "We used ElasticSearch for full-text search"},
        {"type": "project", "name": "Search Infra", "decision": "add",
         "evidence": "I built the search infrastructure"},
        {"type": "experience", "name": "Staff Engineer", "company": "Globex",
         "decision": "supersede", "target": "experience: Senior Engineer @ Globex",
         "evidence": "I got promoted to Staff Engineer at Globex"},
    ):
        services_module.apply_artifact_decision(
            user.user_id, proposal, source_context=context)

    assert "deleted" in services_module.delete_job(job_id).lower()

    with Session(isolated_engine) as session:
        assert session.exec(select(JobDescription)).all() == []
        assert session.exec(select(ChatMessage)).all() == [], "chat history is gone"

        skill = session.exec(
            select(Skill).where(Skill.name == "ElasticSearch")).first()
        assert skill is not None, "skill must survive job deletion"
        link = session.exec(select(UserSkill).where(
            UserSkill.user_id == user.user_id,
            UserSkill.skill_id == skill.skill_id)).first()
        assert link is not None and link.source_context == context, (
            "the back-reference survives as dangling soft metadata, by design")

        assert session.exec(
            select(Project).where(Project.name == "Search Infra")).first() is not None

        experiences = session.exec(select(Experience).where(
            Experience.user_id == user.user_id)).all()
        assert len(experiences) == 1
        assert experiences[0].title == "Staff Engineer", (
            "the superseded row must survive with its update intact")


# ── the extraction path stays on the #142 seam ────────────────────────────────

def test_extraction_module_does_not_hand_roll_json_parsing():
    """#142's contract: extraction goes through get_extractor, not JsonOutputParser.

    A regression here would silently take chat extraction off the schema-validated
    (and LangSmith-traced) path, which is exactly why #21 waited on #142.
    """
    import ast
    from pathlib import Path

    # Parsed, not grepped — the module's own docstring names both of the things
    # it is documenting its avoidance of.
    tree = ast.parse(Path(ke.__file__).read_text(encoding="utf-8"))
    called = {
        ast.unparse(node.func)
        for node in ast.walk(tree) if isinstance(node, ast.Call)
    }
    imported = {
        alias.name
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert "json.loads" not in called
    assert "JsonOutputParser" not in imported
    assert "get_extractor" in imported
