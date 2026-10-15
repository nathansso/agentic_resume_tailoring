"""Skill areas: the keyword table first, then cached Jev for what it cannot place (issue #318).

No test here reaches TypeSafe. A fake transport answers when a test turns Jev on, and
`get_client` is replaced with a tripwire wherever a path must stay offline.
"""
import re
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import database.db as db
from database.models import JevDecision, Skill, User, UserSkill
from harness.decisions import engine, skill_area as jev_area
from harness.decisions.client import DEFAULT_MODEL, JevClient
from knowledge_graph import areas
from knowledge_graph.areas import AREAS, OTHER, skill_area, skill_areas
from knowledge_graph.evidence_view import build_evidence_view

ANSWERS = {"Figma": "Tools", "Mailchimp": "Tools", "Prompt engineering": "ML & AI",
           "Hobby knitting": "Other"}


class Transport:
    """Answers a skill_area question from `ANSWERS`, by the skill named in its instructions."""

    def __init__(self, script=None):
        self.calls, self.script = [], list(script or [])

    def __call__(self, url, headers, payload, timeout):
        self.calls.append(payload)
        if self.script:
            return self.script.pop(0)
        answers = {}
        for qid, wire in payload["questions"].items():
            skill = re.search(r'the skill "([^"]+)"', wire["instructions"]).group(1)
            pick = ANSWERS.get(skill, "Other")
            answers[qid] = {"type": "choice", "choice": pick, "confidence": 0.9,
                            "probabilities": {pick: 0.9}}
        return 200, {"model": DEFAULT_MODEL, "answers": answers, "usage": {}}

    @property
    def asked(self):
        return sorted(re.search(r'the skill "([^"]+)"', w["instructions"]).group(1)
                      for c in self.calls for w in c["questions"].values())


@pytest.fixture()
def jev(isolated_engine, monkeypatch):
    """Jev on (mode auto) with a fake transport behind the client."""
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    transport = Transport()
    client = JevClient("test-key-not-real", transport=transport, sleep=lambda s: None)
    monkeypatch.setattr(engine, "get_client", lambda: client)
    engine.reset_stats()
    return transport


def forbid_live(monkeypatch):
    """From here on any attempt to build a live client fails the test."""
    def boom():
        raise AssertionError("a live Jev client was requested")
    monkeypatch.setattr(engine, "get_client", boom)


def make_user(engine_):
    with Session(engine_) as s:
        u = User(name="Areas", email=f"areas_{uuid4().hex[:6]}@example.com")
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.user_id


def link_skill(engine_, uid, name, category=None):
    with Session(engine_) as s:
        sk = Skill(name=name, category=category)
        s.add(sk)
        s.commit()
        s.refresh(sk)
        s.add(UserSkill(user_id=uid, skill_id=sk.skill_id, proficiency=3,
                        evidence_source="resume", confidence_score=0.9))
        s.commit()


def ingest(uid, items):
    from harness.ingest import _Store
    return _Store(uid)._save_skills(items, "resume")


def decision_rows():
    with Session(db.engine) as s:
        return s.exec(select(JevDecision)).all()


# ── the keyword path ─────────────────────────────────────────────────────────

def test_the_keyword_table_places_skills_without_any_jev_work(jev, monkeypatch):
    forbid_live(monkeypatch)
    pairs = [("Python", "Programming Language"), ("PyTorch", None), ("Docker", "Tools"),
             ("Some Obscure Thing", "Cloud")]
    got = skill_areas(pairs)
    assert got == {pairs[0]: "Languages", pairs[1]: "ML & AI", pairs[2]: "Tools",
                   pairs[3]: "Infrastructure & Cloud"}
    assert jev.calls == [] and decision_rows() == []


def test_the_area_descriptions_cover_every_area_but_other():
    assert tuple(areas.AREA_DESCRIPTIONS) == AREAS[:-1]
    q = jev_area.question_for("Figma", "Design")
    assert set(q.options) == set(AREAS) and q.no_match == OTHER and q.options[OTHER] is None


def test_the_question_carries_the_skill_and_leaves_out_a_useless_category():
    assert 'the skill "Figma"' in jev_area.question_for("  Figma ", None).instructions
    assert "lists it under" not in jev_area.question_for("Figma", "Other").instructions
    assert "lists it under" not in jev_area.question_for("Figma", "").instructions
    assert 'under "Design"' in jev_area.question_for("Figma", "Design").instructions
    assert (jev_area.question_for("Figma", "Design").canonical()
            != jev_area.question_for("Figma", "UI").canonical())


# ── the write path ───────────────────────────────────────────────────────────

def test_ingest_places_an_unplaced_skill_with_one_request_and_the_read_path_finds_it(jev, monkeypatch):
    uid = make_user(db.engine)
    ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3},
                 {"name": "Mailchimp", "category": "Marketing", "proficiency": 2},
                 {"name": "Python", "category": "Language", "proficiency": 5}])
    assert len(jev.calls) == 1 and jev.asked == ["Figma", "Mailchimp"]   # Python is the table's
    assert len(decision_rows()) == 2

    forbid_live(monkeypatch)
    monkeypatch.setenv("ART_JEV_MODE", "off")            # reads use the cache in any mode
    assert skill_area("Figma", "Design") == "Tools"
    view = build_evidence_view(uid)
    assert {s["name"]: s["area"] for s in view["skills"]} == {
        "Figma": "Tools", "Mailchimp": "Tools", "Python": "Languages"}
    assert len(jev.calls) == 1


