"""Preference API tests (issue #129).

The confirmation model at the HTTP boundary, and the two non-negotiables from
#121 applied to a tier that *suppresses* resume content: every extraction is
correctable, and nothing is ever deleted. The React surface over these
endpoints is a follow-up (#147); this is what it will call.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import services
from database.models import ChatMessage, User, UserPreference


def _seed_user(engine, name="Prefer") -> User:
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


@pytest.fixture()
def pref_client(isolated_engine, monkeypatch):
    """TestClient factory on the isolated DB, auth overridden per user."""
    import database.db as db_module
    import web.routers.preferences_router as router_module

    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(router_module, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    import web.auth as web_auth_module
    from web.app import create_app

    def _make(user: User):
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


_PROPOSAL = {
    "text": "Do not lead with the Recipe App, it was coursework.",
    "polarity": "suppress",
    "target_type": "project",
    "target_key": "proj:recipe app",
    "target_term": "Recipe App",
    "scope_type": "global",
    "scope_value": None,
    "strength": 3,
    "confidence": 0.8,
    "provenance": {"quote": "that was just a class project"},
    "decision": "add",
}


def _patch_extraction(monkeypatch, proposals):
    monkeypatch.setattr(
        services, "propose_preferences",
        lambda user_id, job_id, messages: list(proposals),
    )


def test_propose_returns_candidates_and_writes_nothing(
        isolated_engine, pref_client, monkeypatch):
    user = _seed_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(ChatMessage(job_id=None, user_id=user.user_id, role="user",
                          content="that was just a class project"))
        s.commit()
    _patch_extraction(monkeypatch, [_PROPOSAL])

    res = pref_client(user).post("/api/preferences/propose", json={})
    assert res.status_code == 200
    assert res.json()["proposals"][0]["target_key"] == "proj:recipe app"
    with Session(isolated_engine) as s:
        assert s.exec(select(UserPreference)).all() == []


def test_propose_filters_out_already_held_preferences(
        isolated_engine, pref_client, monkeypatch):
    user = _seed_user(isolated_engine)
    _patch_extraction(monkeypatch, [{**_PROPOSAL, "decision": "no_op"}])
    res = pref_client(user).post("/api/preferences/propose", json={})
    assert res.json()["proposals"] == []


def test_accepting_a_proposal_persists_it(isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    res = pref_client(user).post(
        "/api/preferences/decide", json={"action": "accept", "proposal": _PROPOSAL})
    assert res.status_code == 200
    assert res.json()["saved"] is True
    assert len(services.load_preferences(user.user_id)) == 1


def test_dismissing_a_proposal_writes_nothing(isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    res = pref_client(user).post(
        "/api/preferences/decide", json={"action": "dismiss", "proposal": _PROPOSAL})
    assert res.json()["saved"] is False
    assert services.load_preferences(user.user_id) == []


def test_an_unknown_action_is_rejected(isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    res = pref_client(user).post(
        "/api/preferences/decide", json={"action": "obliterate", "proposal": _PROPOSAL})
    assert res.status_code == 422


def test_listing_returns_only_active_preferences_by_default(
        isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    client = pref_client(user)
    client.post("/api/preferences/decide",
                json={"action": "accept", "proposal": _PROPOSAL})
    pid = services.load_preferences(user.user_id)[0]["preference_id"]
    client.delete(f"/api/preferences/{pid}")

    assert client.get("/api/preferences/").json()["preferences"] == []
    everything = client.get(
        "/api/preferences/?include_inactive=true").json()["preferences"]
    assert everything[0]["status"] == "retracted"


def test_editing_corrects_an_extraction_and_stamps_the_flag(
        isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    client = pref_client(user)
    client.post("/api/preferences/decide",
                json={"action": "accept", "proposal": _PROPOSAL})
    pid = services.load_preferences(user.user_id)[0]["preference_id"]

    res = client.patch(f"/api/preferences/{pid}", json={"strength": 5})
    assert res.status_code == 200
    assert res.json()["strength"] == 5
    assert res.json()["edited"] is True


def test_retracting_keeps_the_row(isolated_engine, pref_client):
    """DELETE retracts. Negation must not expire, and the transition is what
    #51 Phase 2 learns preference weights from."""
    user = _seed_user(isolated_engine)
    client = pref_client(user)
    client.post("/api/preferences/decide",
                json={"action": "accept", "proposal": _PROPOSAL})
    pid = services.load_preferences(user.user_id)[0]["preference_id"]

    assert client.delete(f"/api/preferences/{pid}").json()["status"] == "retracted"
    with Session(isolated_engine) as s:
        assert len(s.exec(select(UserPreference)).all()) == 1


def test_another_users_preference_is_not_reachable(isolated_engine, pref_client):
    victim, attacker = _seed_user(isolated_engine, "Victim"), _seed_user(
        isolated_engine, "Attacker")
    pref_client(victim).post("/api/preferences/decide",
                             json={"action": "accept", "proposal": _PROPOSAL})
    pid = services.load_preferences(victim.user_id)[0]["preference_id"]

    client = pref_client(attacker)
    assert client.get("/api/preferences/").json()["preferences"] == []
    assert client.patch(f"/api/preferences/{pid}", json={"strength": 1}).status_code == 404
    assert client.delete(f"/api/preferences/{pid}").status_code == 404
    assert services.load_preferences(victim.user_id)[0]["strength"] == 3


def test_a_malformed_preference_id_is_a_404_not_a_500(isolated_engine, pref_client):
    user = _seed_user(isolated_engine)
    assert pref_client(user).delete("/api/preferences/not-a-uuid").status_code == 404
