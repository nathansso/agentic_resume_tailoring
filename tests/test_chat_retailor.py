"""
Job-chat tailoring action set (issue #91) + chat reward channel (issue #51).

Covers: propose-before-apply re-tailoring, plan approval threading a
plan_override into the pipeline, explain/diff from the decision log, one-level
revert, and the 1–5 score prompt writing user_score into the decision log.
"""
import uuid

import pytest
from sqlmodel import Session

import agents.chat as chat_module
from conftest import _seed_user_and_skill
from database.models import JobDescription, JobSkill, Skill, UserJobResult


class ShouldNotBeCalledLLM:
    def invoke(self, *_a, **_kw):
        raise AssertionError("LLM must not be called on this path")


def _no_llm(monkeypatch):
    monkeypatch.setattr(
        chat_module, "get_llm",
        lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM(),
    )


def _make_analyzed_job(engine, description="Python data pipelines"):
    with Session(engine) as session:
        job = JobDescription(title="SWE", company="Co", description=description,
                             status="analyzed")
        session.add(job)
        session.flush()
        skill = Skill(name="Python")
        session.add(skill)
        session.flush()
        session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id,
                             required=True, weight=1.0))
        session.commit()
        return str(job.job_id)


def _add_result(engine, job_id, user_id, content=None, decisions=None, previous=None):
    with Session(engine) as session:
        result = UserJobResult(
            user_id=user_id, job_id=uuid.UUID(job_id),
            tailored_resume_content=content or {},
            tailoring_decisions=decisions or [],
            tailored_resume_previous=previous or {},
        )
        session.add(result)
        session.commit()
        return str(result.result_id)


_CONTENT = {
    "experiences": [{"title": "ML Engineer", "company": "Nimbus",
                     "bullets": ["built models"]}],
    "projects": [{"name": "Recipe Review", "bullets": ["rf pipeline"]}],
    "skills_emphasized": ["Python"],
}

_LOG_ENTRY = {
    "timestamp": "2026-07-14T00:00:00", "revision_notes": "", "planner": "llm",
    "knobs": {}, "context": {"is_revision": False},
    "actions": [
        {"section": "project", "item_key": "proj:recipe review",
         "label": "Recipe Review", "op": "replace",
         "replacement_key": "proj:diginetica",
         "rationale": "diginetica is more rigorous", "propensity": 1.0},
        {"section": "experience", "item_key": "exp:ml engineer|nimbus",
         "label": "ML Engineer", "op": "keep", "rationale": "already strong",
         "propensity": 1.0},
    ],
    "reward": {"composite": 72.7, "baseline_composite": 50.2, "delta": 22.5},
}


# ── propose-before-apply ─────────────────────────────────────────────────────


def test_tailor_notes_proposes_plan_when_content_exists(isolated_engine, monkeypatch):
    """'tailor <notes>' with an existing tailored resume shows the delta plan
    and does not run the pipeline until approved (issue #91)."""
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT)

    import agents.tailor as tailor_module
    monkeypatch.setattr(tailor_module, "get_llm", lambda *a, **kw: object())

    canned_plan = {
        "actions": [
            {"section": "project", "item_key": "proj:recipe review",
             "label": "Recipe Review", "op": "replace",
             "replacement_key": "proj:diginetica",
             "rationale": "stronger fit", "propensity": 1.0},
        ],
        "knobs": {}, "planner": "llm",
    }
    monkeypatch.setattr(
        tailor_module.ResumeTailorAgent, "plan_preview",
        lambda self, uid, jid, rid, notes="": canned_plan,
    )

    applied = {}

    def fake_tailor_active(self, args, _confirmed=False, _plan_override=None):
        applied["args"] = args
        applied["plan"] = _plan_override
        return "Tailoring complete — applied."

    monkeypatch.setattr(chat_module.ChatAgent, "_tailor_active_job", fake_tailor_active)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent.chat("tailor swap the recipe project")

    assert "Proposed re-tailoring plan" in response
    assert "REPLACE Recipe Review → diginetica" in response
    assert "stronger fit" in response
    assert applied == {}, "pipeline must not run before approval"

    # Approving applies the exact previewed plan.
    response = agent.chat("1")
    assert "applied" in response.lower()
    assert applied["args"] == "swap the recipe project"
    assert applied["plan"] is canned_plan

    # Cancelling instead leaves everything unchanged.
    agent2 = chat_module.ChatAgent()
    agent2.set_active_job(job_id)
    agent2.chat("tailor swap the recipe project")
    applied.clear()
    assert "unchanged" in agent2.chat("2").lower()
    assert applied == {}


def test_tailor_notes_first_time_runs_directly(isolated_engine, monkeypatch):
    """Without tailored content there is no delta to propose — 'tailor <notes>'
    runs the pipeline directly, preserving the issue #70 flow."""
    _no_llm(monkeypatch)
    _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)

    captured = {}

    def fake_tailor_active(self, args, _confirmed=False, _plan_override=None):
        captured["args"] = args
        return "Tailoring complete."

    monkeypatch.setattr(chat_module.ChatAgent, "_tailor_active_job", fake_tailor_active)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent.chat("tailor emphasize Python")

    assert captured["args"] == "emphasize Python"
    assert "complete" in response.lower()


