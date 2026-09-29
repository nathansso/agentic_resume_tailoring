"""Skill retrieval without a model (#233): word-boundary skill links, bullet
provenance, degrees reached through projects, and requirement keywords that rank
skills. Everything here is deterministic; no model client is involved.
"""

from uuid import UUID

import pytest
from sqlmodel import Session

from agents import project_context as pc
from agents.skill_matching import (
    SkillMatcher, canonical_key, match_requirement_terms, priority_skills,
)
from database.models import (
    Education, Experience, JDProfile, Project, ProjectBlurb, Skill, User, UserSkill,
)


# ── the matcher (pure) ────────────────────────────────────────────────────────

@pytest.mark.parametrize("skill,text,expected", [
    # Java is not inside JavaScript; a whole-word Java still links.
    ("Java", "Built apps in JavaScript and TypeScript", False),
    ("Java", "Wrote Java services", True),
    ("Java", "A Java-based backend, mostly Java.", True),
    ("JavaScript", "Wrote Java services", False),
    # `+`, `#` and `.` belong to the name.
    ("C++", "Optimised C++ kernels", True),
    ("C", "Optimised C++ kernels", False),
    ("C#", "Built C# services", True),
    ("C", "Built C# services", False),
    ("C", "Wrote C/C++ drivers", True),
    (".NET", "Ported the service to .NET Core", True),
    ("NET", "Ported the service to .NET Core", False),
    ("Node.js", "Built Node.js APIs", True),
    ("Node", "Built Node.js APIs", False),
    ("Python", "Wrote Python.", True),
    ("Python", "Wrote pythonic code", False),
    # Short names: whole tokens in their own capitalization, never beside `&`.
    ("R", "Analysed survey data in R and Python", True),
    ("R", "The R&D team ran research on rewards", False),
    ("R", "used r to test", False),
    ("Go", "Services written in Go and Rust", True),
    ("Go", "We go to market in June", False),
    ("Go", "Go to market strategy", False),
    ("Go", "Shipped it. Go services followed", False),
    ("Go", "Read mongo and goroutine docs", False),
    # Aliases resolve on both sides.
    ("PyTorch", "Trained models with torch", True),
    ("torch", "Trained models with PyTorch", True),
    ("scikit-learn", "Fit sklearn pipelines", True),
    ("scikit-learn", "Fit scikit learn pipelines", True),
    ("OpenAI API", "Called OpenAI models", True),
    # A short alias that is also a fragment does not link.
    ("TensorFlow", "Ran TF-IDF ranking", False),
    ("Machine Learning", "Applied machine-learning methods", True),
])
def test_word_boundary_matching(skill, text, expected):
    assert SkillMatcher(skill).mentions(text) is expected


def test_canonical_key_applies_the_alias_map():
    assert canonical_key("torch") == canonical_key("PyTorch") == "pytorch"
    assert canonical_key("  Node.js ") == "node.js"


# ── graph links and provenance ────────────────────────────────────────────────

def _user(engine, skills):
    with Session(engine) as s:
        user = User(name="Test User", email="t@example.com")
        s.add(user)
        s.commit()
        s.refresh(user)
        for name in skills:
            row = Skill(name=name, category="Other")
            s.add(row)
            s.commit()
            s.refresh(row)
            s.add(UserSkill(user_id=user.user_id, skill_id=row.skill_id, proficiency=3,
                            evidence_source="resume", confidence_score=0.9))
        s.commit()
        return user.user_id


def _graph(uid):
    from knowledge_graph.builder import SkillGraphBuilder
    b = SkillGraphBuilder(uid)
    b.build_graph()
    return b


@pytest.fixture()
def links(isolated_engine):
    uid = _user(isolated_engine, ["Java", "JavaScript", "C++", "C#", "C", "R", "Go",
                                  "PyTorch", "Node.js"])
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=uid, title="Engineer", company="Acme", description="Web work",
                         bullets=["Built React apps in JavaScript.",
                                  "Rewrote the engine in C++ and C#.",
                                  "",
                                  "Trained torch models; the R&D team reviewed them.",
                                  "Analysed results in R and shipped a Go service."]))
        s.add(Project(user_id=uid, name="Kernels", description="GPU kernels in C++"))
        s.add(Project(user_id=uid, name="Node.js-shop", description="A store"))
        s.commit()
    return uid


def _linked(g, item):
    return {d["name"] for _, d in g.graph.nodes(data=True) if d.get("type") == "Skill"
            and g.graph.has_edge(item, f"Skill:{d['name']}")}


