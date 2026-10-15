"""The evidence map's data (issue #136).

The map is a view over the knowledge graph: items on the left, skills (grouped by
area) on the right, and an edge for every (item, skill) pair that is linked, with
the bullets that name the skill. These pin what the UI relies on:

- one shared row per skill, however many items cite it;
- a user's view holds only that user's rows;
- the bullet numbers on an edge are the numbers the harness cites by (`#b<n>`),
  including past an empty bullet and over scrambled blurb storage (#327);
- a GitHub-only link is an edge with no citation;
- editing a bullet moves the links without moving any bullet's number.
"""
from uuid import uuid4

import pytest
from sqlmodel import Session, select

import services
from database.models import (
    Achievement, Education, Experience, JobDescription, JobSkill, Project, ProjectBlurb,
    Skill, User, UserSkill,
)
from knowledge_graph.areas import AREAS, skill_area
from knowledge_graph.evidence_view import build_evidence_view, skill_source_kind


def _user(engine, name="Mapper"):
    with Session(engine) as s:
        u = User(name=name, email=f"{name.lower()}_{uuid4().hex[:6]}@example.com")
        s.add(u)
        s.commit()
        s.refresh(u)
        return u.user_id


def _skill(engine, uid, name, category="Language", source="resume", proficiency=3):
    with Session(engine) as s:
        sk = Skill(name=name, category=category)
        s.add(sk)
        s.commit()
        s.refresh(sk)
        s.add(UserSkill(user_id=uid, skill_id=sk.skill_id, proficiency=proficiency,
                        evidence_source=source, confidence_score=0.9))
        s.commit()
        return sk.skill_id


def _project(engine, uid, name, description="", repo_url=None, **kw):
    with Session(engine) as s:
        p = Project(user_id=uid, name=name, description=description, repo_url=repo_url, **kw)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.project_id


def _experience(engine, uid, title, company, bullets=None, **kw):
    with Session(engine) as s:
        e = Experience(user_id=uid, title=title, company=company, bullets=bullets or [], **kw)
        s.add(e)
        s.commit()
        s.refresh(e)
        return e.experience_id


def _by_name(view):
    return {s["name"]: s for s in view["skills"]}


def _item(view, title):
    return next(i for i in view["items"] if i["title"] == title)


def _edge(view, item_title, skill_name):
    item = _item(view, item_title)
    skill = _by_name(view)[skill_name]
    return next((e for e in view["edges"]
                 if e["item_key"] == item["key"] and e["skill_key"] == skill["key"]), None)


# ── skill_area ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,category,area", [
    ("Python", "Programming Language", "Languages"),
    ("Python", None, "Languages"),
    ("TypeScript", "Skill", "Languages"),
    ("React", "Languages & Libraries", "Web & Apps"),
    ("FastAPI", "Framework", "Web & Apps"),
    ("PyTorch", "Languages & Libraries", "ML & AI"),
    ("scikit-learn", None, "ML & AI"),        # alias-mapped name
    ("sklearn", None, "ML & AI"),
    ("Pandas", "Languages & Libraries", "Data"),
    ("PostgreSQL", "Database", "Data"),
    ("Docker", "Tools", "Tools"),
    ("AWS", "Cloud", "Infrastructure & Cloud"),
    ("Agile", "Other", "Practices"),
    ("Contrastive learning", "Technique", "ML & AI"),   # unknown name, category decides
    ("Some Obscure Thing", "Cloud", "Infrastructure & Cloud"),
    ("Some Obscure Thing", "Uncategorized", "Other"),
    ("Some Obscure Thing", None, "Other"),
    ("", "", "Other"),
])
def test_skill_area_mapping(name, category, area):
    assert skill_area(name, category) == area


def test_skill_area_is_always_one_of_the_fixed_areas_and_deterministic():
    for name in ("Python", "x", "Kafka", "Leadership", "dbt", "CSS", "Looker"):
        for cat in (None, "", "Tools", "Data Engineering", "Weird Category"):
            assert skill_area(name, cat) in AREAS
            assert skill_area(name, cat) == skill_area(name, cat)