def test_plan_override_threaded_into_pipeline_state(isolated_engine, monkeypatch):
    """_tailor_active_job(_plan_override=...) lands in the pipeline state so
    tailor_resume_node executes the approved plan."""
    import graph.pipeline as _pipeline

    _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)

    seen = {}

    def fake_match(state):
        state["matched_skills"] = {}
        state["missing_skills"] = []
        state["ats_score"] = 50.0
        return state

    def fake_tailor(state):
        seen["plan_override"] = state.get("plan_override")
        state["tailored_content"] = {"experiences": [], "projects": [],
                                     "skills_emphasized": []}
        state["result_id"] = ""
        return state

    monkeypatch.setattr(_pipeline, "match_skills_node", fake_match)
    monkeypatch.setattr(_pipeline, "tailor_resume_node", fake_tailor)
    monkeypatch.setattr(_pipeline, "format_resume_node", lambda state: state)

    plan = {"actions": [{"item_key": "proj:x", "op": "delete"}], "knobs": {},
            "planner": "llm"}
    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    agent._tailor_active_job("notes", _plan_override=plan)

    assert seen["plan_override"] is plan


# ── explain / diff ────────────────────────────────────────────────────────────


def test_explain_renders_decision_log(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT,
                decisions=[_LOG_ENTRY])

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent.chat("explain")

    assert "REPLACE Recipe Review → diginetica" in response
    assert "diginetica is more rigorous" in response
    assert "72.7" in response and "+22.5" in response


def test_diff_lists_only_changes(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT,
                decisions=[_LOG_ENTRY])

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent.chat("what changed")

    assert "REPLACE Recipe Review" in response
    assert "KEEP ML Engineer" not in response            # keeps are omitted
    assert "diginetica is more rigorous" not in response  # rationale text omitted


def test_explain_without_log_gives_guidance(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    assert "decision log" in agent.chat("explain").lower()


# ── revert ────────────────────────────────────────────────────────────────────


def test_revert_swaps_current_and_previous(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    previous_content = {"experiences": [{"title": "Old", "company": "Version",
                                         "bullets": ["old bullet"]}]}
    result_id = _add_result(
        isolated_engine, job_id, user.user_id, content=_CONTENT,
        previous={"content": previous_content, "score_breakdown": {"composite": 40.0}},
    )

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent.chat("revert")
    assert "Reverted" in response

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, uuid.UUID(result_id))
        assert stored.tailored_resume_content == previous_content
        # swap, not pop: the replaced version is retained
        assert stored.tailored_resume_previous["content"] == _CONTENT
        # the revert is recorded in the decision log
        assert stored.tailoring_decisions[-1]["planner"] == "revert"
        assert stored.edited_tex is None

    # Reverting again restores the original content.
    agent.chat("revert")
    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, uuid.UUID(result_id))
        assert stored.tailored_resume_content == _CONTENT


def test_revert_without_previous_version(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    assert "nothing to revert" in agent.chat("revert").lower()


# ── 1–5 score channel (issue #51 chat reward) ────────────────────────────────


def test_score_reply_writes_user_score_into_decision_log(isolated_engine, monkeypatch):
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    result_id = _add_result(isolated_engine, job_id, user.user_id,
                            content=_CONTENT, decisions=[_LOG_ENTRY])

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    agent._pending_score_result_id = result_id

    response = agent.chat("4")
    assert "4/5" in response

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, uuid.UUID(result_id))
        assert stored.tailoring_decisions[-1]["reward"]["user_score"] == 4
        # the algorithmic reward is preserved alongside
        assert stored.tailoring_decisions[-1]["reward"]["delta"] == 22.5
    assert agent._pending_score_result_id is None


def test_non_score_reply_dismisses_prompt(isolated_engine, monkeypatch):
    """Ignoring the score prompt clears it — a later bare digit must not be
    misread as a score."""
    _no_llm(monkeypatch)
    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    result_id = _add_result(isolated_engine, job_id, user.user_id,
                            content=_CONTENT, decisions=[_LOG_ENTRY])

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    agent._pending_score_result_id = result_id

    agent.chat("explain")   # any non-score message dismisses the prompt
    assert agent._pending_score_result_id is None

    with Session(isolated_engine) as session:
        stored = session.get(UserJobResult, uuid.UUID(result_id))
        assert "user_score" not in stored.tailoring_decisions[-1]["reward"]


def test_retailor_run_sets_score_prompt(isolated_engine, monkeypatch):
    """A revision-style tailor run ends with the 1–5 score prompt armed."""
    import graph.pipeline as _pipeline

    user = _seed_user_and_skill(isolated_engine)
    job_id = _make_analyzed_job(isolated_engine)
    result_id = _add_result(isolated_engine, job_id, user.user_id, content=_CONTENT)

    def fake_match(state):
        state["matched_skills"] = {}
        state["missing_skills"] = []
        state["ats_score"] = 50.0
        return state

    def fake_tailor(state):
        state["tailored_content"] = {"experiences": [], "projects": [],
                                     "skills_emphasized": []}
        state["result_id"] = result_id
        return state

    monkeypatch.setattr(_pipeline, "match_skills_node", fake_match)
    monkeypatch.setattr(_pipeline, "tailor_resume_node", fake_tailor)
    monkeypatch.setattr(_pipeline, "format_resume_node", lambda state: state)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent._tailor_active_job("emphasize Python more")

    assert "Reply 1–5" in response
    assert agent._pending_score_result_id == result_id


# ── router grounding ─────────────────────────────────────────────────────────


def test_router_prompt_includes_tailored_summary():
    prompt = chat_module.build_router_prompt(
        has_profile=True, profile_name="Test",
        active_job_title="SWE", active_job_company="Co",
        tailored_summary="experiences [ML Engineer]; projects [Recipe Review]",
    )
    assert "source of truth" in prompt
    assert "ML Engineer" in prompt
    assert "propose_retailor_plan" in prompt
    assert "revert_tailoring" in prompt
