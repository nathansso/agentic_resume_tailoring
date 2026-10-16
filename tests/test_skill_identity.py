"""One identity per skill (issue #365).

`Skill` rows are global; two names that `skill_fold` calls one ("PyTorch", "pytorch", "torch";
"Time-Series Analysis", "time series analysis") must be one skill for a user on every path that
links a user or a job to a skill, and a user who already has the duplicates must be able to merge
them. These pin:

- `resolve_skill`: the user's own folded match, else the exact canonical name, else a
  deterministic global folded match;
- every link path joins the existing row: the legacy parser's `_save_skills`, the harness's
  `upsert_items`, `add_skill_to_profile`, the chat artifact, `_supersede_skill`, `set_skill_core`,
  and the job analyzer and chat `JobSkill` paths;
- `_heal_skills` / `scripts/merge_skill_duplicates.py`: one skill per fold, every source kept,
  idempotent, another user's rows and every global `Skill` row untouched, the user's jobs and
  dismissed-skill tombstones repointed;
- the evidence view shows one skill with the union of the evidence afterwards.
"""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import services
from agents.skill_resolver import SkillResolver, merge_user_skill_duplicates, resolve_skill
from database.clock import utc_now
from database.models import DeletedEntry, JobDescription, JobSkill, Skill, User, UserSkill
from harness.contract import invoke
from knowledge_graph.evidence_view import build_evidence_view


def _user(engine, name="Folder"):
    with Session(engine) as s:
        u = User(name=name, email=f"{name.lower()}_{uuid4().hex[:6]}@example.com")
        s.add(u)
        s.commit()
        return u.user_id


def _skill(engine, name, age_days=0):
    """A global Skill row; `age_days` makes it older, so the oldest-wins rule is exercised."""
    with Session(engine) as s:
        sk = Skill(name=name, category="Language", created_at=utc_now() - timedelta(days=age_days))
        s.add(sk)
        s.commit()
        return sk.skill_id


def _link(engine, uid, sid, source="resume.md", **row):
    with Session(engine) as s:
        s.add(UserSkill(user_id=uid, skill_id=sid, evidence_source=source,
                        **{"proficiency": 3, "confidence_score": 0.5, **row}))
        s.commit()


def _user_skill(engine, uid, name, source="resume.md", age_days=0, **row):
    sid = _skill(engine, name, age_days)
    _link(engine, uid, sid, source, **row)
    return sid


def _linked(engine, uid):
    """The distinct Skill names a user is linked to."""
    with Session(engine) as s:
        return sorted({sk.name for sk in s.exec(
            select(Skill).join(UserSkill, UserSkill.skill_id == Skill.skill_id)
            .where(UserSkill.user_id == uid)).all()})


def _all_skill_names(engine):
    with Session(engine) as s:
        return sorted(sk.name for sk in s.exec(select(Skill)).all())


def _store(uid):
    from agents.kg_store import KGStoreMixin

    store = KGStoreMixin()
    store.user = SimpleNamespace(user_id=uid)
    return store


def _job(engine, uid=None):
    with Session(engine) as s:
        job = JobDescription(user_id=uid, title="MLE", company="Acme", description="Uses PyTorch")
        s.add(job)
        s.commit()
        return job.job_id


def _job_skill_names(engine, job_id):
    with Session(engine) as s:
        return sorted(sk.name for sk in s.exec(
            select(Skill).join(JobSkill, JobSkill.skill_id == Skill.skill_id)
            .where(JobSkill.job_id == job_id)).all())


# ── the resolver ──────────────────────────────────────────────────────────────

def test_resolve_prefers_the_users_own_skill_by_fold(isolated_engine):
    uid = _user(isolated_engine)
    mine = _user_skill(isolated_engine, uid, "Time series analysis")
    _skill(isolated_engine, "Time-Series Analysis", age_days=30)       # older, but not the user's

    with Session(isolated_engine) as s:
        assert resolve_skill(s, uid, "Time-Series Analysis").skill_id == mine
        assert resolve_skill(s, uid, "  time  SERIES analysis ").skill_id == mine


def test_resolve_takes_the_exact_canonical_name_before_a_folded_one(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, "Time series analysis", age_days=30)
    exact = _skill(isolated_engine, "Time-Series Analysis")

    with Session(isolated_engine) as s:
        assert resolve_skill(s, uid, "Time-Series Analysis").skill_id == exact


