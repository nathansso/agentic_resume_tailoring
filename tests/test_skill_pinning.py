"""Tests for pinned 'core' skills (UserSkill.is_core) — issue #54 Phase 3."""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from conftest import _seed_user_and_skill
from database.models import User, Skill, UserSkill
import services
from agents.skill_scorer import MAX_SKILLS, rank_and_select_skills


def _skill(name, category="Other", proficiency=3, confidence=0.5, is_core=False):
    return {
        "name": name,
        "category": category,
        "proficiency": proficiency,
        "confidence": confidence,
        "is_core": is_core,
    }


# ── Scorer: pinned skills bypass the cap ────────────────────────────────────────

def test_pinned_skill_always_selected_even_when_irrelevant():
    # 20 JD-relevant skills fill the cap; a pinned, JD-irrelevant skill must still
    # appear because it is pinned.
    relevant = [_skill(f"Rel{i}", proficiency=3) for i in range(20)]
    pinned = _skill("PinnedNiche", proficiency=1, is_core=True)
    skills = relevant + [pinned]
    jd = " ".join(f"Rel{i}" for i in range(20))  # mentions only the relevant skills
    selected = rank_and_select_skills(skills, jd, matched_skills={})
    names = {s["name"] for s in selected}
    assert "PinnedNiche" in names


def test_pin_replaces_inferred_floor():
    # A low-proficiency, JD-irrelevant skill is excluded normally, but included
    # once pinned — demonstrating the pin overrides the inferred proficiency floor.
    relevant = [_skill(f"Rel{i}", proficiency=3) for i in range(20)]
    jd = " ".join(f"Rel{i}" for i in range(20))

    unpinned = relevant + [_skill("Niche", proficiency=1, is_core=False)]
    without = {s["name"] for s in rank_and_select_skills(unpinned, jd, matched_skills={})}
    assert "Niche" not in without

    pinned = relevant + [_skill("Niche", proficiency=1, is_core=True)]
    with_pin = {s["name"] for s in rank_and_select_skills(pinned, jd, matched_skills={})}
    assert "Niche" in with_pin


def test_pinned_carries_through_scored_items():
    skills = [_skill("Python", is_core=True), _skill("Java")]
    selected = rank_and_select_skills(skills, "Python Java role", matched_skills={})
    # short list (<= MIN_SKILLS) returns all; both present regardless
    assert {s["name"] for s in selected} == {"Python", "Java"}


# ── Services: pin/unpin control ────────────────────────────────────────────────

def test_set_skill_core_pins_and_unpins(isolated_engine):
    seeded = _seed_user_and_skill(isolated_engine)
    uid = seeded.user_id

    msg = services.set_skill_core(uid, "python", True)  # case-insensitive
    assert "Pinned" in msg
    skills = {s["name"]: s for s in services.get_skills(uid)}
    assert skills["Python"]["is_core"] is True

    msg = services.set_skill_core(uid, "Python", False)
    assert "Unpinned" in msg
    skills = {s["name"]: s for s in services.get_skills(uid)}
    assert skills["Python"]["is_core"] is False


def test_set_skill_core_unknown_skill(isolated_engine):
    seeded = _seed_user_and_skill(isolated_engine)
    msg = services.set_skill_core(seeded.user_id, "Nonexistent", True)
    assert "not in your profile" in msg


def test_get_skills_includes_is_core_field(isolated_engine):
    seeded = _seed_user_and_skill(isolated_engine)
    rows = services.get_skills(seeded.user_id)
    assert rows and "is_core" in rows[0]


# ── Web endpoint ───────────────────────────────────────────────────────────────

@pytest.fixture()
def web_client(isolated_engine, monkeypatch):
    import database.db as db_module
    import web.routers.profile_router as profile_router_module
    monkeypatch.setattr(db_module, "engine", isolated_engine)
    monkeypatch.setattr(profile_router_module, "engine", isolated_engine)

    with Session(isolated_engine) as s:
        user = User(name="T", email="pin@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        uid = user.user_id
        skill = Skill(name="Python", category="language")
        s.add(skill)
        s.commit()
        s.refresh(skill)
        s.add(UserSkill(user_id=uid, skill_id=skill.skill_id, proficiency=3))
        s.commit()
        user = s.get(User, uid)

    from web.app import create_app
    import web.auth as web_auth_module
    app = create_app()
    app.dependency_overrides[web_auth_module.get_current_user] = lambda: user
    return TestClient(app), user


def test_set_skill_core_endpoint(web_client):
    tc, _ = web_client
    resp = tc.post("/api/profile/skills/core", json={"name": "Python", "is_core": True})
    assert resp.status_code == 200
    assert "Pinned" in resp.json()["result"]
    skills = tc.get("/api/profile/skills").json()
    assert any(s["name"] == "Python" and s["is_core"] for s in skills)
