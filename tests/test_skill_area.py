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


# ── writers that used to skip the hook (issue #342) ──────────────────────────

def make_job(engine_, uid):
    from database.models import JobDescription
    with Session(engine_) as s:
        job = JobDescription(user_id=uid, title="Designer", company="Acme", description="Uses Figma")
        s.add(job)
        s.commit()
        return job.job_id


def test_the_job_analyzer_places_an_unplaced_job_skill(jev, monkeypatch):
    from agents.job_analyzer import JobAnalyzerAgent

    uid = make_user(db.engine)
    job_id = make_job(db.engine, uid)
    JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "Designer", "company": "Acme"},
        [{"name": "Figma", "category": "Design"}, {"name": "Python", "category": "Language"}],
        "Uses Figma", "paste", job_id=str(job_id))

    assert len(jev.calls) == 1 and jev.asked == ["Figma"]            # Python is the table's
    forbid_live(monkeypatch)
    assert skill_area("Figma", "Design") == "Tools"


def test_a_new_job_from_the_analyzer_places_its_skills_too(jev):
    from agents.job_analyzer import JobAnalyzerAgent

    job = JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "Designer", "company": "Acme"}, [{"name": "Mailchimp", "category": "Marketing"}],
        "Uses Mailchimp", "paste")

    assert job.title == "Designer" and jev.asked == ["Mailchimp"]


def test_the_job_analyzer_still_saves_the_job_when_the_classification_fails(isolated_engine, monkeypatch):
    from agents.job_analyzer import JobAnalyzerAgent

    def boom():
        raise RuntimeError("boom")

    monkeypatch.setenv("ART_JEV_MODE", "auto")
    monkeypatch.setattr(engine, "get_client", boom)
    job = JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "Designer", "company": "Acme"}, [{"name": "Figma", "category": "Design"}], "x", "paste")

    with Session(isolated_engine) as s:
        assert len(s.exec(select(Skill).where(Skill.name == "Figma")).all()) == 1
    assert job.title == "Designer"


def test_the_chat_job_analysis_places_an_unplaced_job_skill(jev, monkeypatch):
    import agents.chat as chat_module
    import agents.job_analyzer as ja_module
    import services

    uid = make_user(db.engine)
    job_id = make_job(db.engine, uid)

    class _FakeAnalyzer:
        def _extract_skills(self, text):
            return [{"name": "Figma", "category": "Design", "required": True, "weight": 0.9},
                    {"name": "Docker", "category": "Tools", "required": False, "weight": 0.4}]

    monkeypatch.setattr(ja_module, "JobAnalyzerAgent", _FakeAnalyzer)
    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: object())
    monkeypatch.setattr(services, "rebuild_jd_profile", lambda *a, **k: None)   # a model call
    agent = chat_module.ChatAgent()
    agent.set_active_job(str(job_id))
    agent._analyze_active_job("")

    assert jev.asked == ["Figma"]                                    # Docker is the table's
    assert skill_area("Figma", "Design") == "Tools"


def test_a_skill_added_in_chat_or_by_hand_is_placed(jev):
    import services

    uid = make_user(db.engine)
    assert "Added skill 'Figma'" in services.create_artifact_from_chat(
        uid, "skill", {"name": "Figma", "category": "Design", "evidence": "I design in Figma daily."})
    assert "Added 'Mailchimp'" in services.add_skill_to_profile(uid, "Mailchimp")

    assert jev.asked == ["Figma", "Mailchimp"]
    assert skill_area("Figma", "Design") == "Tools"


# ── get_job_skills reads the area cache once (issue #342) ────────────────────

class QueryCount:
    """Counts the statements that read the Jev decision cache."""

    def __init__(self, engine_):
        from sqlalchemy import event
        self.n = 0
        self._engine = engine_
        event.listen(engine_, "before_cursor_execute", self._count)

    def _count(self, conn, cursor, statement, *a):
        if "jevdecision" in statement.lower() and statement.lstrip().upper().startswith("SELECT"):
            self.n += 1

    def stop(self):
        from sqlalchemy import event
        event.remove(self._engine, "before_cursor_execute", self._count)