def test_resolve_among_global_folds_prefers_the_alias_canonical_spelling_then_the_oldest(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, "pytorch", age_days=60)                    # oldest, not canonical
    canonical = _skill(isolated_engine, "PyTorch", age_days=1)
    older = _skill(isolated_engine, "Time-series analysis", age_days=10)
    _skill(isolated_engine, "time series  Analysis", age_days=5)

    with Session(isolated_engine) as s:
        assert resolve_skill(s, uid, "torch").skill_id == canonical          # alias -> exact canonical
        assert resolve_skill(s, uid, "Time Series Analysis").skill_id == older
    # With only non-canonical spellings left, the alias-canonical rule decides over age.
    with Session(isolated_engine) as s:
        s.delete(s.get(Skill, canonical))
        s.commit()
        assert resolve_skill(s, uid, "PYTORCH").name == "pytorch"


def test_resolve_finds_nothing_for_a_new_skill_and_get_or_create_names_it_canonically(isolated_engine):
    uid = _user(isolated_engine)
    with Session(isolated_engine) as s:
        assert resolve_skill(s, uid, "Airflow") is None
        assert resolve_skill(s, uid, "  ") is None
        resolver = SkillResolver(s, uid)
        made = resolver.get_or_create("torch")
        assert made.name == "PyTorch"                                      # the alias map, once
        assert resolver.get_or_create("pytorch").skill_id == made.skill_id  # a batch sees its own rows
        s.commit()
    assert _all_skill_names(isolated_engine) == ["PyTorch"]


# ── every link path joins the existing row ────────────────────────────────────

@pytest.mark.parametrize("stored, spelling", [
    ("PyTorch", "pytorch"), ("PyTorch", "Pytorch"), ("PyTorch", "torch"),
    ("Time series analysis", "Time-Series Analysis"), ("Time-Series Analysis", "time series analysis"),
])
def test_the_parser_save_path_joins_the_users_existing_skill(isolated_engine, stored, spelling):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, stored)

    outcomes = _store(uid)._save_skills([{"name": spelling, "proficiency": 4}], "github:repo")

    assert outcomes[0][0] == "created"                                    # a new source edge ...
    assert outcomes[0][1]["name"] == stored                               # ... on the existing skill
    assert _all_skill_names(isolated_engine) == [stored]
    assert [s["name"] for s in services.get_skills(uid)] == [stored]