def test_corrected_links_on_word_boundaries(links):
    g = _graph(links)
    assert _linked(g, "Experience:Acme - Engineer") == {
        "JavaScript", "C++", "C#", "PyTorch", "R", "Go"}
    # Java is not in "JavaScript", C is not in "C++" / "C#", R is not in "R&D".
    assert "Java" not in _linked(g, "Experience:Acme - Engineer")
    assert _linked(g, "Project:Kernels") == {"C++"}
    # A name-only match still links, to the project rather than to a bullet.
    assert _linked(g, "Project:Node.js-shop") == {"Node.js"}


def test_a_link_records_the_bullets_that_name_the_skill(links):
    g = _graph(links)
    edge = lambda item, skill: g.graph.edges[item, f"Skill:{skill}"]["bullets"]
    exp = "Experience:Acme - Engineer"
    assert edge(exp, "JavaScript") == [0]
    assert edge(exp, "C++") == [1] and edge(exp, "C#") == [1]
    # Empty bullets are dropped before numbering, as the harness numbers its cites.
    assert edge(exp, "PyTorch") == [2]        # "torch" reaches PyTorch through the alias map
    assert edge(exp, "R") == [3] and edge(exp, "Go") == [3]
    assert edge("Project:Kernels", "C++") == [0]   # a description is the project's bullet 0
    assert edge("Project:Node.js-shop", "Node.js") == []   # named only in the project's name


def test_evidence_carries_cites_that_resolve(links):
    from harness.executor import _KG
    g = _graph(links)
    ev = g.evidence_for_skills(["C++", "Node.js"])
    # Projects first, then roles, as in the entry's own `projects` / `experiences`.
    assert ev["C++"]["bullets"] == [
        {"key": "proj:kernels", "index": 0, "cite": "proj:kernels#b0"},
        {"key": "exp:engineer|acme", "index": 1, "cite": "exp:engineer|acme#b1"}]
    # Existing keys and shapes are untouched.
    assert ev["C++"]["projects"] == ["Kernels"]
    assert ev["C++"]["experiences"] == [{"title": "Engineer", "company": "Acme"}]
    # Named only in a project's name: evidence, but no bullet to cite.
    assert "bullets" not in ev["Node.js"]
    kg = _KG(links)
    assert all(kg.resolves(b["cite"]) for b in ev["C++"]["bullets"])


def test_project_bullets_index_its_blurbs_when_it_has_them(isolated_engine):
    from harness.executor import _KG
    uid = _user(isolated_engine, ["Rust"])
    with Session(isolated_engine) as s:
        p = Project(user_id=uid, name="Engine", description="Plain description")
        s.add(p)
        s.commit()
        s.refresh(p)
        s.add(ProjectBlurb(project_id=p.project_id, style="a_concise", content="Fast."))
        s.add(ProjectBlurb(project_id=p.project_id, style="b_detailed",
                           content="Written in Rust for speed."))
        s.commit()
    ev = _graph(uid).evidence_for_skills(["Rust"])
    assert ev["Rust"]["bullets"] == [{"key": "proj:engine", "index": 1, "cite": "proj:engine#b1"}]
    assert _KG(uid).resolves("proj:engine#b1")


def test_a_jd_alias_finds_the_held_skill(links):
    ev = _graph(links).evidence_for_skills(["torch"])
    assert list(ev) == ["torch"]
    assert ev["torch"]["bullets"][0]["cite"] == "exp:engineer|acme#b2"


def test_evidence_lists_degrees_reached_through_projects(links, isolated_engine):
    with Session(isolated_engine) as s:
        s.add(Education(user_id=links, institution="State U", degree="M.S. Data Science"))
        s.commit()
    assert "education_via_projects" not in _graph(links).evidence_for_skills(["C++"])["C++"]
    pc.set_project_context(isolated_engine, links, "Kernels", "edu:state u|m.s. data science")
    ev = _graph(links).evidence_for_skills(["C++"])["C++"]
    assert ev["education_via_projects"] == [
        {"institution": "State U", "degree": "M.S. Data Science", "project": "Kernels"}]
    # A degree is not a role: nothing leaks into the role lists.
    assert "experiences_via_projects" not in ev


# ── requirement keywords -> skills ────────────────────────────────────────────

REQS = [
    {"text": "Knows Kafka.", "type": "preferred", "criticality": 2, "terms": ["kafka", "sql"],
     "ordinal": 0},
    {"text": "Knows Rust.", "type": "required", "criticality": 3, "terms": ["rust"], "ordinal": 1},
    {"text": "Knows deep learning.", "type": "required", "criticality": 5,
     "terms": ["torch", "deep learning"], "ordinal": 2},
    {"text": "Mentions Go in passing.", "type": "incidental", "terms": ["go"], "ordinal": 3},
]