def test_a_second_ingest_of_the_same_skills_makes_no_new_call(jev):
    uid = make_user(db.engine)
    items = [{"name": "Figma", "category": "Design", "proficiency": 3}]
    ingest(uid, items)
    ingest(uid, items)
    assert len(jev.calls) == 1


def test_a_read_that_misses_is_other_and_never_goes_to_the_network(jev, monkeypatch):
    forbid_live(monkeypatch)
    assert skill_area("Figma", "Design") == OTHER
    assert skill_areas([("Figma", "Design"), ("Mailchimp", None)]) == {
        ("Figma", "Design"): OTHER, ("Mailchimp", None): OTHER}
    assert jev.calls == []


def test_ingest_succeeds_and_the_area_stays_other_with_no_key(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)
    uid = make_user(db.engine)
    result = ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3}])
    assert [status for status, _ in result] == ["created"]
    assert skill_area("Figma", "Design") == OTHER and decision_rows() == []


def test_ingest_succeeds_and_the_area_stays_other_when_jev_errors(jev):
    jev.script = [(401, {"message": "bad key", "error_type": "auth"})]
    uid = make_user(db.engine)
    result = ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3}])
    assert [status for status, _ in result] == ["created"]
    assert skill_area("Figma", "Design") == OTHER and decision_rows() == []
    with Session(db.engine) as s:
        assert s.exec(select(Skill).where(Skill.name == "Figma")).first() is not None


def test_ingest_succeeds_when_the_classification_itself_blows_up(jev, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("jev module broke")
    monkeypatch.setattr(jev_area, "classify", boom)
    uid = make_user(db.engine)
    result = ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3}])
    assert [status for status, _ in result] == ["created"]


def test_jev_off_in_the_suite_default_asks_nothing(isolated_engine):
    uid = make_user(db.engine)                            # conftest: ART_JEV_MODE=off
    ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3}])
    assert decision_rows() == [] and skill_area("Figma", "Design") == OTHER


def test_a_jev_answer_of_other_is_cached_as_other(jev):
    uid = make_user(db.engine)
    ingest(uid, [{"name": "Hobby knitting", "category": None, "proficiency": 1}])
    assert skill_area("Hobby knitting", None) == OTHER
    ingest(uid, [{"name": "Hobby knitting", "category": None, "proficiency": 1}])
    assert len(jev.calls) == 1


def test_a_new_question_version_invalidates_the_cached_area(jev, monkeypatch):
    uid = make_user(db.engine)
    ingest(uid, [{"name": "Figma", "category": "Design", "proficiency": 3}])
    assert skill_area("Figma", "Design") == "Tools"
    monkeypatch.setattr(jev_area, "VERSION", "skill_area@v2")
    assert skill_area("Figma", "Design") == OTHER


# ── the backfill ─────────────────────────────────────────────────────────────

def test_the_backfill_classifies_only_unplaced_skills_and_only_once(jev, monkeypatch):
    uid = make_user(db.engine)
    other = make_user(db.engine)
    for name, cat in (("Python", "Language"), ("Figma", "Design"), ("Prompt engineering", None),
                      ("Hobby knitting", None)):
        link_skill(db.engine, uid, name, cat)
    link_skill(db.engine, other, "Mailchimp", "Marketing")          # another user's: not asked

    dry = areas.classify_unplaced_skills(uid, dry_run=True)
    assert dry == {"skills": 4, "unplaced": 3, "classified": 0, "asked": 0, "fallback": 0}
    assert jev.calls == []

    stats = areas.classify_unplaced_skills(uid)
    assert stats == {"skills": 4, "unplaced": 3, "classified": 2, "asked": 3, "fallback": 0}
    assert len(jev.calls) == 1 and jev.asked == ["Figma", "Hobby knitting", "Prompt engineering"]

    again = areas.classify_unplaced_skills(uid)
    assert again["asked"] == 0 and again["classified"] == 2 and len(jev.calls) == 1

    forbid_live(monkeypatch)
    assert skill_area("Prompt engineering", None) == "ML & AI"


def test_the_backfill_with_no_key_leaves_skills_unplaced(isolated_engine, monkeypatch):
    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", lambda: None)
    uid = make_user(db.engine)
    link_skill(db.engine, uid, "Figma", "Design")
    assert areas.classify_unplaced_skills(uid) == {
        "skills": 1, "unplaced": 1, "classified": 0, "asked": 0, "fallback": 1}


# ── the cache-only lookup ────────────────────────────────────────────────────

def test_lookup_reads_the_cache_in_any_mode_and_never_calls(jev, monkeypatch):
    q = jev_area.question_for("Figma", "Design")
    assert engine.lookup(jev_area.POINT, jev_area.STATE, [q]) == [None]
    engine.decide(jev_area.POINT, jev_area.STATE, [q])
    monkeypatch.setenv("ART_JEV_MODE", "off")
    forbid_live(monkeypatch)
    (answer,) = engine.lookup(jev_area.POINT, jev_area.STATE, [q])
    assert answer.value == "Tools" and answer.source == "cache"
    assert len(jev.calls) == 1


def test_lookup_rejects_a_question_of_another_point(isolated_engine):
    from harness.decisions.questions import Choice
    with pytest.raises(ValueError):
        engine.lookup(jev_area.POINT, {}, [Choice("other@v1", "x", {"a": None, "b": None})])