def test_the_parser_save_path_joins_a_skill_only_another_user_has(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    shared = _user_skill(isolated_engine, alice, "Time series analysis")

    outcomes = _store(bob)._save_skills([{"name": "Time-Series Analysis"}], "resume.md")

    assert outcomes[0][1]["skill_id"] == shared
    assert _all_skill_names(isolated_engine) == ["Time series analysis"]


def test_two_spellings_in_one_ingest_make_one_skill(isolated_engine):
    uid = _user(isolated_engine)

    outcomes = _store(uid)._save_skills(
        [{"name": "Time-Series Analysis"}, {"name": "time series analysis"}, {"name": "torch"},
         {"name": "pytorch"}], "resume.md")

    assert [o[0] for o in outcomes] == ["created", "unchanged", "created", "unchanged"]
    assert _linked(isolated_engine, uid) == ["PyTorch", "Time-Series Analysis"]


def test_the_harness_upsert_path_joins_the_users_existing_skill(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "PyTorch")
    _user_skill(isolated_engine, uid, "Time series analysis")

    out = invoke("upsert_items", uid, {"records": [
        {"kind": "skill", "data": {"name": "pytorch", "proficiency": 4}},
        {"kind": "skill", "data": {"name": "torch"}},
        {"kind": "skill", "data": {"name": "Time-Series Analysis"}}]})

    assert {r["key"] for r in out["results"]} == {"skill:pytorch", "skill:time series analysis"}
    assert _all_skill_names(isolated_engine) == ["PyTorch", "Time series analysis"]


def test_a_chat_add_joins_the_users_existing_skill(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "PyTorch")

    assert "already in your profile" in services.add_skill_to_profile(uid, "pytorch")
    assert "already in your profile" in services.add_skill_to_profile(uid, "torch")
    assert _all_skill_names(isolated_engine) == ["PyTorch"]


def test_a_chat_add_joins_a_global_skill_the_user_lacks(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    shared = _user_skill(isolated_engine, alice, "Time series analysis")

    assert "Added" in services.add_skill_to_profile(bob, "Time-Series Analysis")

    assert _all_skill_names(isolated_engine) == ["Time series analysis"]
    with Session(isolated_engine) as s:
        assert s.exec(select(UserSkill).where(UserSkill.user_id == bob)).one().skill_id == shared


def test_a_new_chat_skill_is_stored_under_its_canonical_name(isolated_engine):
    uid = _user(isolated_engine)

    services.add_skill_to_profile(uid, "torch")

    assert _all_skill_names(isolated_engine) == ["PyTorch"]


def test_a_chat_captured_skill_joins_the_users_existing_skill(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "Time series analysis")

    out = services.create_artifact_from_chat(
        uid, "skill", {"name": "Time-Series Analysis", "evidence": "I forecast demand weekly"})

    assert "already in your profile" in out
    assert _all_skill_names(isolated_engine) == ["Time series analysis"]


def test_a_chat_captured_skill_spelled_differently_joins_a_global_skill(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _user_skill(isolated_engine, alice, "PyTorch")

    out = services.create_artifact_from_chat(bob, "skill", {"name": "pytorch", "evidence": "I train with it"})

    assert "Added skill" in out
    assert _all_skill_names(isolated_engine) == ["PyTorch"]


def test_a_superseded_skill_is_found_by_fold(isolated_engine):
    uid = _user(isolated_engine)
    sid = _user_skill(isolated_engine, uid, "PyTorch", proficiency=2)

    with Session(isolated_engine) as s:
        out = services._supersede_skill(s, uid, {"name": "torch", "proficiency": 5}, "now senior", "chat:x")

    assert "Updated skill 'PyTorch'" in out
    with Session(isolated_engine) as s:
        assert s.exec(select(UserSkill).where(UserSkill.skill_id == sid)).one().proficiency == 5


def test_pinning_a_skill_by_another_spelling_pins_it_and_its_unmerged_twin(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "PyTorch")
    _user_skill(isolated_engine, uid, "pytorch", source="github:repo")     # not merged yet

    assert "Pinned" in services.set_skill_core(uid, "torch", True)

    with Session(isolated_engine) as s:
        assert [l.is_core for l in s.exec(select(UserSkill).where(UserSkill.user_id == uid)).all()] == [True, True]
    assert "not in your profile" in services.set_skill_core(uid, "Airflow", True)


def test_the_job_analyzer_points_a_postings_skill_at_the_owners_existing_row(isolated_engine):
    from agents.job_analyzer import JobAnalyzerAgent

    uid = _user(isolated_engine)
    mine = _user_skill(isolated_engine, uid, "PyTorch")
    job_id = _job(isolated_engine, uid)

    JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "MLE", "company": "Acme"},
        [{"name": "pytorch", "required": True, "weight": 1.0}, {"name": "Airflow", "required": False}],
        "Uses pytorch", "paste", job_id=str(job_id))

    assert _job_skill_names(isolated_engine, job_id) == ["Airflow", "PyTorch"]
    assert _all_skill_names(isolated_engine) == ["Airflow", "PyTorch"]
    with Session(isolated_engine) as s:
        assert s.exec(select(JobSkill.skill_id).join(Skill).where(Skill.name == "PyTorch")).one() == mine


def test_a_new_job_with_no_owner_still_joins_a_global_skill(isolated_engine):
    from agents.job_analyzer import JobAnalyzerAgent

    alice = _user(isolated_engine, "Alice")
    mine = _user_skill(isolated_engine, alice, "Time series analysis")

    job = JobAnalyzerAgent.__new__(JobAnalyzerAgent)._save_job(
        {"title": "DS", "company": "Acme"}, [{"name": "Time-Series Analysis"}], "text", "paste")

    assert _job_skill_names(isolated_engine, job.job_id) == ["Time series analysis"]
    assert _all_skill_names(isolated_engine) == ["Time series analysis"]
    with Session(isolated_engine) as s:
        assert s.exec(select(JobSkill.skill_id)).one() == mine


def test_the_chat_job_analysis_path_joins_the_owners_existing_row(isolated_engine, monkeypatch):
    import agents.chat as chat_module
    import agents.job_analyzer as ja_module

    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "Time series analysis")
    job_id = _job(isolated_engine, uid)

    class _FakeAnalyzer:
        def _extract_skills(self, text):
            return [{"name": "Time-Series Analysis", "category": "Data", "required": True, "weight": 0.9}]

    import services
    monkeypatch.setattr(ja_module, "JobAnalyzerAgent", _FakeAnalyzer)
    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: object())
    # The chat path also rebuilds the job's JD profile, a model call this test isn't about.
    monkeypatch.setattr(services, "rebuild_jd_profile", lambda *a, **k: None)
    agent = chat_module.ChatAgent()
    agent.set_active_job(str(job_id))
    agent._analyze_active_job("")

    assert _job_skill_names(isolated_engine, job_id) == ["Time series analysis"]
    assert _all_skill_names(isolated_engine) == ["Time series analysis"]


