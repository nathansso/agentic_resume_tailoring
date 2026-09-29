"""Project context: Project -PART_OF-> Experience | Education, and
Achievement -AWARDED_FOR-> Project.

Covers the suggestion rules (pure, never write), the only write paths, the graph
edges and what they add to evidence, link integrity through heal merges and
deletes, the harness tools, and the tailoring inputs, including that a user
with no confirmed links sees exactly what they saw before.
"""

from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from agents import project_context as pc
from database.models import (
    Achievement, Education, Experience, Project, Skill, UserSkill,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def world(isolated_engine):
    """One user with two roles, two degrees, work/course/personal repos, an award,
    and one skill (XGBoost) that only a work project names."""
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)
    uid = user.user_id
    with Session(isolated_engine) as s:
        xgb = Skill(name="XGBoost", category="Framework")
        s.add(xgb)
        s.add(Experience(user_id=uid, title="Data Science Intern", company="IDX Exchange",
                         description="Forecasting home prices", bullets=["Built pipelines"]))
        s.add(Experience(user_id=uid, title="AI Engineer", company="Agos",
                         description="Evaluation harnesses", bullets=[]))
        s.add(Education(user_id=uid, institution="UC San Diego", degree="M.S. Data Science"))
        s.add(Education(user_id=uid, institution="UC San Diego",
                        degree="B.S. Mathematics & Economics"))
        s.add(Project(user_id=uid, name="idx_ds38", description="Price model with XGBoost"))
        s.add(Project(user_id=uid, name="agos-memory", description="Decision kernel"))
        s.add(Project(user_id=uid, name="dsc207miniproject", description="Coursework"))
        s.add(Project(user_id=uid, name="dsc30-pa0", description="Coursework"))
        s.add(Project(user_id=uid, name="Atrium", description="Learn anything with one search"))
        s.add(Achievement(user_id=uid, title="1st Place Overall", issuer="Devnovate"))
        s.commit()
        s.refresh(xgb)
        s.add(UserSkill(user_id=uid, skill_id=xgb.skill_id, proficiency=4,
                        evidence_source="resume", confidence_score=0.9))
        s.commit()
    return uid


IDX = "exp:data science intern|idx exchange"
AGOS = "exp:ai engineer|agos"
MS = "edu:uc san diego|m.s. data science"
BS = "edu:uc san diego|b.s. mathematics & economics"


def _project(engine, name):
    with Session(engine) as s:
        return s.exec(select(Project).where(Project.name == name)).one()


def _by_project(suggestions):
    return {s["project"]: [c["context_key"] for c in s["suggestions"]] for s in suggestions}


# ── rules (pure) ──────────────────────────────────────────────────────────────

def test_course_code_parses_repo_names():
    assert pc.course_code("dsc30-pa0") == ("dsc", 30)
    assert pc.course_code("DSC-80-Project-Notebook") == ("dsc", 80)
    assert pc.course_code("dsc207miniproject") == ("dsc", 207)
    assert pc.course_code("dsc140b") == ("dsc", 140)
    assert pc.course_code("Of Mice and Medicine (2025)") is None
    assert pc.course_code("python3-tools") is None
    assert pc.course_code("portfolio") is None


def test_degree_level():
    assert pc.degree_level("M.S. Data Science") == "graduate"
    assert pc.degree_level("Ph.D. Physics") == "graduate"
    assert pc.degree_level("B.S. Mathematics & Economics, Minor in Data Science") == "undergraduate"
    assert pc.degree_level("Certificate") == ""


def test_suggestions_by_rule(world, isolated_engine):
    got = _by_project(pc.suggest_for_user(isolated_engine, world))
    assert got["idx_ds38"] == [IDX]          # employer beats the 'ds38' course code
    assert got["agos-memory"] == [AGOS]
    assert got["dsc207miniproject"] == [MS]  # 200-level -> graduate degree
    assert got["dsc30-pa0"] == [BS]          # below 200 -> undergraduate degree
    assert got["Atrium"] == []               # no rule fires: the user must say
    reasons = {s["project"]: s["suggestions"] for s in pc.suggest_for_user(isolated_engine, world)}
    assert reasons["idx_ds38"][0]["rule"] == "employer"
    assert reasons["dsc30-pa0"][0]["rule"] == "course_code"


def test_description_naming_the_employer_is_a_match():
    got = pc.suggest_contexts(
        [{"name": "price-model", "description": "Built at IDX Exchange for listings"}],
        [{"title": "Data Science Intern", "company": "IDX Exchange"}], [])
    assert [c["context_key"] for c in got[0]["suggestions"]] == [IDX]


