"""Two projects (or roles) with one name must not share evidence (issue #334).

The graph keys a node by name (`Project:<name>`) and a cite by `proj_key` / `exp_key`, so
two same-name items cannot be told apart there. ART keeps the invariant instead of re-keying:

- a rename onto another item's key is refused (web 409); the chat supersede path matches the key
  twin first, so it never renames a row onto another;
- chat create matches by key, so "my app" is "My App", never a second row;
- the builder reads rows in a fixed order, so which of two rows survives a collapsed node
  does not depend on storage order;
- a duplicate that does exist (made between ingests) is flagged in the evidence view, and
  neither item is handed the collapsed node's links.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import services
from database.models import Experience, Project, User
from harness.contract import invoke
from knowledge_graph.builder import SkillGraphBuilder
from tests.test_evidence_view import _experience, _item, _project, _skill


def _user(engine, name="Namer"):
    with Session(engine) as s:
        u = User(name=name, email=f"{name.lower()}_{uuid4().hex[:6]}@example.com")
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.user_id


def _names(engine, uid, model=Project, field="name"):
    with Session(engine) as s:
        return sorted(getattr(r, field) for r in s.exec(select(model).where(model.user_id == uid)).all())


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


# ── a rename onto another item's key is refused ──────────────────────────────────

@pytest.mark.parametrize("new_name", ["Atlas", "atlas", "  Atlas  ", "ATLAS"])
def test_project_rename_onto_another_projects_key_is_409(isolated_engine, client_for, new_name):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas", "first")
    other = _project(isolated_engine, uid, "Beacon", "second")

    r = client_for(uid).patch(f"/api/profile/projects/{other}", json={"name": new_name})

    assert r.status_code == 409
    assert "Atlas" in r.json()["detail"] and "delete the other" in r.json()["detail"]
    assert _names(isolated_engine, uid) == ["Atlas", "Beacon"]      # nothing changed
    with Session(isolated_engine) as s:
        assert s.get(Project, other).manually_edited is False


def test_project_rename_to_a_new_name_or_its_own_still_works(isolated_engine, client_for):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas", "first")
    mine = _project(isolated_engine, uid, "Beacon", "second")
    client = client_for(uid)

    assert client.patch(f"/api/profile/projects/{mine}", json={"name": "Lighthouse"}).status_code == 200
    assert client.patch(f"/api/profile/projects/{mine}", json={"name": "LIGHTHOUSE"}).status_code == 200
    assert client.patch(f"/api/profile/projects/{mine}",
                        json={"name": "LIGHTHOUSE", "description": "x"}).status_code == 200
    assert _names(isolated_engine, uid) == ["Atlas", "LIGHTHOUSE"]


def test_another_users_project_of_the_same_name_is_not_a_clash(isolated_engine, client_for):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _project(isolated_engine, bob, "Atlas", "his")
    mine = _project(isolated_engine, alice, "Beacon", "hers")

    assert client_for(alice).patch(f"/api/profile/projects/{mine}", json={"name": "Atlas"}).status_code == 200


def test_editing_an_existing_duplicate_other_fields_is_not_blocked(isolated_engine, client_for):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas", "first")
    dup = _project(isolated_engine, uid, "atlas", "second")      # made between ingests

    assert client_for(uid).patch(f"/api/profile/projects/{dup}",
                                 json={"description": "fixed up"}).status_code == 200


@pytest.mark.parametrize("title,company", [
    ("Data Analyst", "Acme"), ("data analyst", "ACME"), (" Data Analyst ", "Acme "),
])
def test_experience_rename_onto_another_roles_key_is_409(isolated_engine, client_for, title, company):
    uid = _user(isolated_engine)
    _experience(isolated_engine, uid, "Data Analyst", "Acme")
    other = _experience(isolated_engine, uid, "Intern", "Acme")

    r = client_for(uid).patch(f"/api/profile/experiences/{other}", json={"title": title, "company": company})

    assert r.status_code == 409
    assert "Data Analyst" in r.json()["detail"] and "Acme" in r.json()["detail"]
    assert _names(isolated_engine, uid, Experience, "title") == ["Data Analyst", "Intern"]


def test_experience_rename_to_a_new_title_company_or_pair_still_works(isolated_engine, client_for):
    uid = _user(isolated_engine)
    _experience(isolated_engine, uid, "Data Analyst", "Acme")
    mine = _experience(isolated_engine, uid, "Intern", "Acme")
    client = client_for(uid)

    # Same title at another company, and another title at the same company, are different roles.
    assert client.patch(f"/api/profile/experiences/{mine}", json={"company": "Beta", "title": "Data Analyst"}
                        ).status_code == 200
    assert client.patch(f"/api/profile/experiences/{mine}", json={"title": "Staff Analyst", "company": "Acme"}
                        ).status_code == 200
    assert client.patch(f"/api/profile/experiences/{mine}", json={"title": "STAFF ANALYST"}).status_code == 200


def test_the_service_raises_a_value_error_subclass(isolated_engine):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas")
    other = _project(isolated_engine, uid, "Beacon")
    with pytest.raises(services.DuplicateItemError):
        services.update_project(uid, str(other), {"name": "atlas"})
    assert issubclass(services.DuplicateItemError, ValueError)


# ── the harness cannot rename, so `correct` cannot collide ───────────────────────

def test_harness_correct_never_changes_a_name(isolated_engine):
    uid = _user(isolated_engine)
    invoke("upsert_items", uid, {"records": [
        {"kind": "project", "data": {"name": "Atlas", "description": "one"}},
        {"kind": "project", "data": {"name": "Beacon", "description": "two"}},
    ]})

    out = invoke("upsert_items", uid, {"records": [
        {"kind": "project", "correct": True, "data": {"name": "beacon", "description": "two, corrected"}},
    ]})

    assert out["results"][0]["status"] == "corrected"
    assert _names(isolated_engine, uid) == ["Atlas", "Beacon"]


# ── chat create / supersede match by key ─────────────────────────────────────────

def test_chat_create_of_a_project_with_the_same_key_adds_no_second_row(isolated_engine):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "My App", "built it")

    for name in ("my app", "  MY APP "):
        msg = services.create_artifact_from_chat(uid, "project", {"name": name, "evidence": "I built my app"})
        assert "already" in msg.lower() and "My App" in msg
    assert _names(isolated_engine, uid) == ["My App"]

    # A different spelling is a different key, as it is for a cite.
    services.create_artifact_from_chat(uid, "project", {"name": "My-App", "evidence": "I built my app"})
    assert _names(isolated_engine, uid) == ["My App", "My-App"]


def test_chat_create_of_an_experience_with_the_same_key_adds_no_second_row(isolated_engine):
    uid = _user(isolated_engine)
    _experience(isolated_engine, uid, "Data Analyst", "Acme")

    msg = services.create_artifact_from_chat(
        uid, "experience", {"title": "data analyst", "company": "ACME", "evidence": "I was an analyst at Acme"})

    assert "already" in msg.lower()
    assert _names(isolated_engine, uid, Experience, "title") == ["Data Analyst"]


def test_chat_supersede_prefers_the_key_twin_so_it_never_renames_onto_another_row(isolated_engine):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas", "first")
    _project(isolated_engine, uid, "Beacon", "second")

    # The decision targets Beacon but names "atlas": the row with that key is Atlas, so it is
    # the one updated, and Beacon keeps its name.
    msg = services.apply_artifact_decision(uid, {
        "type": "project", "decision": "supersede", "name": "atlas", "description": "now with docs",
        "target": "project: Beacon", "evidence": "I added docs"})

    assert "Updated project 'Atlas'" in msg
    assert _names(isolated_engine, uid) == ["Atlas", "Beacon"]


def test_chat_supersede_of_a_role_prefers_the_key_twin(isolated_engine):
    uid = _user(isolated_engine)
    _experience(isolated_engine, uid, "Data Analyst", "Acme")
    _experience(isolated_engine, uid, "Intern", "Acme")

    msg = services.apply_artifact_decision(uid, {
        "type": "experience", "decision": "supersede", "title": "data analyst", "company": "Acme",
        "description": "owned dashboards", "target": "experience: Intern @ Acme",
        "evidence": "I owned dashboards"})

    assert "Data Analyst @ Acme" in msg
    assert _names(isolated_engine, uid, Experience, "title") == ["Data Analyst", "Intern"]


# ── the builder is deterministic over a forced duplicate ─────────────────────────

def _survivor(engine, uid, kind="Project"):
    graph = SkillGraphBuilder(uid).build_graph()
    return [d["id"] for _, d in graph.nodes(data=True) if d.get("type") == kind]


def test_a_collapsed_project_node_keeps_the_same_row_whatever_the_storage_order(isolated_engine):
    a, b = _user(isolated_engine, "Forward"), _user(isolated_engine, "Backward")
    ids = {}
    for uid, order in ((a, (1, 2)), (b, (2, 1))):       # b stores its seq-2 row first
        for seq in order:
            ids[(uid, seq)] = _project(isolated_engine, uid, "Atlas", f"row {seq}", seq=seq)

    assert _survivor(isolated_engine, a) == [str(ids[(a, 2)])]
    assert _survivor(isolated_engine, b) == [str(ids[(b, 2)])]
    assert _survivor(isolated_engine, b) == _survivor(isolated_engine, b)


def test_a_total_tie_is_broken_by_id_not_by_storage_order(isolated_engine):
    from datetime import datetime, timezone
    from uuid import UUID
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    survivors = []
    for trial, stored in enumerate(("12", "21")):       # the two ids, in the order they are written
        uid = _user(isolated_engine, f"Tie{trial}")
        with Session(isolated_engine) as s:
            for n in stored:
                s.add(Experience(user_id=uid, title="Analyst", company="Acme", created_at=when,
                                 experience_id=UUID(f"0000000{trial}-0000-4000-8000-00000000000{n}")))
            s.commit()
        survivors.append(_survivor(isolated_engine, uid, "Experience"))
    # Same two rows either way: the larger id is the later one, so it is the one that survives.
    assert survivors == [[f"0000000{t}-0000-4000-8000-000000000002"] for t in (0, 1)]


# ── the evidence view flags a duplicate and misattributes nothing ────────────────

def test_evidence_view_flags_both_and_hands_neither_the_collapsed_nodes_links(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _skill(isolated_engine, uid, "Docker")
    _project(isolated_engine, uid, "Atlas", "Wrote it in Python.", seq=1)
    _project(isolated_engine, uid, "Atlas", "Shipped in Docker.", seq=2)

    view = services.get_evidence_view(uid)

    atlas = [i for i in view["items"] if i["title"] == "Atlas"]
    assert len(atlas) == 2
    assert {i["duplicate_of"] for i in atlas} == {atlas[0]["key"], atlas[1]["key"]}
    assert atlas[0]["duplicate_of"] == atlas[1]["key"] and atlas[1]["duplicate_of"] == atlas[0]["key"]
    assert view["edges"] == []              # neither is shown evidence it may not have
    assert view["stats"]["items"] == 2


def test_case_variant_duplicates_are_flagged_but_keep_their_own_links(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _skill(isolated_engine, uid, "Docker")
    _project(isolated_engine, uid, "My App", "Wrote it in Python.", seq=1)
    _project(isolated_engine, uid, "my app", "Shipped in Docker.", seq=2)

    view = services.get_evidence_view(uid)

    first, second = _item(view, "My App"), _item(view, "my app")
    assert first["duplicate_of"] == second["key"] and second["duplicate_of"] == first["key"]
    skills = {s["name"]: s["key"] for s in view["skills"]}
    by_item = {(e["item_key"], e["skill_key"]) for e in view["edges"]}
    assert by_item == {(first["key"], skills["Python"]), (second["key"], skills["Docker"])}


def test_experience_duplicates_are_flagged_and_unique_items_are_not(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _experience(isolated_engine, uid, "Analyst", "Acme", ["Used Python daily"], seq=1)
    _experience(isolated_engine, uid, "Analyst", "Acme", ["Used Python weekly"], seq=2)
    _experience(isolated_engine, uid, "Analyst", "Beta", ["Used Python"], seq=3)
    _project(isolated_engine, uid, "Solo", "Python")

    view = services.get_evidence_view(uid)

    acme = [i for i in view["items"] if i["org"] == "Acme"]
    assert all(i["duplicate_of"] for i in acme)
    assert _item(view, "Solo")["duplicate_of"] is None
    beta = next(i for i in view["items"] if i["org"] == "Beta")
    assert beta["duplicate_of"] is None
    assert {e["item_key"] for e in view["edges"]} == {beta["key"], _item(view, "Solo")["key"]}


def test_a_view_with_no_duplicates_carries_none(isolated_engine):
    uid = _user(isolated_engine)
    _project(isolated_engine, uid, "Atlas")
    _experience(isolated_engine, uid, "Analyst", "Acme")

    assert all(i["duplicate_of"] is None for i in services.get_evidence_view(uid)["items"])