# ── merging a user's existing duplicates ──────────────────────────────────────

def _merge(engine, uid):
    with Session(engine) as s:
        merged = merge_user_skill_duplicates(s, uid)
        s.commit()
    return merged


def _rows(engine, uid):
    with Session(engine) as s:
        return sorted(((s.get(Skill, l.skill_id).name, l.evidence_source, l.proficiency, l.is_core)
                       for l in s.exec(select(UserSkill).where(UserSkill.user_id == uid)).all()),
                      key=lambda r: (r[0], r[1] or ""))


def test_a_users_folded_duplicates_become_one_skill_with_every_source_kept(isolated_engine):
    uid = _user(isolated_engine)
    keeper = _user_skill(isolated_engine, uid, "PyTorch", source="resume.md", proficiency=2)
    twin = _user_skill(isolated_engine, uid, "pytorch", source="github:repo", proficiency=5, is_core=True)
    _link(isolated_engine, uid, twin, source="resume.md", proficiency=4)       # repeats the keeper's source
    _user_skill(isolated_engine, uid, "SQL", source="resume.md")

    assert _merge(isolated_engine, uid) == 1

    assert _rows(isolated_engine, uid) == [
        ("PyTorch", "github:repo", 5, True),
        ("PyTorch", "resume.md", 4, False),            # higher proficiency kept on the repeated source
        ("SQL", "resume.md", 3, False)]
    with Session(isolated_engine) as s:
        assert {l.skill_id for l in s.exec(select(UserSkill).where(
            UserSkill.user_id == uid, UserSkill.evidence_source == "github:repo")).all()} == {keeper}
    # Idempotent.
    assert _merge(isolated_engine, uid) == 0
    assert len(_rows(isolated_engine, uid)) == 3


def test_core_survives_a_merge_when_either_row_had_it(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "Time series analysis", is_core=False, proficiency=1)
    twin = _skill(isolated_engine, "Time-Series Analysis", age_days=-1)
    _link(isolated_engine, uid, twin, is_core=True, proficiency=2)             # same source, twin loses

    _merge(isolated_engine, uid)

    assert _rows(isolated_engine, uid) == [("Time series analysis", "resume.md", 2, True)]


def test_the_keeper_is_the_alias_canonical_spelling_then_the_oldest(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "pytorch", source="a", age_days=90)
    _user_skill(isolated_engine, uid, "PyTorch", source="b", age_days=1)
    _user_skill(isolated_engine, uid, "time series analysis", source="a", age_days=2)
    _user_skill(isolated_engine, uid, "Time-Series Analysis", source="b", age_days=40)

    assert _merge(isolated_engine, uid) == 2

    assert _linked(isolated_engine, uid) == ["PyTorch", "Time-Series Analysis"]


