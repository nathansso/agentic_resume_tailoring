"""Dismissing a skill claim that no bullet proves (issue #321).

A skill nothing cites (a LinkedIn skill list entry, a GitHub language) can be dismissed
from the evidence map. These pin the contract:

- dismissing deletes the user's `UserSkill` rows and tombstones the skill with a snapshot;
- a skill a bullet cites is refused (409): the answer there is to edit the bullet;
- a re-ingest (kg_store and the harness's `upsert_items`) does not bring it back, nor an alias;
- restore puts the rows back exactly and drops the tombstone;
- asking for the skill in chat clears the dismissal;
- one user's dismissal touches no one else's data;
- an old database without the `payload` column migrates, and its old tombstones still block.
"""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import services
from database.models import DeletedEntry, Experience, Project, Skill, User, UserSkill
from harness.contract import invoke
from knowledge_graph.builder import SkillGraphBuilder
from knowledge_graph.evidence_view import build_evidence_view


def _user(engine, name="Dismisser"):
    with Session(engine) as s:
        u = User(name=name, email=f"{name.lower()}_{uuid4().hex[:6]}@example.com")
        s.add(u)
        s.commit()
        return u.user_id


def _skill(engine, uid, name, source="linkedin:https://example.com/in/x", **row):
    with Session(engine) as s:
        sk = s.exec(select(Skill).where(Skill.name == name)).first()
        if sk is None:
            sk = Skill(name=name, category="Language")
            s.add(sk)
            s.commit()
            s.refresh(sk)
        s.add(UserSkill(user_id=uid, skill_id=sk.skill_id, evidence_source=source,
                        **{"proficiency": 3, "confidence_score": 0.9, **row}))
        s.commit()
        return sk.skill_id


def _cited(engine, uid, skill="Python"):
    """A skill a bullet names: it has evidence, so it cannot be dismissed."""
    sid = _skill(engine, uid, skill, source="resume.md")
    with Session(engine) as s:
        s.add(Experience(user_id=uid, title="Engineer", company="Acme",
                         bullets=[f"Built the pipeline in {skill}"]))
        s.commit()
    return sid


def _rows(engine, uid, sid):
    with Session(engine) as s:
        rows = s.exec(select(UserSkill).where(UserSkill.user_id == uid,
                                              UserSkill.skill_id == sid)).all()
        return sorted(({f: getattr(r, f) for f in
                        ("evidence_source", "evidence_detail", "proficiency", "confidence_score",
                         "is_core", "source_context", "created_at", "updated_at")} for r in rows),
                      key=lambda d: d["evidence_source"] or "")


def _tombs(engine, uid):
    with Session(engine) as s:
        return s.exec(select(DeletedEntry).where(DeletedEntry.user_id == uid,
                                                 DeletedEntry.entity_type == "skill")).all()


def _names(view):
    return {s["name"] for s in view["skills"]}


