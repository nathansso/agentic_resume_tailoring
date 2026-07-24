"""
Knowledge-graph evidence wired into the tailoring planner (issue #138).

Covers the mandatory KG-evidence step:
  - the shared builder traversal (get_experiences_using_skill / evidence_for_skills);
  - the tailor helpers that invert, promote, and annotate that evidence;
  - the pipeline guarantees — the graph is actually consulted during tailoring,
    a graph-only path changes the candidate set, and an empty graph is a
    byte-for-byte no-op;
  - the planner consuming the evidence in its planning payload.

The "graph-only" case: a JD *requires* a skill (a JobSkill) that a project
evidences, but the JD's own free-text never repeats that skill name — so the
JD-keyword pre-selection pools the project while the graph's skill->project edge
surfaces it.
"""
from types import SimpleNamespace

from sqlmodel import Session

from database.models import (
    Experience, JobDescription, JobSkill, Project, Skill, UserJobResult, UserSkill,
)


# ── seeding ──────────────────────────────────────────────────────────────────


def _seed_scenario(engine) -> SimpleNamespace:
    """A user + JD where WebApp evidences the required skill GraphQL (its prose
    names it, so the graph links them) but the JD's own text never says
    "GraphQL", so JD-keyword pre-selection out-scores it with two other projects
    and pools it. The graph is the only signal that surfaces it."""
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(engine)  # gives a Python skill
    with Session(engine) as s:
        gql = Skill(name="GraphQL", category="tool")
        s.add(gql)
        s.commit()
        s.refresh(gql)
        s.add(UserSkill(user_id=user.user_id, skill_id=gql.skill_id, proficiency=4,
                        evidence_source="resume", confidence_score=0.9))

        # Two strongly JD-relevant projects (selected) + WebApp (pooled).
        s.add(Project(user_id=user.user_id, name="DataPipeline",
                      description="Built a data pipeline with airflow spark kafka "
                                  "etl warehouse analytics and orchestration",
                      end_date="Present"))
        s.add(Project(user_id=user.user_id, name="MLModel",
                      description="Trained machine learning models with pytorch for "
                                  "analytics etl warehouse pipeline scoring",
                      end_date="Present"))
        # Names GraphQL (so the graph links it) but shares no JD-text keywords.
        s.add(Project(user_id=user.user_id, name="WebApp",
                      description="Internal developer tool built with GraphQL"))

        job = JobDescription(
            title="Data Engineer", company="Acme", status="analyzed",
            description="data pipeline airflow spark kafka etl warehouse analytics "
                        "machine learning orchestration scoring",
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        s.add(JobSkill(job_id=job.job_id, skill_id=gql.skill_id, required=True, weight=1.0))
        s.commit()

        result = UserJobResult(user_id=user.user_id, job_id=job.job_id)
        s.add(result)
        s.commit()
        s.refresh(result)
        return SimpleNamespace(
            user_id=user.user_id, job_id=job.job_id, result_id=result.result_id,
        )


def _agent(monkeypatch, engine):
    import agents.tailor as tm
    monkeypatch.setattr(tm, "engine", engine)
    monkeypatch.setattr(tm, "get_llm", lambda *a, **k: object())
    return tm.ResumeTailorAgent()


# ── shared builder traversal (no duplicated graph logic) ─────────────────────


def test_evidence_for_skills_returns_project_evidence(isolated_engine):
    """evidence_for_skills surfaces the project that evidences a skill, and omits
    skills with no evidence edge (so a sparse graph yields {})."""
    from conftest import _seed_user_and_skill
    from knowledge_graph.builder import SkillGraphBuilder

    user = _seed_user_and_skill(isolated_engine)
    with Session(isolated_engine) as s:
        gql = Skill(name="GraphQL", category="tool")
        s.add(gql)
        s.commit()
        s.refresh(gql)
        s.add(UserSkill(user_id=user.user_id, skill_id=gql.skill_id, proficiency=4,
                        evidence_source="resume", confidence_score=0.9))
        s.add(Project(user_id=user.user_id, name="WebApp",
                      description="Internal developer tool built with GraphQL"))
        s.commit()

    builder = SkillGraphBuilder(user.user_id)
    builder.build_graph()

    ev = builder.evidence_for_skills(["GraphQL"])
    assert "WebApp" in ev["GraphQL"]["projects"]
    # Skills with no evidence edge are omitted (sparse -> {}).
    assert builder.evidence_for_skills(["Rust"]) == {}
    assert builder.evidence_for_skills([]) == {}


def test_get_experiences_using_skill_returns_title_and_company(isolated_engine):
    from conftest import _seed_user_and_skill
    from knowledge_graph.builder import SkillGraphBuilder

    user = _seed_user_and_skill(isolated_engine)  # Python skill
    with Session(isolated_engine) as s:
        s.add(Experience(user_id=user.user_id, title="Backend Engineer",
                         company="Nimbus", description="Built services in Python",
                         bullets=["Wrote Python APIs"]))
        s.commit()

    builder = SkillGraphBuilder(user.user_id)
    builder.build_graph()
    exps = builder.get_experiences_using_skill("Python")
    assert {"title": "Backend Engineer", "company": "Nimbus"} in exps


# ── tailor helpers (pure) ────────────────────────────────────────────────────


def test_item_evidence_map_scopes_to_candidates_and_dedupes():
    from agents.tailor import ResumeTailorAgent as R

    kg = {
        "GraphQL": {"projects": ["WebApp"], "experiences": []},
        "Python": {"projects": ["WebApp"],
                   "experiences": [{"title": "Eng", "company": "Nimbus"}]},
        "Rust": {"projects": ["Unknown"], "experiences": []},
    }
    proj_dicts = [{"name": "WebApp"}]
    exp_dicts = [{"title": "Eng", "company": "Nimbus"}]
    m = R._item_evidence_map(kg, proj_dicts, [], exp_dicts)

    assert m["proj:webapp"] == ["GraphQL", "Python"]   # de-duped, ordered
    assert m["exp:eng|nimbus"] == ["Python"]
    assert "proj:unknown" not in m                      # not a candidate -> scoped out


def test_promote_pulls_uncovered_pooled_project():
    from agents.tailor import ResumeTailorAgent as R

    selected = [{"name": "DataPipeline"}]
    pool = [{"name": "WebApp"}, {"name": "Extra"}]
    item_ev = {"proj:webapp": ["GraphQL"], "proj:datapipeline": ["Python"]}
    sel, rem = R._promote_evidenced_projects(selected, pool, item_ev)

    assert [p["name"] for p in sel] == ["DataPipeline", "WebApp"]
    assert [p["name"] for p in rem] == ["Extra"]        # dropped from replace pool


def test_promote_skips_skill_already_covered_by_selected():
    from agents.tailor import ResumeTailorAgent as R

    selected = [{"name": "DataPipeline"}]
    pool = [{"name": "WebApp"}]
    item_ev = {"proj:datapipeline": ["GraphQL"], "proj:webapp": ["GraphQL"]}
    sel, rem = R._promote_evidenced_projects(selected, pool, item_ev)

    assert [p["name"] for p in sel] == ["DataPipeline"]  # GraphQL already surfaced
    assert [p["name"] for p in rem] == ["WebApp"]


def test_promote_is_identity_on_empty_evidence():
    from agents.tailor import ResumeTailorAgent as R

    selected = [{"name": "A"}]
    pool = [{"name": "B"}]
    sel, rem = R._promote_evidenced_projects(selected, pool, {})
    assert sel is selected and rem is pool               # untouched references


def test_annotate_sets_graph_evidence_only_when_present():
    from agents.tailor import ResumeTailorAgent as R

    exps = [{"title": "Eng", "company": "Nimbus"}, {"title": "Other", "company": "X"}]
    projs = [{"name": "WebApp"}]
    m = {"exp:eng|nimbus": ["Python"], "proj:webapp": ["GraphQL"]}
    R._annotate_graph_evidence(exps, projs, m)

    assert exps[0]["graph_evidence"] == ["Python"]
    assert "graph_evidence" not in exps[1]               # no evidence -> no key
    assert projs[0]["graph_evidence"] == ["GraphQL"]


# ── pipeline: the graph is consulted (criterion 1) ───────────────────────────


def test_tailoring_consults_the_graph_for_jd_skills(isolated_engine, monkeypatch):
    """A call-based assertion (like the routing tests): tailoring builds the KG
    and asks it for evidence on the active JD's skills."""
    import agents.tailor as tm

    scenario = _seed_scenario(isolated_engine)
    calls = {"built": 0, "evidence_args": []}
    real = tm.SkillGraphBuilder

    class Spy(real):
        def build_graph(self):
            calls["built"] += 1
            return super().build_graph()

        def evidence_for_skills(self, names):
            calls["evidence_args"].append(list(names))
            return super().evidence_for_skills(names)

    monkeypatch.setattr(tm, "SkillGraphBuilder", Spy)
    agent = _agent(monkeypatch, isolated_engine)
    agent._load_inputs(scenario.user_id, scenario.job_id, scenario.result_id)

    assert calls["built"] == 1
    assert calls["evidence_args"] == [["GraphQL"]]


# ── pipeline: graph-only evidence changes candidates (criterion 5) ───────────


def test_graph_only_evidence_promotes_pooled_project(isolated_engine, monkeypatch):
    """WebApp is out-scored on JD text and pooled, but the graph ties it to the
    required skill GraphQL — so it is promoted into the candidate set the planner
    sees, and removed from the replace pool."""
    scenario = _seed_scenario(isolated_engine)
    agent = _agent(monkeypatch, isolated_engine)
    inputs = agent._load_inputs(scenario.user_id, scenario.job_id, scenario.result_id)

    names = [p["name"] for p in inputs["projects"]]
    assert "WebApp" in names
    webapp = next(p for p in inputs["projects"] if p["name"] == "WebApp")
    assert webapp["graph_evidence"] == ["GraphQL"]
    assert "WebApp" not in [p["name"] for p in inputs["project_pool"]]
    assert inputs["item_evidence"]["proj:webapp"] == ["GraphQL"]


# ── pipeline: empty graph is byte-for-byte (criterion 3) ─────────────────────


def test_empty_evidence_leaves_candidate_set_unchanged(isolated_engine, monkeypatch):
    """With no graph evidence, the pooled WebApp stays pooled, no item carries a
    graph_evidence key, and the result is deterministic — proving the step is
    purely additive (absence reproduces prior output)."""
    import agents.tailor as tm

    scenario = _seed_scenario(isolated_engine)

    class Empty(tm.SkillGraphBuilder):
        def evidence_for_skills(self, names):
            return {}

    monkeypatch.setattr(tm, "SkillGraphBuilder", Empty)
    agent = _agent(monkeypatch, isolated_engine)
    inputs = agent._load_inputs(scenario.user_id, scenario.job_id, scenario.result_id)

    assert inputs["item_evidence"] == {}
    names = [p["name"] for p in inputs["projects"]]
    assert "WebApp" not in names                         # not promoted
    assert all("graph_evidence" not in p for p in inputs["projects"])
    assert all("graph_evidence" not in e for e in inputs["experiences"])

    # Deterministic across runs.
    inputs2 = agent._load_inputs(scenario.user_id, scenario.job_id, scenario.result_id)
    assert [p["name"] for p in inputs2["projects"]] == names


# ── planner consumes the evidence (criterion 2) ──────────────────────────────


class _RecordingLLM:
    """Captures the planner prompt and returns an empty plan."""

    def __init__(self):
        self.prompt = None

    def invoke(self, messages):
        self.prompt = messages[0]["content"]
        return SimpleNamespace(content="[]")


def test_planner_payload_carries_graph_evidence_when_present():
    from agents.tailor_planner import TailorPlanner

    llm = _RecordingLLM()
    planner = TailorPlanner(llm=llm)
    items = [
        {"key": "proj:webapp", "section": "project", "label": "WebApp",
         "source_text": "a small web app", "graph_evidence": ["GraphQL"]},
    ]
    planner.plan(items, [], "jd text", [])

    assert '"graph_evidence"' in llm.prompt          # JSON payload key present
    assert "GraphQL" in llm.prompt
    # The planner is instructed how to use it.
    assert "uniquely evidences a required skill" in llm.prompt


def test_planner_payload_omits_graph_evidence_when_absent():
    """Byte-for-byte: an item without graph evidence adds no payload key (the
    prompt rule mentions `graph_evidence` in backticks, never as a JSON key)."""
    from agents.tailor_planner import TailorPlanner

    llm = _RecordingLLM()
    planner = TailorPlanner(llm=llm)
    items = [
        {"key": "proj:webapp", "section": "project", "label": "WebApp",
         "source_text": "a small web app"},
    ]
    planner.plan(items, [], "jd text", [])

    assert '"graph_evidence"' not in llm.prompt        # no payload key
