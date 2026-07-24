"""Chat knowledge-artifact API tests (issue #21).

The confirmation model at the HTTP boundary: `/artifacts/propose` may never
write, `/artifacts/decide` is the only endpoint that touches the knowledge
graph, and dismissing leaves nothing behind. The inline chat-panel UI is a
follow-up issue — these endpoints are the surface it will call.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from database.models import ChatMessage, JobDescription, Project, Skill, User, UserSkill


def _seed_user(engine, name="Chatter") -> User:
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


@pytest.fixture()
def chat_client(isolated_engine, monkeypatch):
    """TestClient factory on the isolated DB, auth overridden per user."""
    import database.db as db_module
    import web.routers.chat_router as chat_router_module

    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(chat_router_module, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    import web.auth as web_auth_module
    from web.app import create_app

    def _make(user: User) -> TestClient:
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


def _patch_extraction(monkeypatch, proposals):
    """Stub the agent's Chain-of-Note step; the pipeline itself is tested elsewhere."""
    import agents.chat as chat_module

    monkeypatch.setattr(
        chat_module.ChatAgent, "_extract_chat_artifacts",
        lambda self, messages: list(proposals),
    )


_REDIS = {
    "type": "skill", "name": "Redis", "category": "Database", "decision": "add",
    "evidence": "I built a distributed cache using Redis",
}


def test_propose_returns_candidates_and_writes_nothing(
        isolated_engine, chat_client, monkeypatch):
    user = _seed_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(ChatMessage(job_id=None, user_id=user.user_id, role="user",
                          content="I built a distributed cache using Redis"))
        s.commit()
    _patch_extraction(monkeypatch, [_REDIS])

    resp = chat_client(user).post("/api/chat/landing/artifacts/propose")
    assert resp.status_code == 200
    assert [p["name"] for p in resp.json()["proposals"]] == ["Redis"]

    with Session(isolated_engine) as s:
        assert s.exec(select(Skill).where(Skill.name == "Redis")).first() is None, (
            "proposing must never write to the knowledge graph")


def test_propose_filters_out_no_op_candidates(
        isolated_engine, chat_client, monkeypatch):
    user = _seed_user(isolated_engine)
    _patch_extraction(monkeypatch, [
        _REDIS, {**_REDIS, "name": "Python", "decision": "no_op"},
    ])

    proposals = chat_client(user).post(
        "/api/chat/landing/artifacts/propose").json()["proposals"]
    assert [p["name"] for p in proposals] == ["Redis"]


def test_decide_accept_writes_the_artifact(isolated_engine, chat_client, monkeypatch):
    user = _seed_user(isolated_engine)
    resp = chat_client(user).post(
        "/api/chat/landing/artifacts/decide",
        json={"action": "accept", "proposal": _REDIS},
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    assert "added" in resp.json()["message"].lower()

    with Session(isolated_engine) as s:
        skill = s.exec(select(Skill).where(Skill.name == "Redis")).first()
        assert skill is not None
        link = s.exec(select(UserSkill).where(
            UserSkill.user_id == user.user_id,
            UserSkill.skill_id == skill.skill_id)).first()
        assert link.source_context == "chat:landing"


def test_decide_dismiss_writes_nothing(isolated_engine, chat_client):
    user = _seed_user(isolated_engine)
    resp = chat_client(user).post(
        "/api/chat/landing/artifacts/decide",
        json={"action": "dismiss", "proposal": _REDIS},
    )
    assert resp.status_code == 200
    assert resp.json()["saved"] is False

    with Session(isolated_engine) as s:
        assert s.exec(select(Skill).where(Skill.name == "Redis")).first() is None


def test_decide_rejects_an_unknown_action(isolated_engine, chat_client):
    user = _seed_user(isolated_engine)
    resp = chat_client(user).post(
        "/api/chat/landing/artifacts/decide",
        json={"action": "obliterate", "proposal": _REDIS},
    )
    assert resp.status_code == 422


def test_artifact_endpoints_reject_another_users_job(
        isolated_engine, chat_client, monkeypatch):
    """Job-scoped chats stay owner-scoped, same as /history (issue #73)."""
    owner = _seed_user(isolated_engine, "Owner")
    intruder = _seed_user(isolated_engine, "Intruder")
    with Session(isolated_engine) as s:
        job = JobDescription(title="Eng", company="Acme", user_id=owner.user_id)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = str(job.job_id)
    _patch_extraction(monkeypatch, [_REDIS])

    client = chat_client(intruder)
    assert client.post(f"/api/chat/{job_id}/artifacts/propose").status_code == 403
    assert client.post(
        f"/api/chat/{job_id}/artifacts/decide",
        json={"action": "accept", "proposal": _REDIS},
    ).status_code == 403

    with Session(isolated_engine) as s:
        assert s.exec(select(Skill).where(Skill.name == "Redis")).first() is None


def test_decide_accept_supersedes_an_existing_row(isolated_engine, chat_client):
    user = _seed_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id, name="Cache", description="old"))
        s.commit()

    resp = chat_client(user).post(
        "/api/chat/landing/artifacts/decide",
        json={"action": "accept", "proposal": {
            "type": "project", "name": "Cache", "description": "Redis-backed layer",
            "decision": "supersede", "target": "project: Cache",
            "evidence": "The cache project is Redis-backed",
        }},
    )
    assert resp.status_code == 200
    assert "updated" in resp.json()["message"].lower()

    with Session(isolated_engine) as s:
        rows = s.exec(select(Project).where(Project.user_id == user.user_id)).all()
    assert len(rows) == 1, "supersede must update in place over the API too"
    assert rows[0].description == "Redis-backed layer"