def test_get_job_skills_makes_one_cache_query_for_a_profile_job(jev):
    import services
    from agents.extraction_schemas import JDProfileExtraction, JDRequirementItem
    from agents.jd_payload import compile_profile_payload
    from database.models import JDProfile

    uid = make_user(db.engine)
    job_id = make_job(db.engine, uid)
    items = [JDRequirementItem(text=t, type="required", criticality=3, terms=[t])
             for t in ("Figma", "Mailchimp", "Prompt engineering", "Hobby knitting", "Python")]
    payload = compile_profile_payload("Designer", "", JDProfileExtraction(requirements=items))
    with Session(db.engine) as s:
        s.add(JDProfile(job_id=job_id, user_id=uid, payload=payload))
        s.commit()
    areas.classify_skills_at_write([("Figma", ""), ("Mailchimp", "")])      # asked under the display name

    counter = QueryCount(db.engine)
    try:
        rows = services.get_job_skills(job_id)
    finally:
        counter.stop()

    by_name = {r["name"].lower(): r["area"] for r in rows}      # display names, lower-cased here
    assert counter.n == 1                                   # was one per unplaced requirement
    assert by_name["figma"] == "Tools" and by_name["python"] == "Languages"
    assert by_name["hobby knitting"] == "Other"             # not asked: the read path only reads


def test_get_job_skills_makes_one_cache_query_for_job_skill_rows(jev):
    import services
    from agents.job_analyzer import JobAnalyzerAgent

    uid = make_user(db.engine)
    job_id = make_job(db.engine, uid)
    JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "Designer", "company": "Acme"},
        [{"name": n, "category": c} for n, c in (("Figma", "Design"), ("Mailchimp", "Marketing"),
                                                 ("Hobby knitting", "Craft"), ("Python", "Language"))],
        "x", "paste", job_id=str(job_id))

    counter = QueryCount(db.engine)
    try:
        rows = services.get_job_skills(job_id)
    finally:
        counter.stop()

    assert counter.n == 1
    assert {r["name"]: r["area"] for r in rows} == {
        "Figma": "Tools", "Mailchimp": "Tools", "Hobby knitting": "Other", "Python": "Languages"}


# ── a key configured after the skills were ingested (issue #342) ─────────────