def test_single_degree_is_the_course_fallback():
    got = pc.suggest_contexts([{"name": "cse101"}], [],
                              [{"institution": "State U", "degree": "Certificate"}])
    assert got[0]["suggestions"][0]["context_key"] == "edu:state u|certificate"


def test_suggest_never_writes_and_skips_reviewed(world, isolated_engine):
    pc.suggest_for_user(isolated_engine, world)
    assert _project(isolated_engine, "idx_ds38").context_status == pc.UNREVIEWED
    pc.set_project_context(isolated_engine, world, "Atrium", "personal")
    names = {s["project"] for s in pc.suggest_for_user(isolated_engine, world)}
    assert "Atrium" not in names
    reviewed = {s["project"]: s for s in pc.suggest_for_user(isolated_engine, world, True)}
    assert reviewed["Atrium"]["context_status"] == pc.PERSONAL


# ── writes ────────────────────────────────────────────────────────────────────

def test_set_project_context_links_and_clears(world, isolated_engine):
    out = pc.set_project_context(isolated_engine, world, "proj:idx_ds38", IDX)
    assert out == {"project_key": "proj:idx_ds38", "context_status": "linked", "context_key": IDX}
    p = _project(isolated_engine, "idx_ds38")
    assert p.experience_id is not None and p.education_id is None

    # Re-linking to a degree clears the role: at most one context.
    pc.set_project_context(isolated_engine, world, "idx_ds38", MS)
    p = _project(isolated_engine, "idx_ds38")
    assert p.experience_id is None and p.education_id is not None

    pc.set_project_context(isolated_engine, world, "idx_ds38", "unreviewed")
    p = _project(isolated_engine, "idx_ds38")
    assert (p.experience_id, p.education_id, p.context_status) == (None, None, "unreviewed")


def test_set_project_context_errors(world, isolated_engine):
    assert pc.set_project_context(isolated_engine, world, "proj:nope", "personal")["error"]["code"] == "not_found"
    err = pc.set_project_context(isolated_engine, world, "idx_ds38", "exp:ceo|nowhere")["error"]
    assert err["code"] == "not_found" and IDX in err["suggestions"]
    assert pc.set_project_context(isolated_engine, world, "idx_ds38", "school")["error"]["code"] == "invalid_context"


def test_cannot_link_into_another_users_rows(world, isolated_engine):
    from database.models import User
    with Session(isolated_engine) as s:
        other = User(name="Other", email="other@example.com")
        s.add(other)
        s.commit()
        s.refresh(other)
        s.add(Experience(user_id=other.user_id, title="Intern", company="Elsewhere"))
        s.commit()
        other_id = other.user_id
    err = pc.set_project_context(isolated_engine, world, "idx_ds38", "exp:intern|elsewhere")
    assert err["error"]["code"] == "not_found"
    # And the other user cannot address this user's project at all.
    assert pc.set_project_context(isolated_engine, other_id, "idx_ds38", "personal")["error"]["code"] == "not_found"


def test_link_achievement(world, isolated_engine):
    out = pc.link_achievement(isolated_engine, world, "ach:1st place overall", "Atrium")
    assert out == {"achievement_key": "ach:1st place overall", "project_key": "proj:atrium"}
    out = pc.link_achievement(isolated_engine, world, "1st Place Overall", None)
    assert out["project_key"] is None
    assert pc.link_achievement(isolated_engine, world, "ach:nope", None)["error"]["code"] == "not_found"


# ── the graph ────────────────────────────────────────────────────────────────

def _graph(uid):
    from knowledge_graph.builder import SkillGraphBuilder
    b = SkillGraphBuilder(uid)
    b.build_graph()
    return b


def test_graph_edges(world, isolated_engine):
    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    pc.set_project_context(isolated_engine, world, "dsc207miniproject", MS)
    pc.link_achievement(isolated_engine, world, "1st Place Overall", "Atrium")
    b = _graph(world)
    rel = {(u, v): d["relation"] for u, v, d in b.graph.edges(data=True)}
    assert rel[("Project:idx_ds38", "Experience:IDX Exchange - Data Science Intern")] == "PART_OF"
    assert rel[("Project:dsc207miniproject", "Education:UC San Diego - M.S. Data Science")] == "PART_OF"
    assert rel[("Achievement:1st Place Overall", "Project:Atrium")] == "AWARDED_FOR"
    assert b.get_project_context("idx_ds38") == {
        "type": "Experience", "title": "Data Science Intern", "company": "IDX Exchange"}
    assert b.get_project_context("Atrium") is None
    assert b.get_awards_for_project("Atrium") == ["1st Place Overall"]
    # A context node is a successor of the project, but never one of its skills.
    assert b.get_skills_for_project("idx_ds38") == ["XGBoost"]


