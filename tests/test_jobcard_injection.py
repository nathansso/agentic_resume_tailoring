"""JobCard injection into the tailoring context (issue #137).

Covers the consumption half of the cross-job memory tier:
  - `_load_inputs` ranks the user's prior cards against the active JD and
    returns a bounded, stably-ordered top-N;
  - the planner renders them into its prompt, including the negation signal;
  - **absent cards are byte-for-byte today's behavior** — a pre-#137 database
    produces the identical planner prompt, which is the backward-compatibility
    guarantee the acceptance criteria ask for;
  - the run is recorded in the decision log's context features.

Everything is offline: the planner LLM is a recording stub and the one
`role_family` classify is monkeypatched.
"""
from types import SimpleNamespace

import pytest
from sqlmodel import Session

from database.models import JobDescription, Project, UserJobResult


class _RecordingLLM:
    """Captures the planner prompt and returns an empty plan."""

    def __init__(self):
        self.prompt = None

    def invoke(self, messages):
        self.prompt = messages[0]["content"]
        return SimpleNamespace(content="[]")


def _agent(monkeypatch, engine):
    import agents.tailor as tm
    monkeypatch.setattr(tm, "engine", engine)
    monkeypatch.setattr(tm, "get_llm", lambda *a, **k: object())
    return tm.ResumeTailorAgent()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """Pin the one LLM call and keep the embedding model out of the suite."""
    import agents.job_card as jc
    import agents.skill_embeddings as se

    monkeypatch.setattr(
        jc, "classify_role_family",
        lambda title, company="", description="", **kw: "machine_learning")
    # No sentence-transformer download; JD vectors come from the stored JSON
    # column in these tests, and ensure_job_embedding must not try to encode.
    monkeypatch.setattr(se, "_encode", lambda texts: None)


def _seed_user(engine):
    from conftest import _seed_user_and_skill
    return _seed_user_and_skill(engine)


def _seed_job(engine, user_id, *, title, status="tailored",
              description="Train and serve machine learning models",
              embedding=None, content=None, decisions=None):
    """A job + result pair, optionally already carrying a cached JD centroid.

    `embedding_model` is set to the configured model so `ensure_job_embedding`
    treats the seeded vector as a warm cache and hands it straight back — a
    mismatched tag makes it recompute, which offline yields None and would
    quietly drop the embedding signal out of the ranking under test.
    """
    import json

    from config import EMBEDDING_MODEL

    with Session(engine) as s:
        job = JobDescription(
            user_id=user_id, title=title, company="Acme", status=status,
            description=description,
            embedding=json.dumps(embedding) if embedding else None,
            embedding_model=EMBEDDING_MODEL if embedding else None,
        )
        s.add(job)
        s.commit()
        s.refresh(job)
        result = UserJobResult(
            user_id=user_id, job_id=job.job_id, ats_score=70.0,
            tailored_score_breakdown={"composite": 80.0, "delta": 10.0},
            tailored_resume_content=content if content is not None else {
                "experiences": [{"title": "ML Engineer", "company": "Nimbus"}],
                "projects": [{"name": "SemanticSearch"}],
                "skills_emphasized": ["Python"],
            },
            tailoring_decisions=decisions or [],
        )
        s.add(result)
        s.commit()
        s.refresh(result)
        return job.job_id, result.result_id


def _rejection_log(label, item_key):
    """A decision-log entry recording a user-driven removal."""
    return [{
        "planner": "chat_approved",
        "revision_notes": f"drop {label}",
        "actions": [{
            "item_key": item_key, "op": "delete", "label": label,
            "section": "project", "rationale": "not relevant to this kind of role",
        }],
        "reward": {"delta": 12.0, "user_score": 5},
    }]


# ── absent cards are byte-for-byte today's behavior ───────────────────────────