def test_a_merge_never_deletes_or_renames_a_global_skill_or_touches_another_user(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    keeper = _user_skill(isolated_engine, alice, "PyTorch", source="resume.md")
    twin = _user_skill(isolated_engine, alice, "pytorch", source="github:repo")
    _link(isolated_engine, bob, twin, source="linkedin:x", proficiency=1)       # Bob links the twin
    before = _all_skill_names(isolated_engine)

    _merge(isolated_engine, alice)

    assert _all_skill_names(isolated_engine) == before                           # both rows remain, same names
    with Session(isolated_engine) as s:
        bob_row = s.exec(select(UserSkill).where(UserSkill.user_id == bob)).one()
        assert (bob_row.skill_id, bob_row.evidence_source, bob_row.proficiency) == (twin, "linkedin:x", 1)
        assert {l.skill_id for l in s.exec(select(UserSkill).where(UserSkill.user_id == alice)).all()} == {keeper}
    assert _linked(isolated_engine, bob) == ["pytorch"]                          # Bob's profile is untouched


def test_a_merge_repoints_the_users_own_jobs_and_leaves_other_users_jobs(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    keeper = _user_skill(isolated_engine, alice, "PyTorch")
    twin = _user_skill(isolated_engine, alice, "pytorch", source="github:repo")
    mine, mine_both, theirs = _job(isolated_engine, alice), _job(isolated_engine, alice), _job(isolated_engine, bob)
    with Session(isolated_engine) as s:
        s.add(JobSkill(job_id=mine, skill_id=twin, required=False, weight=0.4))
        s.add(JobSkill(job_id=mine_both, skill_id=keeper, required=False, weight=0.2))
        s.add(JobSkill(job_id=mine_both, skill_id=twin, required=True, weight=0.9))
        s.add(JobSkill(job_id=theirs, skill_id=twin, required=True, weight=1.0))
        s.commit()

    _merge(isolated_engine, alice)

    with Session(isolated_engine) as s:
        got = {(j.job_id, j.skill_id): (j.required, j.weight) for j in s.exec(select(JobSkill)).all()}
    assert got == {(mine, keeper): (False, 0.4),
                   (mine_both, keeper): (True, 0.9),                              # one row, strongest facts
                   (theirs, twin): (True, 1.0)}


def test_a_dismissed_skill_tombstone_naming_a_merged_away_id_follows_the_keeper(isolated_engine):
    uid = _user(isolated_engine)
    keeper = _user_skill(isolated_engine, uid, "PyTorch")
    twin = _user_skill(isolated_engine, uid, "pytorch", source="github:repo")
    with Session(isolated_engine) as s:
        s.add(DeletedEntry(user_id=uid, entity_type="skill", key_a="pytorch",
                           payload={"skill_id": str(twin), "name": "pytorch", "category": "Language", "rows": []}))
        s.add(DeletedEntry(user_id=uid, entity_type="skill", key_a="sql",
                           payload={"skill_id": str(uuid4()), "name": "SQL", "rows": []}))
        s.add(DeletedEntry(user_id=uid, entity_type="skill", key_a="old"))        # an old tombstone: no payload
        s.commit()

    _merge(isolated_engine, uid)

    with Session(isolated_engine) as s:
        tombs = {t.key_a: t.payload for t in s.exec(select(DeletedEntry)).all()}
    assert tombs["pytorch"]["skill_id"] == str(keeper) and tombs["pytorch"]["name"] == "PyTorch"
    assert tombs["sql"]["name"] == "SQL" and tombs["old"] is None


def test_the_evidence_view_shows_one_skill_with_the_union_of_evidence_after_the_merge(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "Time series analysis", source="resume.md")
    _user_skill(isolated_engine, uid, "Time-Series Analysis", source="github:https://github.com/a/b",
                proficiency=5)
    summary = lambda: [(s["name"], s["sources"], s["proficiency"]) for s in build_evidence_view(uid)["skills"]]
    assert len(summary()) == 2

    _merge(isolated_engine, uid)

    assert summary() == [("Time series analysis", ["github", "resume"], 5)]
    assert [s["name"] for s in services.get_skills(uid)] == ["Time series analysis"]


def test_a_harness_ingest_heals_the_users_existing_duplicates(isolated_engine):
    uid = _user(isolated_engine)
    _user_skill(isolated_engine, uid, "PyTorch", source="resume.md")
    _user_skill(isolated_engine, uid, "pytorch", source="github:repo")

    invoke("upsert_items", uid, {"records": [{"kind": "skill", "data": {"name": "Airflow"}}]})

    assert _linked(isolated_engine, uid) == ["Airflow", "PyTorch"]


def test_the_backfill_script_merges_and_its_dry_run_changes_nothing(isolated_engine, capsys):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "merge_skill_duplicates", Path(__file__).resolve().parent.parent / "scripts" / "merge_skill_duplicates.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    uid, other = _user(isolated_engine), _user(isolated_engine, "Other")
    _user_skill(isolated_engine, uid, "PyTorch", source="a")
    twin = _user_skill(isolated_engine, uid, "pytorch", source="b")
    _link(isolated_engine, other, twin, source="c")

    assert script.merge(uid, dry_run=True) == 1
    assert _linked(isolated_engine, uid) == ["PyTorch", "pytorch"]
    assert script.merge(uid) == 1
    assert _linked(isolated_engine, uid) == ["PyTorch"]
    assert script.merge(uid) == 0                                                  # safe to re-run
    assert _linked(isolated_engine, other) == ["pytorch"]