def test_role_evidences_skill_through_its_project(world, isolated_engine):
    before = _graph(world).evidence_for_skills(["XGBoost"])
    # Bullet provenance (#233) is additive: the project's description is its bullet 0.
    assert before == {"XGBoost": {"projects": ["idx_ds38"], "experiences": [], "bullets": [
        {"key": "proj:idx_ds38", "index": 0, "cite": "proj:idx_ds38#b0"}]}}

    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    after = _graph(world).evidence_for_skills(["XGBoost"])
    assert after["XGBoost"]["experiences"] == []   # the role's own text never names it
    assert after["XGBoost"]["experiences_via_projects"] == [
        {"title": "Data Science Intern", "company": "IDX Exchange", "project": "idx_ds38"}]


def test_coursework_link_adds_no_role_evidence(world, isolated_engine):
    pc.set_project_context(isolated_engine, world, "idx_ds38", MS)
    ev = _graph(world).evidence_for_skills(["XGBoost"])
    assert "experiences_via_projects" not in ev["XGBoost"]


# ── integrity ────────────────────────────────────────────────────────────────

def test_deleting_the_role_returns_its_projects_to_unreviewed(world, isolated_engine):
    import services
    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    exp_id = _project(isolated_engine, "idx_ds38").experience_id
    assert services.delete_experience(world, str(exp_id))
    p = _project(isolated_engine, "idx_ds38")
    assert (p.experience_id, p.context_status) == (None, "unreviewed")


def test_deleting_a_degree_unlinks_its_projects(world, isolated_engine):
    import services
    pc.set_project_context(isolated_engine, world, "dsc30-pa0", BS)
    edu_id = _project(isolated_engine, "dsc30-pa0").education_id
    assert services.delete_education(world, str(edu_id))
    assert _project(isolated_engine, "dsc30-pa0").education_id is None


def test_deleting_a_project_keeps_its_award(world, isolated_engine):
    import services
    pc.link_achievement(isolated_engine, world, "1st Place Overall", "Atrium")
    assert services.delete_project(world, str(_project(isolated_engine, "Atrium").project_id))
    with Session(isolated_engine) as s:
        ach = s.exec(select(Achievement).where(Achievement.user_id == world)).one()
    assert ach.project_id is None


def test_heal_merge_moves_links_to_the_survivor(world, isolated_engine):
    from agents.kg_store import KGStoreMixin
    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    with Session(isolated_engine) as s:
        # A richer duplicate of the IDX role arrives from a re-ingest.
        s.add(Experience(user_id=world, title="Data Science Intern", company="IDX Exchange",
                         description="Forecasting home prices across California",
                         bullets=["Built pipelines", "Shipped a stacked ensemble"],
                         start_date="2026-01"))
        s.commit()
        assert KGStoreMixin._heal_experiences(s, world) == 1
        s.commit()
        survivor = s.exec(select(Experience).where(Experience.company == "IDX Exchange")).one()
    assert _project(isolated_engine, "idx_ds38").experience_id == survivor.experience_id
    assert _graph(world).get_project_context("idx_ds38")["company"] == "IDX Exchange"


def test_heal_merge_of_projects_keeps_the_confirmed_context(world, isolated_engine):
    from agents.kg_store import KGStoreMixin
    pc.set_project_context(isolated_engine, world, "Atrium", "personal")
    pc.link_achievement(isolated_engine, world, "1st Place Overall", "Atrium")
    with Session(isolated_engine) as s:
        s.add(Project(user_id=world, name="Atrium",
                      description="Learn anything with one search, then test it in a "
                                  "simulated classroom", repo_url="https://github.com/x/Atrium"))
        s.commit()
        assert KGStoreMixin._heal_projects(s, world) == 1
        s.commit()
    survivor = _project(isolated_engine, "Atrium")
    assert survivor.context_status == pc.PERSONAL
    with Session(isolated_engine) as s:
        assert s.exec(select(Achievement)).one().project_id == survivor.project_id


# ── harness tools ────────────────────────────────────────────────────────────