@pytest.fixture()
def keyed(jev, monkeypatch, tmp_path):
    """Jev on with a key in the environment; the path is the data dir that holds the marker."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "key-one")
    return tmp_path


def unplaced_profile():
    uid = make_user(db.engine)
    link_skill(db.engine, uid, "Figma", "Design")
    link_skill(db.engine, uid, "Python", "Language")
    return uid


def test_the_backfill_places_unplaced_skills_when_a_key_is_present(keyed, jev):
    uid = unplaced_profile()
    stats = areas.backfill_unplaced_if_due(uid, today="2026-10-15", data_dir=keyed)

    assert stats["unplaced"] == 1 and stats["classified"] == 1
    assert jev.asked == ["Figma"] and skill_area("Figma", "Design") == "Tools"
    assert (keyed / areas.BACKFILL_MARKER).exists()


def test_the_backfill_runs_at_most_once_a_day_per_key(keyed, jev, monkeypatch):
    uid = unplaced_profile()
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-15", data_dir=keyed) is not None
    link_skill(db.engine, uid, "Mailchimp", "Marketing")        # new unplaced skill, same day, same key
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-15", data_dir=keyed) is None
    assert jev.asked == ["Figma"]

    # A new day, or a different key, is due again.
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-16", data_dir=keyed)["unplaced"] == 2
    link_skill(db.engine, uid, "Hobby knitting", "Craft")
    monkeypatch.setenv("TYPESAFE_API_KEY", "key-two")
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-16", data_dir=keyed) is not None
    marker = (keyed / areas.BACKFILL_MARKER).read_text()
    assert "key-one" not in marker and "key-two" not in marker             # only a hash is kept


def test_the_backfill_does_nothing_without_a_key_with_jev_off_or_nothing_unplaced(keyed, jev, monkeypatch):
    uid = unplaced_profile()
    monkeypatch.delenv("TYPESAFE_API_KEY")
    assert areas.backfill_unplaced_if_due(uid, today="d", data_dir=keyed) is None
    monkeypatch.setenv("TYPESAFE_API_KEY", "key-one")
    monkeypatch.setenv("ART_JEV_MODE", "off")
    assert areas.backfill_unplaced_if_due(uid, today="d", data_dir=keyed) is None
    assert jev.calls == [] and not (keyed / areas.BACKFILL_MARKER).exists()

    monkeypatch.setenv("ART_JEV_MODE", "auto")
    clean = make_user(db.engine)
    link_skill(db.engine, clean, "Python", "Language")
    assert areas.backfill_unplaced_if_due(clean, today="d", data_dir=keyed) is None
    assert jev.calls == []


def test_a_failed_backfill_request_waits_for_tomorrow_and_nothing_raises(keyed, jev):
    uid = unplaced_profile()
    jev.script = [(401, {"message": "bad key"})]
    stats = areas.backfill_unplaced_if_due(uid, today="2026-10-15", data_dir=keyed)
    assert stats["fallback"] == 1 and stats["classified"] == 0
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-15", data_dir=keyed) is None
    assert len(jev.calls) == 1

    # A data dir that cannot be written is swallowed too.
    blocker = keyed / "file"
    blocker.write_text("x")
    assert areas.backfill_unplaced_if_due(uid, today="2026-10-17", data_dir=blocker / "sub") is None


def test_the_background_start_does_its_work_on_a_daemon_thread(keyed, jev):
    uid = unplaced_profile()
    assert areas.start_backfill_in_background(None) is None
    thread = areas.start_backfill_in_background(uid, today="2026-10-15", data_dir=keyed)
    assert thread.daemon
    thread.join(10)
    assert jev.asked == ["Figma"]


def test_art_mcp_and_art_ui_start_the_backfill_only_for_a_writable_local_store(monkeypatch, tmp_path):
    import uvicorn
    import database.db as database_db
    import harness.mcp_server as mcp_server
    import web.app as web_app
    from web import local_ui

    started = []
    monkeypatch.setattr(areas, "start_backfill_in_background", lambda uid, **k: started.append(uid))
    # (writable, database): only a writable local SQLite store is backfilled.
    cases = [(True, "sqlite:///x.db", True), (False, "sqlite:///x.db", False),
             (True, "postgresql://u@h/db", False)]

    class _Server:
        def run(self, transport):
            pass

    monkeypatch.setattr(mcp_server, "build_server", lambda *a, **k: _Server())
    for writes, url, expect in cases:
        started.clear()
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(mcp_server, "bootstrap", lambda *a, w=writes, **k: ("user-1", w))
        mcp_server.main([])
        assert started == (["user-1"] if expect else []), (writes, url)

    (tmp_path / "index.html").write_text("<html></html>", encoding="utf-8")

    class _User:
        user_id, name, email = "user-2", "U", None

    monkeypatch.setattr(database_db, "init_db", lambda: None)
    monkeypatch.setattr(local_ui, "_resolve_user", lambda *a: _User())
    monkeypatch.setattr(web_app, "create_app", lambda: object())
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    for var in ("ART_LOCAL_UI", "ART_LOCAL_UI_READ_ONLY", "ART_LOCAL_UI_USER_ID"):
        monkeypatch.setenv(var, "")
    for writable, url, expect in cases:
        started.clear()
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setattr(local_ui, "_pin_database", lambda *a, w=writable: w)
        assert local_ui.main(["--no-open"], static_dir=tmp_path) == 0
        assert started == (["user-2"] if expect else []), (writable, url)
