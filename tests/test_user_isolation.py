"""Cross-user isolation regression tests (issue #73).

Three leakage mechanisms are pinned here:
  1. SkillGraphBuilder built one graph from every user's rows.
  2. get_active_profile() resolved from a server-global pointer file that
     concurrent web requests raced over.
  3. Landing-context chat history (job_id=None) was shared by all users.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session

import database.user_utils as user_utils_module
import services as services_module
from database.models import Achievement, Experience, Project, Skill, User, UserSkill
from knowledge_graph.builder import SkillGraphBuilder


def _seed_user(engine, name: str) -> User:
    with Session(engine) as s:
        user = User(name=name, email=f"{name.lower()}_{uuid4().hex[:8]}@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _seed_profile_rows(engine, user_id, skill_name: str, project_name: str, company: str):
    """One skill + one project + one experience, with the project/experience
    text mentioning the skill so the builder links them."""
    with Session(engine) as s:
        skill = Skill(name=skill_name, category="language")
        s.add(skill)
        s.commit()
        s.refresh(skill)
        s.add(UserSkill(user_id=user_id, skill_id=skill.skill_id, confidence_score=0.9))
        s.add(Project(
            user_id=user_id, name=project_name,
            description=f"Built with {skill_name}.",
        ))
        s.add(Experience(
            user_id=user_id, title="Engineer", company=company,
            description=f"Used {skill_name} daily.",
        ))
        s.commit()


# ── 1. Knowledge graph scoping ────────────────────────────────────────────────

def test_graph_contains_only_own_user_rows(isolated_engine):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    _seed_profile_rows(isolated_engine, alice.user_id, "Python", "AliceProj", "AliceCorp")
    _seed_profile_rows(isolated_engine, bob.user_id, "Java", "BobProj", "BobCorp")

    g_alice = SkillGraphBuilder(alice.user_id).build_graph()
    g_bob = SkillGraphBuilder(bob.user_id).build_graph()

    assert "Skill:Python" in g_alice and "Project:AliceProj" in g_alice
    assert "Skill:Java" not in g_alice
    assert "Project:BobProj" not in g_alice
    assert "Experience:BobCorp - Engineer" not in g_alice

    assert "Skill:Java" in g_bob and "Project:BobProj" in g_bob
    assert "Skill:Python" not in g_bob
    assert "Project:AliceProj" not in g_bob


def test_graph_edges_stay_within_user(isolated_engine):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    _seed_profile_rows(isolated_engine, alice.user_id, "Python", "AliceProj", "AliceCorp")
    # Bob's project text mentions Alice's skill name — must not create an edge
    # in Alice's graph because Bob's project isn't in it at all.
    with Session(isolated_engine) as s:
        s.add(Project(user_id=bob.user_id, name="BobPythonProj",
                      description="Also built with Python."))
        s.commit()

    g_alice = SkillGraphBuilder(alice.user_id).build_graph()
    assert "Project:BobPythonProj" not in g_alice
    assert g_alice.has_edge("Project:AliceProj", "Skill:Python")


def test_graph_summary_scoped_per_user(isolated_engine):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    _seed_profile_rows(isolated_engine, alice.user_id, "Python", "AliceProj", "AliceCorp")

    summary_bob = services_module.get_graph_summary(bob.user_id)
    assert summary_bob["top_skills"] == []
    assert summary_bob["by_category"] == {}

    summary_alice = services_module.get_graph_summary(alice.user_id)
    assert any(s["name"] == "Python" for s in summary_alice["top_skills"])


def test_achievements_scoped_per_user(isolated_engine):
    """One user's achievements never surface for another (FK-scoped, issue #73)."""
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    with Session(isolated_engine) as s:
        s.add(Achievement(user_id=alice.user_id, title="Dean's List", date="2023"))
        s.commit()

    assert services_module.get_achievements(bob.user_id) == []
    alice_rows = services_module.get_achievements(alice.user_id)
    assert [a["title"] for a in alice_rows] == ["Dean's List"]