def test_no_cards_means_no_injection(isolated_engine, monkeypatch):
    """A user with no completed jobs gets an empty card list — and pays nothing,
    not even the role classify."""
    import agents.job_card as jc

    calls = []
    monkeypatch.setattr(
        jc, "classify_role_family",
        lambda *a, **kw: calls.append(a) or "machine_learning")

    user = _seed_user(isolated_engine)
    job_id, result_id = _seed_job(isolated_engine, user.user_id, title="ML Engineer")

    agent = _agent(monkeypatch, isolated_engine)
    inputs = agent._load_inputs(user.user_id, job_id, result_id)

    assert inputs["job_cards"] == []
    assert inputs["role_family"] is None
    assert calls == [], "an empty candidate set must not spend a classify"


def test_planner_prompt_is_unchanged_without_cards():
    """The backward-compat guarantee, asserted literally: the prompt built with
    an empty card set is byte-identical to the prompt built with no card
    argument at all."""
    from agents.tailor_planner import TailorPlanner

    items = [{"key": "proj:webapp", "section": "project", "label": "WebApp",
              "source_text": "a small web app"}]

    without = _RecordingLLM()
    TailorPlanner(llm=without).plan(items, [], "jd text", [])

    for empty in ([], None):
        with_empty = _RecordingLLM()
        TailorPlanner(llm=with_empty).plan(
            items, [], "jd text", [], job_cards=empty)
        assert with_empty.prompt == without.prompt

    assert "PRIOR SIMILAR JOBS" not in without.prompt


# ── selection reaches the planner ─────────────────────────────────────────────


def test_load_inputs_ranks_prior_cards_against_the_active_jd(
    isolated_engine, monkeypatch,
):
    """Two completed jobs, one aligned with the active JD's cached vector and
    one orthogonal: the aligned card must come first, and the active job's own
    card must never be injected into its own run."""
    import services

    user = _seed_user(isolated_engine)
    aligned_id, _ = _seed_job(isolated_engine, user.user_id, title="Aligned Role",
                              embedding=[1.0, 0.0])
    orthogonal_id, _ = _seed_job(isolated_engine, user.user_id, title="Orthogonal Role",
                                 embedding=[0.0, 1.0])
    for job_id in (aligned_id, orthogonal_id):
        services.rebuild_job_card(user.user_id, job_id)

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active Role", embedding=[1.0, 0.0])
    services.rebuild_job_card(user.user_id, active_id)

    agent = _agent(monkeypatch, isolated_engine)
    inputs = agent._load_inputs(user.user_id, active_id, active_result)

    titles = [c["payload"]["job"]["title"] for c in inputs["job_cards"]]
    assert titles == ["Aligned Role", "Orthogonal Role"]
    assert "Active Role" not in titles, "a job must not remember itself"
    assert inputs["role_family"] == "machine_learning"


def test_injection_count_and_ordering_are_stable(isolated_engine, monkeypatch):
    """Fixed JD + fixed card set ⇒ the same count and the same order, every
    time. Repeated loads must not drift."""
    import agents.job_card as jc
    import services

    user = _seed_user(isolated_engine)
    for i in range(5):
        job_id, _ = _seed_job(
            isolated_engine, user.user_id, title=f"Prior Role {i}",
            embedding=[1.0 - i * 0.2, i * 0.2])
        services.rebuild_job_card(user.user_id, job_id)

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active Role", embedding=[1.0, 0.0])

    agent = _agent(monkeypatch, isolated_engine)
    runs = [
        [c["payload"]["job"]["title"]
         for c in agent._load_inputs(user.user_id, active_id, active_result)["job_cards"]]
        for _ in range(3)
    ]
    assert runs[0] == runs[1] == runs[2]
    assert len(runs[0]) == jc.top_n(), "injection must be capped at top-N"
    assert runs[0][0] == "Prior Role 0", "the most JD-aligned card leads"