def test_the_category_match_is_by_word_not_substring():
    # "email" contains "ai"; it must not read as ML & AI.
    assert skill_area("Mailchimp", "Email marketing") == "Other"


@pytest.mark.parametrize("src,kind", [
    ("github:nathansso/RollAway", "github"), ("github:nathansso", "github"),
    ("linkedin", "linkedin"), ("manual", "manual"), ("chat:abc", "chat"), ("host", "host"),
    ("resume", "resume"), ("C:/Users/x/resume.pdf", "resume"), (None, "resume"), ("", "resume"),
])
def test_skill_source_kind(src, kind):
    assert skill_source_kind(src) == kind


# ── payload ───────────────────────────────────────────────────────────────────

def test_no_user_is_an_empty_view():
    view = services.get_evidence_view(None)
    assert view["items"] == [] and view["skills"] == [] and view["edges"] == []
    assert view["stats"]["skills"] == 0


def test_an_empty_profile_is_an_empty_view(isolated_engine):
    view = services.get_evidence_view(_user(isolated_engine))
    assert view["items"] == [] and view["edges"] == []
    assert set(view["stats"]) == {"items", "skills", "evidenced", "unevidenced",
                                  "github_only", "needs_text"}


def test_payload_shape(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python", proficiency=2)
    # A second link to the same skill row's name, from another source and at a higher level.
    with Session(isolated_engine) as s:
        sk = s.exec(select(Skill).where(Skill.name == "Python")).one()
        s.add(UserSkill(user_id=uid, skill_id=sk.skill_id, proficiency=5,
                        evidence_source="github:me/repo", is_core=True))
        s.commit()
    exp = _experience(isolated_engine, uid, "Analyst", "Acme", ["Shipped Python services"],
                      start_date="2023-06", end_date="2024-01", description="Backend")
    pid = _project(isolated_engine, uid, "Alpha", "Python pipeline", experience_id=exp)
    with Session(isolated_engine) as s:
        s.add(Education(user_id=uid, institution="UCSD", degree="B.S. Math", gpa="3.9"))
        s.add(Achievement(user_id=uid, title="First Place", issuer="HackX", project_id=pid))
        s.commit()

    view = services.get_evidence_view(uid)

    assert set(view) == {"items", "skills", "edges", "stats"}
    analyst, alpha, edu = _item(view, "Analyst"), _item(view, "Alpha"), _item(view, "B.S. Math")
    assert (analyst["kind"], alpha["kind"], edu["kind"]) == ("exp", "proj", "edu")
    assert analyst["org"] == "Acme" and analyst["start"] == "2023-06" and analyst["end"] == "2024-01"
    assert alpha["parent_key"] == analyst["key"]
    assert [a["title"] for a in alpha["achievements"]] == ["First Place"]
    assert edu["org"] == "UCSD" and edu["text_source"] == "fields" and edu["fields"]["gpa"] == "3.9"
    assert analyst["cite_key"] == "exp:analyst|acme" and alpha["cite_key"] == "proj:alpha"

    py = _by_name(view)["Python"]
    assert py["area"] == "Languages" and py["proficiency"] == 5 and py["is_core"] is True
    assert py["sources"] == ["github", "resume"]
    assert len({i["key"] for i in view["items"]} | {s["key"] for s in view["skills"]}) == 4


def test_stats_partition_the_skills(isolated_engine):
    uid = _user(isolated_engine)
    for name in ("Python", "Rust", "Go", "Haskell"):
        _skill(isolated_engine, uid, name)
    _skill(isolated_engine, uid, "Zig", source="github:me/zig-thing")
    _experience(isolated_engine, uid, "Analyst", "Acme", ["Wrote Python"])
    _project(isolated_engine, uid, "Zig thing", "a hackathon build",
             repo_url="https://github.com/me/zig-thing")
    _project(isolated_engine, uid, "Empty one")

    s = services.get_evidence_view(uid)["stats"]
    assert s["skills"] == 5 and s["items"] == 3
    assert s["evidenced"] == 1                       # Python, cited by a bullet
    assert s["github_only"] == 1                     # Zig, provenance only
    assert s["unevidenced"] == 3                     # Rust, Go, Haskell
    assert s["evidenced"] + s["github_only"] + s["unevidenced"] == s["skills"]
    assert s["needs_text"] == 1                      # the project with no text


# ── isolation ─────────────────────────────────────────────────────────────────

def test_a_view_holds_only_its_own_users_rows(isolated_engine):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _skill(isolated_engine, alice, "Python")
    _skill(isolated_engine, bob, "Rust")
    _project(isolated_engine, alice, "AliceProj", "Python work")
    _project(isolated_engine, bob, "BobProj", "Rust work", repo_url="https://github.com/b/p")
    _experience(isolated_engine, bob, "BobRole", "BobCo", ["Rust things"])
    with Session(isolated_engine) as s:
        s.add(Education(user_id=bob, institution="Bob U", degree="B.S."))
        s.add(Achievement(user_id=bob, title="Bob Award"))
        s.commit()

    view = services.get_evidence_view(alice)

    assert [i["title"] for i in view["items"]] == ["AliceProj"]
    assert [s["name"] for s in view["skills"]] == ["Python"]
    assert all(e["item_key"] == view["items"][0]["key"] for e in view["edges"])
    assert "Rust" not in str(view) and "Bob" not in str(view)


# ── shared hub, provenance ────────────────────────────────────────────────────

def test_two_projects_using_one_skill_share_a_single_skill_row(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _project(isolated_engine, uid, "Alpha", "data pipeline in Python")
    _project(isolated_engine, uid, "Beta", "Python scraper")

    view = services.get_evidence_view(uid)

    assert [s["name"] for s in view["skills"]] == ["Python"]
    key = view["skills"][0]["key"]
    assert sorted(e["item_key"] for e in view["edges"] if e["skill_key"] == key) == sorted(
        i["key"] for i in view["items"])
    assert all(e["cites"] == [0] for e in view["edges"])


def test_a_github_only_link_is_an_edge_without_a_citation(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "PyTorch", category="ML", source="github:nathansso/RollAway")
    _project(isolated_engine, uid, "RollAway", "a hackathon build",
             repo_url="https://github.com/nathansso/RollAway.git")
    _project(isolated_engine, uid, "Other", "something else", repo_url="https://github.com/x/y")

    view = services.get_evidence_view(uid)

    edge = _edge(view, "RollAway", "PyTorch")
    assert edge == {"item_key": _item(view, "RollAway")["key"],
                    "skill_key": _by_name(view)["PyTorch"]["key"], "cites": [], "github": True, "marks": []}
    assert _edge(view, "Other", "PyTorch") is None
    assert view["stats"]["github_only"] == 1 and view["stats"]["evidenced"] == 0


def test_provenance_marks_an_edge_the_text_already_made(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python", source="github:me/repo")
    _project(isolated_engine, uid, "Repo", "Python service", repo_url="https://github.com/me/repo")

    edge = _edge(services.get_evidence_view(uid), "Repo", "Python")
    assert edge["cites"] == [0] and edge["github"] is True


def test_the_view_does_not_change_the_graph_the_tailoring_reads(isolated_engine):
    from knowledge_graph.builder import SkillGraphBuilder
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "PyTorch", source="github:me/repo")
    _project(isolated_engine, uid, "Repo", "a build", repo_url="https://github.com/me/repo")

    services.get_evidence_view(uid)
    G = SkillGraphBuilder(uid).build_graph()
    assert not G.has_edge("Project:Repo", "Skill:PyTorch")


# ── bullet numbers ────────────────────────────────────────────────────────────

def test_cites_are_harness_ordinals_past_an_empty_bullet(isolated_engine):
    """The harness numbers an experience's non-empty bullets; the editor writes back raw
    positions. Both are in the payload, and they differ past an empty bullet."""
    from harness.executor import _KG

    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _skill(isolated_engine, uid, "Docker")
    _experience(isolated_engine, uid, "Engineer", "Acme",
                ["Wrote Python tools", "", "Containerised it with Docker"])

    view = services.get_evidence_view(uid)
    eng = _item(view, "Engineer")

    assert [(t["raw_index"], t["index"]) for t in eng["text"]] == [(0, 0), (1, None), (2, 1)]
    assert _edge(view, "Engineer", "Python")["cites"] == [0]
    assert _edge(view, "Engineer", "Docker")["cites"] == [1]
    harness = _KG(uid).source_bullets[eng["cite_key"]]
    for edge in view["edges"]:
        for n in edge["cites"]:
            assert next(t["text"] for t in eng["text"] if t["index"] == n) == harness[n]


def test_view_ordinals_agree_with_the_harness_over_scrambled_blurb_storage(isolated_engine):
    """#327's scrambled storage: the view's `#b<n>` text is the harness's, for a project
    whose blurb rows are stored in the opposite order, beside an experience with a gap."""
    from harness.executor import _KG
    from tests.test_ordering_determinism import (
        PROJ_BULLETS, PROJ_KEY, _ingest_project, _scramble_blurb_storage,
    )

    uid = _user(isolated_engine)
    for name in ("GPS", "latency", "dashboard", "replay", "Python"):
        _skill(isolated_engine, uid, name)
    _ingest_project(uid)
    _scramble_blurb_storage(isolated_engine, uid)
    _experience(isolated_engine, uid, "Engineer", "Acme", ["", "Wrote Python tools", ""])

    view = services.get_evidence_view(uid)
    kg = _KG(uid)

    proj = _item(view, "stable-keys")
    assert proj["cite_key"] == PROJ_KEY and proj["text_source"] == "blurbs"
    assert [t["text"] for t in proj["text"]] == PROJ_BULLETS
    assert [t["index"] for t in proj["text"]] == [0, 1, 2, 3]
    assert all(t["blurb_id"] for t in proj["text"])

    cited = 0
    for edge in view["edges"]:
        item = next(i for i in view["items"] if i["key"] == edge["item_key"])
        for n in edge["cites"]:
            cited += 1
            shown = next(t["text"] for t in item["text"] if t["index"] == n)
            assert shown == kg.source_bullets[item["cite_key"]][n]
    assert cited >= 5      # GPS, latency, dashboard, replay on the project; Python on the role
    assert _edge(view, "stable-keys", "latency")["cites"] == [1]
    assert _edge(view, "Engineer", "Python")["cites"] == [0]      # ordinal 0, raw position 1


def test_a_project_without_blurbs_uses_its_description_as_bullet_zero(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _project(isolated_engine, uid, "Alpha", "Python pipeline")
    _project(isolated_engine, uid, "Nameless Python thing")        # the name alone is no bullet

    view = services.get_evidence_view(uid)
    alpha = _item(view, "Alpha")
    assert alpha["text_source"] == "description"
    assert [(t["raw_index"], t["index"], t["blurb_id"]) for t in alpha["text"]] == [(0, 0, None)]
    assert _edge(view, "Alpha", "Python")["cites"] == [0]
    named = _item(view, "Nameless Python thing")
    assert named["text_source"] == "none" and named["text"] == []
    assert _edge(view, "Nameless Python thing", "Python")["cites"] == []   # linked by name only


def test_an_edge_says_which_words_of_each_cited_bullet_name_the_skill(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "PyTorch", category="ML")
    _skill(isolated_engine, uid, "Java")
    text = "Trained a model with torch, then PyTorch Lightning."
    _experience(isolated_engine, uid, "Engineer", "Acme",
                ["Led a team", text, "Wrote JavaScript, not Java"])

    view = services.get_evidence_view(uid)

    torch = _edge(view, "Engineer", "PyTorch")
    assert torch["cites"] == [1]
    [spans] = torch["marks"]
    assert [text[a:b] for a, b in spans] == ["torch", "PyTorch"]      # the alias is marked too
    java = _edge(view, "Engineer", "Java")
    assert java["cites"] == [2] and [("Wrote JavaScript, not Java"[a:b]) for a, b in java["marks"][0]] == ["Java"]


def test_spans_agree_with_mentions():
    from agents.skill_matching import SkillMatcher
    cases = [("Java", "built a JavaScript app"), ("Java", "wrote Java and Java"),
             ("Go", "Go is fast"), ("Go", "rewrote it in Go"), ("C++", "C++ kernels"),
             ("R", "R&D and Research"), ("Node.js", "a Node.js service. Used Node.js")]
    for name, text in cases:
        m = SkillMatcher(name)
        assert bool(m.spans(text)) is m.mentions(text), (name, text)
    assert SkillMatcher("Java").spans("Java and Java") == [(0, 4), (9, 13)]
    assert SkillMatcher("Java").spans("") == []


# ── editing ───────────────────────────────────────────────────────────────────

def _blurbs(engine, project_id):
    from uuid import UUID
    with Session(engine) as s:
        return s.exec(select(ProjectBlurb).where(
            ProjectBlurb.project_id == UUID(str(project_id)))).all()


def test_editing_an_experience_bullet_moves_the_links(isolated_engine):
    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Python")
    _skill(isolated_engine, uid, "Rust")
    eid = _experience(isolated_engine, uid, "Engineer", "Acme", ["Wrote Python tools", "Led a team"])
    assert _edge(services.get_evidence_view(uid), "Engineer", "Rust") is None

    services.update_experience(uid, str(eid), {"bullets": ["Wrote Python tools", "Rewrote it in Rust"]})

    view = services.get_evidence_view(uid)
    assert _edge(view, "Engineer", "Rust")["cites"] == [1]
    assert _edge(view, "Engineer", "Python")["cites"] == [0]


def test_a_blurb_edit_changes_the_text_and_never_the_position(isolated_engine):
    from tests.test_ordering_determinism import PROJ_BULLETS, _ingest_project, _scramble_blurb_storage

    uid = _user(isolated_engine)
    _skill(isolated_engine, uid, "Kafka")
    _ingest_project(uid)
    _scramble_blurb_storage(isolated_engine, uid)
    view = services.get_evidence_view(uid)
    proj = _item(view, "stable-keys")
    assert _edge(view, "stable-keys", "Kafka") is None
    target = proj["text"][2]
    before = {b.blurb_id: (b.seq, b.style) for b in _blurbs(isolated_engine, proj["id"])}
    from uuid import UUID
    with Session(isolated_engine) as s:
        s.get(Project, UUID(proj["id"])).manually_edited = False
        s.commit()

    row = services.update_project_blurb(uid, proj["id"], target["blurb_id"],
                                       "  Streamed the dashboard feed through Kafka.  ")

    assert row["content"] == "Streamed the dashboard feed through Kafka."
    after = {b.blurb_id: (b.seq, b.style) for b in _blurbs(isolated_engine, proj["id"])}
    assert after == before                                        # seq and style untouched
    with Session(isolated_engine) as s:
        assert s.get(Project, UUID(proj["id"])).manually_edited is True
    view = services.get_evidence_view(uid)
    texts = [t["text"] for t in _item(view, "stable-keys")["text"]]
    assert texts == PROJ_BULLETS[:2] + ["Streamed the dashboard feed through Kafka."] + PROJ_BULLETS[3:]
    assert _edge(view, "stable-keys", "Kafka")["cites"] == [2]


def test_a_blurb_edit_is_owner_only_and_needs_text(isolated_engine):
    from tests.test_ordering_determinism import _ingest_project

    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _ingest_project(alice)
    _ingest_project(bob, name="bobs")
    proj = _item(services.get_evidence_view(alice), "stable-keys")
    bobs = _item(services.get_evidence_view(bob), "bobs")
    blurb = proj["text"][0]["blurb_id"]

    assert services.update_project_blurb(bob, proj["id"], blurb, "hijack") is None
    assert services.update_project_blurb(alice, proj["id"], str(uuid4()), "x") is None
    assert services.update_project_blurb(alice, "not-a-uuid", blurb, "x") is None
    # A bullet of another project, under this project's id.
    assert services.update_project_blurb(alice, proj["id"], bobs["text"][0]["blurb_id"], "x") is None
    assert services.update_project_blurb(None, proj["id"], blurb, "x") is None
    with pytest.raises(ValueError):
        services.update_project_blurb(alice, proj["id"], blurb, "   ")
    assert _item(services.get_evidence_view(alice), "stable-keys")["text"][0]["text"] != "hijack"


# ── HTTP ──────────────────────────────────────────────────────────────────────

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


def test_graph_view_route_serves_the_callers_view_only(isolated_engine, client_for):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _skill(isolated_engine, alice, "Python")
    _project(isolated_engine, alice, "AliceProj", "Python work")
    _project(isolated_engine, bob, "BobProj", "other work")

    r = client_for(alice).get("/api/profile/graph/view")

    assert r.status_code == 200
    body = r.json()
    assert [i["title"] for i in body["items"]] == ["AliceProj"]
    assert body["stats"]["evidenced"] == 1


def test_blurb_route_edits_content_and_maps_errors(isolated_engine, client_for):
    from tests.test_ordering_determinism import _ingest_project

    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    _ingest_project(alice)
    proj = _item(services.get_evidence_view(alice), "stable-keys")
    blurb = proj["text"][1]
    url = f"/api/profile/projects/{proj['id']}/blurbs/{blurb['blurb_id']}"

    ok = client_for(alice).patch(url, json={"content": "Cut latency 4x."})
    assert ok.status_code == 200 and ok.json()["content"] == "Cut latency 4x."
    assert ok.json()["seq"] == 1
    assert client_for(alice).patch(url, json={"content": " "}).status_code == 400
    assert client_for(bob).patch(url, json={"content": "nope"}).status_code == 404
    assert client_for(alice).patch(url, json={}).status_code == 422


def _job(engine, uid, skills):
    with Session(engine) as s:
        job = JobDescription(title="MLE", company="Co", description="", user_id=uid)
        s.add(job)
        s.commit()
        s.refresh(job)
        for name, required, weight in skills:
            sk = s.exec(select(Skill).where(Skill.name == name)).first() or Skill(
                name=name, category="Tools" if name == "Docker" else None)
            s.add(sk)
            s.commit()
            s.refresh(sk)
            s.add(JobSkill(job_id=job.job_id, skill_id=sk.skill_id, required=required, weight=weight))
        s.commit()
        return str(job.job_id)


def test_job_skills_route_lists_what_a_job_asks_for_with_areas(isolated_engine, client_for):
    alice = _user(isolated_engine, "Alice")
    jid = _job(isolated_engine, alice, [("Docker", False, 0.5), ("Python", True, 2.0),
                                        ("PyTorch", True, 1.0)])
    empty = _job(isolated_engine, alice, [])

    r = client_for(alice).get(f"/api/jobs/{jid}/skills")

    assert r.status_code == 200
    assert r.json() == [
        {"name": "Python", "required": True, "weight": 2.0, "area": "Languages"},
        {"name": "PyTorch", "required": True, "weight": 1.0, "area": "ML & AI"},
        {"name": "Docker", "required": False, "weight": 0.5, "area": "Tools"},
    ]
    assert client_for(alice).get(f"/api/jobs/{empty}/skills").json() == []


def test_job_skills_route_is_owner_only(isolated_engine, client_for):
    alice, bob = _user(isolated_engine, "Alice"), _user(isolated_engine, "Bob")
    jid = _job(isolated_engine, alice, [("Python", True, 1.0)])

    assert client_for(bob).get(f"/api/jobs/{jid}/skills").status_code == 404
    assert client_for(alice).get(f"/api/jobs/{uuid4()}/skills").status_code == 404
    assert client_for(alice).get("/api/jobs/not-a-uuid/skills").status_code == 404