def test_matcher_indirect_match_without_builder_is_safe():
    """_check_indirect_match before match() (no user bound) returns no match."""
    from agents.matcher import SkillMatcherAgent
    agent = SkillMatcherAgent()
    assert agent._check_indirect_match("TypeScript", {"react"}) == ""


# ── 2. Request-scoped user binding ────────────────────────────────────────────

def test_request_binding_wins_over_pointer_file(isolated_engine):
    """The regression at the heart of #73: the pointer file names user A, but
    the request context is bound to user B — B must be resolved."""
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    isolated_engine._test_profile_file.write_text(str(alice.user_id))

    try:
        user_utils_module.set_request_user(bob.user_id)
        active = user_utils_module.get_active_profile()
        assert active is not None and active.user_id == bob.user_id
    finally:
        user_utils_module.set_request_user(None)

    # Cleared binding falls back to the CLI pointer file.
    active = user_utils_module.get_active_profile()
    assert active is not None and active.user_id == alice.user_id


def test_no_binding_and_no_file_returns_none(isolated_engine):
    user_utils_module.set_request_user(None)
    assert user_utils_module.get_active_profile() is None


# ── 3. Landing chat history scoping ───────────────────────────────────────────

def test_landing_chat_history_isolated_between_users(isolated_engine):
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")

    try:
        user_utils_module.set_request_user(alice.user_id)
        services_module.save_chat_message(None, "user", "alice landing message")
        user_utils_module.set_request_user(bob.user_id)
        services_module.save_chat_message(None, "user", "bob landing message")

        history_bob = services_module.load_chat_history(None)
        assert [m["content"] for m in history_bob] == ["bob landing message"]
    finally:
        user_utils_module.set_request_user(None)

    # Explicit user_id parameter (router path) scopes the same way.
    history_alice = services_module.load_chat_history(None, user_id=alice.user_id)
    assert [m["content"] for m in history_alice] == ["alice landing message"]


def test_landing_history_hides_legacy_unowned_rows_from_users(isolated_engine):
    """Pre-#73 landing rows have user_id NULL; an authenticated user must not
    see them (they may belong to anyone)."""
    alice = _seed_user(isolated_engine, "Alice")
    # Legacy row: saved with no acting user bound (user_id NULL).
    user_utils_module.set_request_user(None)
    services_module.save_chat_message(None, "user", "legacy shared message")

    history = services_module.load_chat_history(None, user_id=alice.user_id)
    assert history == []


def test_job_chat_history_unaffected(isolated_engine):
    """Job-scoped history still round-trips (scoped by job, owner-checked at
    the router)."""
    from database.models import JobDescription
    alice = _seed_user(isolated_engine, "Alice")
    with Session(isolated_engine) as s:
        job = JobDescription(title="SWE", company="Acme", user_id=alice.user_id)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = str(job.job_id)

    try:
        user_utils_module.set_request_user(alice.user_id)
        services_module.save_chat_message(job_id, "user", "about this job")
    finally:
        user_utils_module.set_request_user(None)

    history = services_module.load_chat_history(job_id)
    assert [m["content"] for m in history] == ["about this job"]


# ── Router: job history ownership ─────────────────────────────────────────────

@pytest.fixture()
def history_client(isolated_engine, monkeypatch):
    """TestClient factory with isolated DB and auth overridden per user."""
    import database.db as db_module
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    import web.routers.chat_router as chat_router_module
    monkeypatch.setattr(chat_router_module, "engine", isolated_engine)

    from fastapi.testclient import TestClient
    from web.app import create_app
    import web.auth as web_auth_module

    def _make(user: User) -> TestClient:
        app = create_app()
        app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
        return TestClient(app, raise_server_exceptions=True)

    return _make