def test_terms_match_exactly_and_through_aliases_by_priority():
    out = match_requirement_terms(["Rust", "PyTorch", "SQL", "Go"], REQS)
    # Required before preferred, the more critical requirement first.
    assert [(m["skill"], m["type"], m["requirement"]) for m in out["matches"]] == [
        ("PyTorch", "required", 2), ("Rust", "required", 1), ("SQL", "preferred", 0)]
    assert priority_skills(out["matches"]) == ["PyTorch", "Rust", "SQL"]
    # Not guessed, and incidental requirements take no part.
    assert out["unmatched"] == [
        {"term": "deep learning", "requirement": 2, "type": "required"},
        {"term": "kafka", "requirement": 0, "type": "preferred"}]


def test_no_requirements_match_nothing():
    assert match_requirement_terms(["Python"], None) == {"matches": [], "unmatched": []}
    assert match_requirement_terms(["Python"], [{"text": "x", "terms": []}]) == {
        "matches": [], "unmatched": []}


def _kg_uid(engine, skills):
    uid = _user(engine, skills)
    with Session(engine) as s:
        s.add(Experience(user_id=uid, title="Engineer", company="Acme", bullets=["Did work."]))
        s.commit()
    return uid


JD = "We want Python. Python everywhere; strong Python and data skills."


def test_a_required_term_ranks_ahead_of_tfidf(isolated_engine):
    from harness.executor import kg_default_content
    uid = _kg_uid(isolated_engine, ["Python", "Rust", "SQL", "Pandas"])
    plain = [s["name"] for s in kg_default_content(uid, JD)["skills_ranked"]]
    assert plain[0] == "Python"
    reqs = [{"text": "Rust.", "type": "required", "terms": ["rust"], "ordinal": 0},
            {"text": "SQL.", "type": "preferred", "terms": ["sql"], "ordinal": 1}]
    ranked = [s["name"] for s in kg_default_content(uid, JD, requirements=reqs)["skills_ranked"]]
    assert ranked[:3] == ["Rust", "SQL", "Python"]     # required, preferred, then TF-IDF
    assert sorted(ranked) == sorted(plain)


def test_no_requirements_leave_the_default_unchanged(isolated_engine):
    from harness.executor import kg_default_content
    uid = _kg_uid(isolated_engine, ["Python", "Rust", "SQL"])
    base = kg_default_content(uid, JD)
    assert kg_default_content(uid, JD, requirements=None) == base
    assert kg_default_content(uid, JD, requirements=[]) == base
    unmatched = [{"text": "Kafka.", "type": "required", "terms": ["kafka"], "ordinal": 0}]
    assert kg_default_content(uid, JD, requirements=unmatched) == base


def test_a_matched_skill_the_tfidf_cap_dropped_comes_back(isolated_engine):
    from agents.skill_scorer import MAX_SKILLS
    from harness.executor import kg_default_content
    uid = _kg_uid(isolated_engine, ["Python"] + [f"Skill{n:02d}" for n in range(30)])
    reqs = [{"text": "x", "type": "required", "terms": ["skill29"], "ordinal": 0}]
    plain = [s["name"] for s in kg_default_content(uid, JD)["skills_ranked"]]
    assert "Skill29" not in plain
    ranked = kg_default_content(uid, JD, requirements=reqs)["skills_ranked"]
    assert ranked[0]["name"] == "Skill29" and len(ranked) <= MAX_SKILLS


def test_open_job_reports_matched_and_unmatched_terms(isolated_engine):
    from harness.contract import invoke
    from harness.executor import job_requirements
    uid = _user(isolated_engine, ["Python", "PyTorch"])
    reqs = [{"text": "The candidate has experience with torch.", "terms": ["torch"],
             "criticality": 4},
            {"text": "The candidate knows Kafka.", "type": "preferred", "terms": ["kafka"]}]
    out = invoke("open_job", uid, {"jd_text": JD, "requirements": reqs,
                                   "metadata": {"title": "Intern", "company": "Acme"}})
    assert out["error"] is None
    assert out["skill_matches"] == [{"skill": "PyTorch", "term": "torch", "requirement": 0,
                                     "type": "required"}]
    assert out["unmatched_terms"] == [{"term": "kafka", "requirement": 1, "type": "preferred"}]
    # The executor reads the same stored requirements for the default ranking.
    stored = job_requirements(uid, UUID(out["job_id"]))
    assert [r["terms"] for r in stored] == [["torch"], ["kafka"]]


def test_open_job_without_requirements_reports_nothing(isolated_engine):
    from harness.contract import invoke
    uid = _user(isolated_engine, ["Python"])
    out = invoke("open_job", uid, {"jd_text": JD, "requirements": [],
                                   "metadata": {"title": "Intern", "company": "Acme"}})
    assert out["skill_matches"] == [] and out["unmatched_terms"] == []