def test_planner_prompt_carries_the_cards_and_the_negation_signal(
    isolated_engine, monkeypatch,
):
    """The whole point of the tier: what the user removed last time reaches the
    planner, with an instruction telling it what that means."""
    import services
    from agents.tailor_planner import TailorPlanner

    user = _seed_user(isolated_engine)
    prior_id, _ = _seed_job(
        isolated_engine, user.user_id, title="Prior ML Role", embedding=[1.0, 0.0],
        decisions=_rejection_log("Pixel Adventure", "proj:pixel adventure"),
    )
    services.rebuild_job_card(user.user_id, prior_id)

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active ML Role", embedding=[1.0, 0.0])

    agent = _agent(monkeypatch, isolated_engine)
    cards = agent._load_inputs(user.user_id, active_id, active_result)["job_cards"]
    assert cards

    llm = _RecordingLLM()
    TailorPlanner(llm=llm).plan(
        [{"key": "proj:webapp", "section": "project", "label": "WebApp",
          "source_text": "a small web app"}],
        [], "jd text", [], job_cards=cards,
    )

    assert "PRIOR SIMILAR JOBS" in llm.prompt
    assert "Prior ML Role" in llm.prompt
    assert "User removed: Pixel Adventure" in llm.prompt
    assert "standing preference" in llm.prompt


def test_plan_preview_does_not_spend_a_classify(isolated_engine, monkeypatch):
    """A preview is a cheap look-ahead — it may read the role-family cache but
    must never call the model for it."""
    import agents.job_card as jc
    import services

    calls = []
    monkeypatch.setattr(
        jc, "classify_role_family",
        lambda *a, **kw: calls.append(a) or "machine_learning")

    user = _seed_user(isolated_engine)
    prior_id, _ = _seed_job(isolated_engine, user.user_id, title="Prior Role",
                            embedding=[1.0, 0.0])
    services.rebuild_job_card(user.user_id, prior_id)
    assert len(calls) == 1  # the card's own compile

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active Role",
        description="A completely different design leadership posting",
        embedding=[1.0, 0.0])

    agent = _agent(monkeypatch, isolated_engine)
    agent._load_inputs(user.user_id, active_id, active_result, allow_classify=False)
    assert len(calls) == 1, "preview must not classify"


def test_selection_degrades_to_nothing_when_the_memory_layer_fails(
    isolated_engine, monkeypatch,
):
    """Memory is optional; tailoring is not."""
    import services

    user = _seed_user(isolated_engine)
    prior_id, _ = _seed_job(isolated_engine, user.user_id, title="Prior Role")
    services.rebuild_job_card(user.user_id, prior_id)

    def _boom(*a, **kw):
        raise RuntimeError("card store is down")

    monkeypatch.setattr(services, "load_job_cards", _boom)

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active Role")
    agent = _agent(monkeypatch, isolated_engine)
    inputs = agent._load_inputs(user.user_id, active_id, active_result)

    assert inputs["job_cards"] == []
    assert inputs["experiences"] is not None  # the rest of the load still worked


def test_decision_log_records_how_many_cards_were_injected(
    isolated_engine, monkeypatch,
):
    """`n_job_cards` joins `n_graph_evidence` as a context feature, so the #51
    offline dataset can tell a card-informed run from a cold one."""
    import agents.tailor as tm
    import services

    user = _seed_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Project(user_id=user.user_id, name="SemanticSearch",
                      description="Semantic search over embeddings with python"))
        s.commit()

    prior_id, _ = _seed_job(isolated_engine, user.user_id, title="Prior Role",
                            embedding=[1.0, 0.0])
    services.rebuild_job_card(user.user_id, prior_id)

    active_id, active_result = _seed_job(
        isolated_engine, user.user_id, title="Active Role", embedding=[1.0, 0.0],
        content={},
    )

    agent = _agent(monkeypatch, isolated_engine)
    # Skip generation entirely: this asserts the bookkeeping, not the LLM loop.
    monkeypatch.setattr(agent, "graph", SimpleNamespace(invoke=lambda state: {
        **state, "attempt": 1, "best_content": {"experiences": []},
        "best_evaluation": {"ats_breakdown": {"composite": 80.0}},
        "tailored_content": {"experiences": []}, "evaluation": {},
        "best_score": 80.0,
    }))
    monkeypatch.setattr(tm.TailorPlanner, "plan",
                        lambda self, **kw: {"actions": [], "knobs": {},
                                            "planner": "default"})
    agent.tailor(user.user_id, active_id, active_result)

    with Session(isolated_engine) as s:
        stored = s.get(UserJobResult, active_result)
        context = stored.tailoring_decisions[-1]["context"]
    assert context["n_job_cards"] == 1