@pytest.fixture()
def client_for(isolated_engine, monkeypatch):
    import database.db as db_module
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(services, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    from web.app import create_app
    import web.auth as web_auth_module

    def make(uid):
        with Session(isolated_engine) as s:
            user = s.get(User, uid)
            s.expunge(user)
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return make


def _store(uid):
    from agents.kg_store import KGStoreMixin

    store = KGStoreMixin()
    store.user = SimpleNamespace(user_id=uid)
    return store


# ── dismiss ───────────────────────────────────────────────────────────────────

def test_dismissing_an_unproven_skill_removes_its_rows_and_tombstones_a_snapshot(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker", is_core=True, source_context="chat:abc")
    _skill(isolated_engine, uid, "Docker", source="github:me/app", proficiency=5)
    _cited(isolated_engine, uid)

    out = services.dismiss_skill(uid, str(sid))

    assert out == {"skill_id": str(sid), "name": "Docker"}
    assert _rows(isolated_engine, uid, sid) == []
    (tomb,) = _tombs(isolated_engine, uid)
    assert tomb.key_a == "docker" and tomb.payload["skill_id"] == str(sid)
    assert sorted(r["evidence_source"] for r in tomb.payload["rows"]) == [
        "github:me/app", "linkedin:https://example.com/in/x"]
    assert "Docker" not in _names(build_evidence_view(uid))
    assert "Python" in _names(build_evidence_view(uid))
    with Session(isolated_engine) as s:                     # the global row is untouched
        assert s.get(Skill, sid) is not None


def test_a_skill_a_bullet_cites_cannot_be_dismissed(isolated_engine, client_for):
    uid = _user(isolated_engine)
    sid = _cited(isolated_engine, uid)

    r = client_for(uid).post(f"/api/profile/skills/{sid}/dismiss")

    assert r.status_code == 409 and "Edit the bullet" in r.json()["detail"]
    assert len(_rows(isolated_engine, uid, sid)) == 1 and _tombs(isolated_engine, uid) == []


def test_a_skill_only_github_reported_can_be_dismissed(isolated_engine):
    uid = _user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Project(user_id=uid, name="app", description="", repo_url="https://github.com/me/app"))
        s.commit()
    sid = _skill(isolated_engine, uid, "Rust", source="github:me/app")
    view = build_evidence_view(uid)
    assert view["stats"]["github_only"] == 1

    services.dismiss_skill(uid, str(sid))

    assert "Rust" not in _names(build_evidence_view(uid))


def test_a_dismissed_skill_is_gone_from_what_tailoring_reads(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    _skill(isolated_engine, uid, "SQL", source="resume.md")
    assert {s["name"] for s in services.get_skills(uid)} == {"Docker", "SQL"}

    services.dismiss_skill(uid, str(sid))

    assert {s["name"] for s in services.get_skills(uid)} == {"SQL"}
    graph_skills = {d["name"] for _, d in SkillGraphBuilder(uid).build_graph().nodes(data=True)
                    if d.get("type") == "Skill"}
    assert graph_skills == {"SQL"}
    items = invoke("list_items", uid, {"kind": "skill"})["items"]
    assert [i["key"] for i in items] == ["skill:sql"]


def test_dismissing_twice_or_an_unknown_id_is_not_found(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    services.dismiss_skill(uid, str(sid))
    for bad in (str(sid), str(uuid4()), "not-a-uuid"):
        with pytest.raises(LookupError):
            services.dismiss_skill(uid, bad)


# ── re-ingest ─────────────────────────────────────────────────────────────────

def test_the_kg_store_save_path_skips_a_dismissed_skill_and_its_aliases(isolated_engine):
    uid = _user(isolated_engine)
    docker = _skill(isolated_engine, uid, "Docker")
    torch = _skill(isolated_engine, uid, "PyTorch")
    services.dismiss_skill(uid, str(docker))
    services.dismiss_skill(uid, str(torch))

    outcomes = _store(uid)._save_skills(
        [{"name": "Docker", "proficiency": 4}, {"name": "torch", "proficiency": 4},
         {"name": "Kubernetes", "proficiency": 2}], "linkedin:https://example.com/in/x")

    assert [status for status, _ in outcomes] == ["skipped_tombstone", "skipped_tombstone", "created"]
    assert {s["name"] for s in services.get_skills(uid)} == {"Kubernetes"}
    assert _rows(isolated_engine, uid, docker) == [] and _rows(isolated_engine, uid, torch) == []


def test_a_respelled_skill_is_still_dismissed(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Time series analysis")
    services.dismiss_skill(uid, str(sid))

    outcomes = _store(uid)._save_skills([{"name": "Time-Series Analysis"}], "resume.md")

    assert outcomes[0][0] == "skipped_tombstone"


def test_the_harness_upsert_path_skips_a_dismissed_skill(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    services.dismiss_skill(uid, str(sid))

    out = invoke("upsert_items", uid, {"records": [
        {"kind": "skill", "data": {"name": "Docker", "proficiency": 4}},
        {"kind": "skill", "data": {"name": "docker"}},
        {"kind": "skill", "data": {"name": "Airflow", "proficiency": 3}}]})

    statuses = [(r["status"], r["key"]) for r in out["results"]]
    assert statuses == [("skipped_tombstone", None), ("skipped_tombstone", None),
                        ("created", "skill:airflow")]
    assert "dismissed" in out["results"][0]["message"]
    assert _rows(isolated_engine, uid, sid) == []


# ── restore ───────────────────────────────────────────────────────────────────

def test_restore_brings_the_rows_back_exactly_and_drops_the_tombstone(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker", is_core=True, proficiency=4,
                 evidence_detail="listed under Skills", source_context="chat:abc")
    _skill(isolated_engine, uid, "Docker", source="github:me/app", proficiency=2,
           confidence_score=0.4)
    before = _rows(isolated_engine, uid, sid)
    services.dismiss_skill(uid, str(sid))

    out = services.restore_skill(uid, str(sid))

    assert out == {"skill_id": str(sid), "name": "Docker"}
    assert _rows(isolated_engine, uid, sid) == before
    assert _tombs(isolated_engine, uid) == []
    assert "Docker" in _names(build_evidence_view(uid))
    assert services.restore_skill(uid, str(sid)) is None        # nothing left to restore


def test_a_restored_skill_is_ingested_again(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    services.dismiss_skill(uid, str(sid))
    services.restore_skill(uid, str(sid))

    outcomes = _store(uid)._save_skills([{"name": "Docker", "proficiency": 5}],
                                        "linkedin:https://example.com/in/x")

    assert outcomes[0][0] == "merged"


def test_asking_for_the_skill_in_chat_clears_the_dismissal(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    services.dismiss_skill(uid, str(sid))

    assert "Added" in services.add_skill_to_profile(uid, "docker")

    assert _tombs(isolated_engine, uid) == []
    assert {s["name"] for s in services.get_skills(uid)} == {"Docker"}


def test_a_chat_captured_skill_also_clears_the_dismissal(isolated_engine):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    services.dismiss_skill(uid, str(sid))

    out = services.create_artifact_from_chat(
        uid, "skill", {"name": "Docker", "evidence": "I ran Docker in prod"})

    assert "Added skill" in out and _tombs(isolated_engine, uid) == []
    outcomes = _store(uid)._save_skills([{"name": "Docker"}], "resume.md")
    assert outcomes[0][0] != "skipped_tombstone"


# ── routes and isolation ──────────────────────────────────────────────────────

def test_routes_dismiss_list_and_restore(isolated_engine, client_for):
    uid = _user(isolated_engine)
    sid = _skill(isolated_engine, uid, "Docker")
    _skill(isolated_engine, uid, "Airflow", source="resume.md")
    client = client_for(uid)

    assert client.get("/api/profile/skills/dismissed").json() == []
    r = client.post(f"/api/profile/skills/{sid}/dismiss")
    assert r.status_code == 200 and r.json()["name"] == "Docker"

    listed = client.get("/api/profile/skills/dismissed").json()
    assert [(d["skill_id"], d["name"], d["sources"]) for d in listed] == [
        (str(sid), "Docker", ["linkedin"])]
    assert listed[0]["dismissed_at"]
    assert "Docker" not in {s["name"] for s in client.get("/api/profile/graph/view").json()["skills"]}

    assert client.post(f"/api/profile/skills/{sid}/restore").status_code == 200
    assert client.get("/api/profile/skills/dismissed").json() == []
    assert "Docker" in {s["name"] for s in client.get("/api/profile/graph/view").json()["skills"]}
    assert client.post(f"/api/profile/skills/{sid}/restore").status_code == 404
    assert client.post("/api/profile/skills/nope/dismiss").status_code == 404


def test_one_users_dismissal_is_not_another_users(isolated_engine, client_for):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    a_sid = _skill(isolated_engine, alice, "Docker")
    b_sid = _skill(isolated_engine, bob, "Docker")
    assert a_sid == b_sid                                   # one shared global Skill row

    # Bob cannot dismiss or restore Alice's skill, nor see it listed.
    client_for(alice).post(f"/api/profile/skills/{a_sid}/dismiss")
    assert client_for(bob).get("/api/profile/skills/dismissed").json() == []
    assert client_for(bob).post(f"/api/profile/skills/{a_sid}/restore").status_code == 404
    assert len(_rows(isolated_engine, bob, b_sid)) == 1     # untouched
    assert _tombs(isolated_engine, bob) == []

    # A user with no such skill gets a 404, not a hint that it exists.
    carol = _user(isolated_engine, "Carol")
    assert client_for(carol).post(f"/api/profile/skills/{a_sid}/dismiss").status_code == 404

    # Alice's dismissal does not stop Bob's ingest, and Alice restoring hers leaves Bob's alone.
    assert _store(bob)._save_skills([{"name": "Docker", "proficiency": 5}], "resume.md")[0][0] == "created"
    assert client_for(alice).post(f"/api/profile/skills/{a_sid}/restore").status_code == 200
    assert len(_rows(isolated_engine, bob, b_sid)) == 2


# ── migration ─────────────────────────────────────────────────────────────────

def test_an_old_database_without_the_payload_column_migrates_and_its_tombstones_still_block(
        tmp_path, monkeypatch):
    import sqlalchemy as sa
    import database.db as db_module

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    uid = uuid4()
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE deletedentry (id INTEGER PRIMARY KEY, user_id CHAR(32) NOT NULL, "
            "entity_type VARCHAR NOT NULL, key_a VARCHAR NOT NULL, key_b VARCHAR, "
            "created_at TIMESTAMP)"))
        conn.execute(sa.text(
            "INSERT INTO deletedentry (user_id, entity_type, key_a, key_b, created_at) "
            "VALUES (:u, 'project', 'Old Project', NULL, '2026-01-01 00:00:00')"),
            {"u": uid.hex})
    monkeypatch.setattr(db_module, "engine", engine)

    db_module._migrate_db()
    db_module._migrate_db()                                  # idempotent

    with Session(engine) as s:
        (tomb,) = s.exec(select(DeletedEntry)).all()
        assert tomb.payload is None and tomb.key_a == "Old Project"
        from agents.kg_store import KGStoreMixin
        assert KGStoreMixin._project_tombstoned([tomb], "Old Project", None)
        s.add(DeletedEntry(user_id=uid, entity_type="skill", key_a="docker",
                           payload={"skill_id": "x", "rows": []}))
        s.commit()
        assert s.exec(select(DeletedEntry).where(DeletedEntry.entity_type == "skill")).one().payload["skill_id"] == "x"