def test_get_history_rejects_other_users_job(isolated_engine, history_client):
    from database.models import JobDescription
    alice = _seed_user(isolated_engine, "Alice")
    bob = _seed_user(isolated_engine, "Bob")
    with Session(isolated_engine) as s:
        job = JobDescription(title="SWE", company="Acme", user_id=alice.user_id)
        s.add(job)
        s.commit()
        s.refresh(job)
        job_id = str(job.job_id)

    assert history_client(bob).get(f"/api/chat/{job_id}/history").status_code == 403
    assert history_client(alice).get(f"/api/chat/{job_id}/history").status_code == 200


def test_get_history_unknown_job_404(isolated_engine, history_client):
    alice = _seed_user(isolated_engine, "Alice")
    client = history_client(alice)
    assert client.get(f"/api/chat/{uuid4()}/history").status_code == 404
    assert client.get("/api/chat/not-a-uuid/history").status_code == 404


# ── 4. Fail-closed user resolution (issue #131) ───────────────────────────────
#
# #73 fixed the call sites by binding a request-scoped ContextVar. The fallback
# underneath them stayed fail-open: it adopted an arbitrary select(User).limit(1)
# row and wrote that into the global pointer file, so a single unbound call site
# would misattribute data and poison every later lookup. These pin it shut.


def test_require_active_user_raises_instead_of_picking_a_user(isolated_engine):
    """With users in the DB but nothing bound, resolution must fail, not guess."""
    _seed_user(isolated_engine, "Alice")
    _seed_user(isolated_engine, "Bob")
    user_utils_module.set_request_user(None)

    with pytest.raises(user_utils_module.NoActiveUserError):
        user_utils_module.require_active_user()


def test_require_active_user_never_writes_the_global_pointer(isolated_engine):
    """A failed resolution must not leave a pointer that poisons later lookups."""
    _seed_user(isolated_engine, "Alice")
    user_utils_module.set_request_user(None)
    pointer = isolated_engine._test_profile_file

    with pytest.raises(user_utils_module.NoActiveUserError):
        user_utils_module.require_active_user()

    assert not pointer.exists(), "fail-closed path wrote the global profile pointer"


def test_require_active_user_returns_the_bound_user(isolated_engine):
    alice = _seed_user(isolated_engine, "Alice")
    _seed_user(isolated_engine, "Bob")
    try:
        user_utils_module.set_request_user(alice.user_id)
        assert user_utils_module.require_active_user().user_id == alice.user_id
    finally:
        user_utils_module.set_request_user(None)


def test_resume_parser_agent_refuses_to_run_unbound(isolated_engine, monkeypatch):
    """ResumeParserAgent writes rows under self.user — unbound must raise, not
    silently attribute Alice's resume to Bob."""
    import agents.parser as parser_module
    _seed_user(isolated_engine, "Alice")
    _seed_user(isolated_engine, "Bob")
    monkeypatch.setattr(parser_module, "get_llm", lambda **_kw: object())
    user_utils_module.set_request_user(None)

    with pytest.raises(user_utils_module.NoActiveUserError):
        parser_module.ResumeParserAgent()


def test_pipeline_ingest_node_refuses_to_run_unbound(isolated_engine):
    """ingest_resume_node sets state['user_id'] for every downstream node."""
    import graph.pipeline as pipeline_module
    _seed_user(isolated_engine, "Alice")
    user_utils_module.set_request_user(None)

    with pytest.raises(user_utils_module.NoActiveUserError):
        pipeline_module.ingest_resume_node({"resume_path": ""})


def test_cli_user_helper_still_creates_and_binds_on_first_run(isolated_engine):
    """CLI behavior is unchanged: an empty DB gets a default profile and pointer."""
    user_utils_module.set_request_user(None)
    pointer = isolated_engine._test_profile_file
    assert not pointer.exists()

    user = user_utils_module.get_or_create_cli_user()

    assert user is not None
    assert pointer.exists() and pointer.read_text().strip() == str(user.user_id)


def test_cli_user_helper_adopts_the_existing_profile(isolated_engine):
    """Second CLI run resolves the same user rather than creating another."""
    user_utils_module.set_request_user(None)
    first = user_utils_module.get_or_create_cli_user()
    second = user_utils_module.get_or_create_cli_user()
    assert first.user_id == second.user_id