def test_records_and_search_follow_the_links(world, isolated_engine):
    from harness import tools
    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    pc.link_achievement(isolated_engine, world, "1st Place Overall", "Atrium")

    proj = tools.get_item(world, "proj:idx_ds38")["record"]
    assert proj["context"] == {"key": IDX, "kind": "experience",
                               "title": "Data Science Intern @ IDX Exchange"}
    assert proj["context_status"] == "linked"
    assert tools.get_item(world, IDX)["record"]["projects"] == ["proj:idx_ds38"]
    assert tools.get_item(world, "ach:1st place overall")["record"]["project"] == "proj:atrium"
    assert tools.get_item(world, "proj:atrium")["record"]["context"] is None

    # The role is findable by its project's name; the project hit names its context.
    hits = {h["key"]: h for h in tools.kg_search(world, "idx_ds38", limit=10)}
    assert IDX in hits
    assert hits["proj:idx_ds38"]["context"] == IDX


def test_harness_edu_key_matches():
    from harness import tools
    for e in ({"institution": "UC San Diego", "degree": "M.S. Data Science"},
              {"institution": "State U", "degree": "—"}, {"institution": "X", "degree": None}):
        assert tools.edu_key(e) == pc.edu_key(e)
    assert tools.ach_key({"title": " 1st Place "}) == pc.ach_key({"title": " 1st Place "})


def test_contract_tools_round_trip(world):
    from harness.contract import invoke
    out = invoke("suggest_project_contexts", world, {})
    assert {p["project_key"] for p in out["projects"]} >= {"proj:idx_ds38", "proj:atrium"}
    assert invoke("set_project_context", world,
                  {"project": "proj:atrium", "context": "personal"})["context_status"] == "personal"
    assert invoke("link_achievement", world, {"achievement": "ach:1st place overall",
                                              "project": "proj:atrium"})["project_key"] == "proj:atrium"
    assert invoke("set_project_context", world, {"project": "proj:atrium", "context": "personal"},
                  allow_writes=False)["error"]["code"] == "read_only"


# ── tailoring inputs ─────────────────────────────────────────────────────────

def test_annotate_evidence_via_projects_only_when_present():
    from agents.tailor import ResumeTailorAgent as T
    exps = [{"title": "Data Science Intern", "company": "IDX Exchange"}]
    T._annotate_evidence_via_projects(exps, {"XGBoost": {"projects": ["idx_ds38"],
                                                          "experiences": []}})
    assert "graph_evidence_via_projects" not in exps[0]
    T._annotate_evidence_via_projects(exps, {"XGBoost": {
        "projects": ["idx_ds38"], "experiences": [],
        "experiences_via_projects": [{"title": "Data Science Intern",
                                      "company": "IDX Exchange", "project": "idx_ds38"}]}})
    assert exps[0]["graph_evidence_via_projects"] == [{"skill": "XGBoost", "project": "idx_ds38"}]


def test_project_contexts_labels(world, isolated_engine):
    from agents.tailor import ResumeTailorAgent as T
    pc.set_project_context(isolated_engine, world, "idx_ds38", IDX)
    pc.set_project_context(isolated_engine, world, "dsc30-pa0", BS)
    pc.set_project_context(isolated_engine, world, "Atrium", "personal")
    with Session(isolated_engine) as s:
        got = {s.get(Project, pid).name: c for pid, c in T._project_contexts(s, world).items()}
    assert got == {
        "idx_ds38": {"kind": "work", "under": "Data Science Intern @ IDX Exchange"},
        "dsc30-pa0": {"kind": "coursework",
                      "under": "UC San Diego — B.S. Mathematics & Economics"},
        "Atrium": {"kind": "personal"},
    }


def test_planner_items_carry_context_only_when_set():
    from agents.tailor import ResumeTailorAgent as T
    plain = T._planner_items([], [{"name": "A", "description": "x"}])
    assert "context" not in plain[0]
    tagged = T._planner_items([], [{"name": "A", "description": "x",
                                    "context": {"kind": "personal"}}])
    assert tagged[0]["context"] == {"kind": "personal"}


class _RecordingLLM:
    def __init__(self):
        self.prompt = None

    def invoke(self, messages):
        self.prompt = messages[0]["content"]
        return SimpleNamespace(content="[]")


def _prompt(items):
    from agents.tailor_planner import TailorPlanner
    llm = _RecordingLLM()
    TailorPlanner(llm=llm).plan(items, [], "jd text", [])
    return llm.prompt


def test_planner_prompt_unchanged_without_context():
    base = [{"key": "proj:a", "section": "project", "label": "A", "source_text": "x"}]
    prompt = _prompt(base)
    assert "`context`" not in prompt and '"context"' not in prompt

    tagged = [dict(base[0], context={"kind": "work", "under": "Intern @ IDX"})]
    prompt = _prompt(tagged)
    assert '"context"' in prompt and "Work outranks coursework" in prompt
