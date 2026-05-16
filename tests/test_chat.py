"""Chat agent routing, fast-paths, tool calls, and trace tests."""
import uuid

import pytest
from sqlmodel import Session, select

import agents.chat as chat_module
import tui.services as services_module
from database.models import ChatMessage, JobDescription, UserJobResult
from conftest import _seed_user_and_skill


# ── Routing ────────────────────────────────────────────────────────────────────

def test_chat_semantic_routing_uses_tool_and_is_fast(isolated_engine, monkeypatch):
    """Fuzzy skill queries reach the LLM; LLM resolves via TOOL_CALL."""
    _seed_user_and_skill(isolated_engine)

    class FakeLLM:
        def invoke(self, *_args, **_kwargs):
            class Resp:
                content = "TOOL_CALL: query_skills_vs_jobs()"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: FakeLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("could you please shwo me all my skils")

    assert response
    assert "tailor" in response.lower() or "no jobs" in response.lower() or "skills" in response.lower()


def test_fast_path_help_command(isolated_engine, monkeypatch):
    """agent.chat('help') returns without calling LLM and contains command list."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_args, **_kwargs):
            raise AssertionError("LLM must not be called for 'help'")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("help")

    assert "skills" in response.lower()
    assert "projects" in response.lower()
    assert "ingest" in response.lower() or "F1" in response


def test_short_message_now_reaches_llm(isolated_engine, monkeypatch):
    """Short/unrecognized messages pass through to the LLM."""
    llm_called = []

    class TrackingLLM:
        def invoke(self, *_args, **_kwargs):
            llm_called.append(True)
            class Resp:
                content = "I can help with that."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: TrackingLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("hmm")

    assert llm_called, "Short unrecognized messages should reach the LLM"
    assert response == "I can help with that."


def test_short_message_passes_through_when_bot_asked_question(isolated_engine, monkeypatch):
    """Short reply after the bot asked a question goes to the LLM (not fast-path)."""
    llm_called = []

    class TrackingLLM:
        def invoke(self, *_args, **_kwargs):
            llm_called.append(True)
            class Resp:
                content = "Got it."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: TrackingLLM())

    agent = chat_module.ChatAgent()
    agent.history.append({"role": "user", "content": "ingest my github"})
    agent.history.append({"role": "assistant", "content": "What is your GitHub username?"})

    agent.chat("nathansso")
    assert llm_called, "LLM should be called when the bot previously asked a question"


def test_ingest_keyword_returns_numbered_options(isolated_engine, monkeypatch):
    """Typing 'ingest' alone returns numbered ingestion choices."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for 'ingest'")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest")

    assert "1" in response and "2" in response and "3" in response
    assert "github" in response.lower()
    assert "resume" in response.lower()
    assert "linkedin" in response.lower()
    assert agent._pending_options, "pending_options should be set after offering choices"


def test_pending_option_resolved_by_digit_reply(isolated_engine, monkeypatch):
    """Replying '1' after numbered options resolves the option without LLM."""
    monkeypatch.setattr(services_module, "ingest_github", lambda username="": f"GitHub ingested for {username}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called when resolving a pending option")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    from database.user_utils import create_profile
    user = create_profile("Test User", "testuser@local")
    from database.db import engine as db_engine
    from sqlmodel import Session as S
    with S(db_engine) as sess:
        u = sess.get(type(user), user.user_id)
        u.github_username = "myghuser"
        sess.add(u)
        sess.commit()

    agent = chat_module.ChatAgent()
    agent.chat("ingest github")
    assert agent._pending_options, "pending_options should be set after 'ingest github'"
    response = agent.chat("1")
    assert "myghuser" in response or "GitHub ingested" in response


def test_ingest_token_combo_routes_to_github(isolated_engine, monkeypatch):
    """'i want to ingest skill from my github' routes to GitHub ingestion."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+github token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("i want to ingest skill from my github")

    assert "github" in response.lower()
    assert "ingest github" in response.lower() or "username" in response.lower()
    assert "Your skills" not in response


def test_ingest_token_combo_routes_to_resume(isolated_engine, monkeypatch):
    """'can you fetch my resume' routes to resume ingestion instructions without LLM."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+resume token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("can you fetch my resume and add it")

    assert "ingest resume" in response.lower()
    assert "path" in response.lower() or "file" in response.lower()


def test_ingest_token_combo_routes_to_linkedin(isolated_engine, monkeypatch):
    """'load my linkedin data' routes to LinkedIn ingestion instructions without LLM."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest+linkedin token combo")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("load my linkedin data")

    assert "linkedin" in response.lower()
    assert "ingest linkedin" in response.lower() or "pdf" in response.lower()


def test_query_skills_vs_jobs_no_jobs(isolated_engine):
    """query_skills_vs_jobs returns helpful guidance when no jobs are saved."""
    from database.user_utils import create_profile
    create_profile("Test User", "test@local")

    result = chat_module.query_skills_vs_jobs()
    assert "no jobs" in result.lower() or "tailor" in result.lower()


def test_query_skills_vs_jobs_with_job_result(isolated_engine):
    """query_skills_vs_jobs shows match score and skill breakdown when results exist."""
    import uuid
    from datetime import datetime
    from database.user_utils import create_profile
    from database.db import engine as db_engine
    from sqlmodel import Session as S

    user = create_profile("Match User", "matchuser@local")
    job_id = uuid.uuid4()
    with S(db_engine) as sess:
        sess.add(JobDescription(
            job_id=job_id, title="ML Engineer", company="Acme",
            description="Build models.", created_at=datetime.utcnow(),
        ))
        sess.add(UserJobResult(
            result_id=uuid.uuid4(), user_id=user.user_id, job_id=job_id,
            ats_score=78.5,
            matched_skills={"Python": 1, "PyTorch": 1},
            missing_skills=["Go", "Kubernetes"],
            created_at=datetime.utcnow(),
        ))
        sess.commit()

    result = chat_module.query_skills_vs_jobs()
    assert "ML Engineer" in result
    assert "78%" in result or "78" in result
    assert "Python" in result
    assert "Go" in result


# ── Fast-path argument routing ──────────────────────────────────────────────────

def test_chat_ingest_resume_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest resume test.md') calls service without LLM."""
    monkeypatch.setattr(services_module, "ingest_resume_file", lambda path: f"Resume ingested: {path}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest resume")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest resume my_resume.md")
    assert "my_resume.md" in response
    assert "LLM" not in response


def test_chat_ingest_github_no_username_returns_prompt(isolated_engine, monkeypatch):
    """agent.chat('ingest github') with no username returns a prompt, not a service call."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest github")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github")
    assert "username" in response.lower()
    assert "ingest github" in response.lower()


def test_chat_ingest_github_with_username_calls_service(isolated_engine, monkeypatch):
    """agent.chat('ingest github <user>') calls the service without the LLM."""
    monkeypatch.setattr(services_module, "ingest_github", lambda username="": f"GitHub ingested for {username}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for ingest github <user>")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github nathansso")
    assert "nathansso" in response


def test_chat_tailor_fast_path(isolated_engine, monkeypatch):
    """agent.chat('tailor <job>') calls run_tailor without LLM."""
    monkeypatch.setattr(chat_module, "run_tailor", lambda job: f"Tailored for: {job}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for tailor fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("tailor Senior Engineer at Acme Corp")
    assert "Senior Engineer at Acme Corp" in response


# ── PRD 06.1 — router prompt hardening and repo-scoped ingestion ───────────────

def test_build_router_prompt_contains_state(isolated_engine):
    """build_router_prompt injects runtime state into the system prompt."""
    prompt = chat_module.build_router_prompt(
        has_profile=True,
        profile_name="Alice",
        github_username="alicecodes",
        waiting_for_clarification=False,
    )
    assert "Role" in prompt
    assert "Current state" in prompt
    assert "Allowed actions" in prompt
    assert "Alice" in prompt
    assert "alicecodes" in prompt
    assert "TOOL_CALL:" in prompt
    assert "CLARIFY:" in prompt
    assert "RESPONSE:" in prompt
    assert "run_ingest_github_repo" in prompt

    prompt_no_profile = chat_module.build_router_prompt(has_profile=False)
    assert "none" in prompt_no_profile.lower()


def test_malformed_router_output_falls_back_safely(isolated_engine, monkeypatch):
    """LLM returning gibberish is treated as plain text without raising."""
    class GibberishLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = "I dunno lol just do stuff maybe??"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.2: GibberishLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("what is the meaning of life")
    assert response == "I dunno lol just do stuff maybe??"


def test_ingest_repo_owner_repo_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest github repo owner/repo') calls the repo service without LLM."""
    monkeypatch.setattr(services_module, "ingest_github_repo", lambda ref: f"Single repo ingested: {ref}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for repo fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest github repo openai/evals")
    assert "openai/evals" in response
    assert "LLM" not in response


def test_ingest_github_url_fast_path(isolated_engine, monkeypatch):
    """agent.chat('ingest https://github.com/owner/repo') routes to repo service without LLM."""
    monkeypatch.setattr(services_module, "ingest_github_repo", lambda ref: f"Single repo ingested: {ref}")

    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for GitHub URL fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest https://github.com/openai/evals")
    assert "openai" in response or "evals" in response
    assert "LLM" not in response


def test_ingest_new_github_repo_returns_clarification(isolated_engine, monkeypatch):
    """'ingest a new github repo' returns a repo-specific clarification."""
    class ShouldNotBeCalledLLM:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for repo clarification fast-path")

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: ShouldNotBeCalledLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("ingest a new github repo")

    assert "owner/repo" in response.lower() or "github url" in response.lower() or "provide" in response.lower()
    assert "ingest github <username>" not in response


# ── PRD 06 — trace system ──────────────────────────────────────────────────────

def test_chat_trace_fast_path(isolated_engine):
    """Fast-path routing emits exactly one trace with correct session metadata."""
    traces = []
    agent = chat_module.ChatAgent(trace_sink=lambda t: traces.append(dict(t)))
    agent.chat("help")

    assert len(traces) == 1
    t = traces[0]
    assert t["session_id"] == agent._session_id
    assert t["turn_index"] == 0
    assert t["user_message"] == "help"
    assert t["route_kind"] == "fast_path"
    assert t["response_text"] != ""


def test_chat_trace_llm_tool_call(isolated_engine, monkeypatch):
    """LLM TOOL_CALL envelope path records tool names in the trace."""
    monkeypatch.setattr(services_module, "ingest_github", lambda username="": f"[STUBBED] ingested {username}")

    class FakeLLM:
        def invoke(self, _messages):
            class R:
                content = "TOOL_CALL: run_ingest_github(testuser)"
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    traces = []
    agent = chat_module.ChatAgent(trace_sink=lambda t: traces.append(dict(t)))
    agent.chat("tell me about my career trajectory")

    assert len(traces) >= 1
    last = traces[-1]
    assert last["route_kind"] in ("tool_call", "llm")
    if last["route_kind"] == "tool_call":
        assert "run_ingest_github" in last.get("tool_calls_requested", [])
        assert "run_ingest_github" in last.get("tool_calls_executed", [])


# ── PRD 10 — Persistent chat memory integration ────────────────────────────────

def test_chat_persists_user_and_assistant_messages_to_db(isolated_engine, monkeypatch):
    """chat() writes both user and assistant ChatMessage rows when a job is active."""
    with Session(isolated_engine) as session:
        job = JobDescription(title="Persist Test", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)
        job_uuid = job.job_id

    class FakeLLM:
        def invoke(self, *_a, **_kw):
            class R:
                content = "Here is what I know about this role."
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    agent.chat("tell me about this job")

    with Session(isolated_engine) as session:
        msgs = session.exec(
            select(ChatMessage).where(ChatMessage.job_id == job_uuid)
        ).all()

    roles = [m.role for m in msgs]
    assert "user" in roles, "User message was not persisted"
    assert "assistant" in roles, "Assistant message was not persisted"
    user_msg = next(m for m in msgs if m.role == "user")
    assert user_msg.content == "tell me about this job"


def test_set_active_job_restores_history_from_db(isolated_engine):
    """set_active_job() populates self.history from DB, simulating an app restart."""
    with Session(isolated_engine) as session:
        job = JobDescription(title="History Test", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    services_module.save_chat_message(job_id, "user", "What roles match me?")
    services_module.save_chat_message(job_id, "assistant", "You match SWE roles.")

    agent = chat_module.ChatAgent()
    assert agent.history == [], "Fresh agent should start with empty history"

    agent.set_active_job(job_id)

    assert len(agent.history) == 2
    assert agent.history[0]["role"] == "user" and agent.history[0]["content"] == "What roles match me?"
    assert agent.history[1]["role"] == "assistant" and agent.history[1]["content"] == "You match SWE roles."


def test_set_active_job_loads_persisted_summary(isolated_engine):
    """set_active_job() populates _job_summaries from a DB-persisted summary."""
    with Session(isolated_engine) as session:
        job = JobDescription(title="Summary Test", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    services_module.save_chat_summary(job_id, "User is experienced in ML and wants remote roles.")

    agent = chat_module.ChatAgent()
    assert agent._job_summaries.get(job_id) is None

    agent.set_active_job(job_id)

    assert agent._job_summaries.get(job_id) == "User is experienced in ML and wants remote roles."


def test_set_active_job_does_not_overwrite_in_session_summary(isolated_engine):
    """set_active_job() does not overwrite a summary already held in memory."""
    with Session(isolated_engine) as session:
        job = JobDescription(title="No Overwrite", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    services_module.save_chat_summary(job_id, "Old DB summary.")

    agent = chat_module.ChatAgent()
    agent._job_summaries[job_id] = "Fresher in-session summary."

    agent.set_active_job(job_id)

    assert agent._job_summaries[job_id] == "Fresher in-session summary."


def test_chat_db_write_failure_does_not_affect_response(isolated_engine, monkeypatch):
    """A DB failure in save_chat_message never surfaces as a chat error or exception."""
    monkeypatch.setattr(services_module, "save_chat_message", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("DB is down")))

    class FakeLLM:
        def invoke(self, *_a, **_kw):
            class R:
                content = "Still working fine."
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("hello")
    assert response == "Still working fine."


def test_compression_triggers_and_trims_history(isolated_engine, monkeypatch):
    """_maybe_compress_history summarizes and trims when history reaches _COMPRESS_AT."""
    class FakeLLM:
        def invoke(self, *_a, **_kw):
            class R:
                content = "Summary: user asked about Python and React skills."
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    agent = chat_module.ChatAgent()
    agent.history = [{"role": "user", "content": f"msg {i}"} for i in range(chat_module._COMPRESS_AT)]

    agent._maybe_compress_history()

    assert len(agent.history) == chat_module._COMPRESS_KEEP
    assert agent._job_summaries[None] == "Summary: user asked about Python and React skills."


def test_summary_injected_into_llm_messages(isolated_engine, monkeypatch):
    """When a summary exists, it is prepended to the LLM message list."""
    captured = []

    class CaptureLLM:
        def invoke(self, messages, *_a, **_kw):
            captured.extend(messages)
            class R:
                content = "RESPONSE: Got it."
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: CaptureLLM())

    agent = chat_module.ChatAgent()
    agent._job_summaries[None] = "Earlier: user discussed Go and Kubernetes."

    agent.chat("what skills do I have?")

    contents = [m["content"] for m in captured]
    assert any("Earlier: user discussed Go and Kubernetes." in c for c in contents), \
        "Summary not found in LLM messages"


# ── Change summary (Part A) ───────────────────────────────────────────────────

def test_tailoring_response_includes_changes_section(isolated_engine, monkeypatch):
    """_tailor_active_job appends a 'Changes made:' section to its response."""
    from sqlmodel import Session
    from database.models import JobDescription, JobSkill, Skill
    import graph.pipeline as _pipeline
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        job = JobDescription(title="SWE", company="Co", description="Build software")
        session.add(job)
        session.flush()
        skill = Skill(name="Python")
        session.add(skill)
        session.flush()
        session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id, required=True, weight=1.0))
        session.commit()
        job_id = str(job.job_id)

    def fake_match(state):
        state["matched_skills"] = {"Python": {"match_type": "direct"}}
        state["missing_skills"] = ["Kubernetes"]
        state["ats_score"] = 80.0
        return state

    def fake_tailor(state):
        state["tailored_content"] = {
            "experiences": [{"title": "Dev", "company": "Acme", "bullets": ["Built thing"]}],
            "projects": [{"name": "Proj", "bullets": ["Did stuff"]}],
            "skills_emphasized": ["Python", "FastAPI"],
        }
        state["result_id"] = ""
        return state

    def fake_format(state):
        state["formatted_resume"] = "# Resume"
        return state

    monkeypatch.setattr(_pipeline, "match_skills_node", fake_match)
    monkeypatch.setattr(_pipeline, "tailor_resume_node", fake_tailor)
    monkeypatch.setattr(_pipeline, "format_resume_node", fake_format)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent._tailor_active_job("")

    assert "Changes made:" in response
    assert "Kubernetes" in response
    assert "add missing skill" in response.lower()


def test_changes_section_shows_emphasized_skills(isolated_engine, monkeypatch):
    """'Changes made:' includes the emphasized skills when tailored_content has them."""
    from sqlmodel import Session
    from database.models import JobDescription, JobSkill, Skill
    import graph.pipeline as _pipeline
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        job = JobDescription(title="Eng", company="Inc", description="")
        session.add(job)
        session.flush()
        skill = Skill(name="Go")
        session.add(skill)
        session.flush()
        session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id, required=True, weight=1.0))
        session.commit()
        job_id = str(job.job_id)

    def fake_match(state):
        state["matched_skills"] = {"Go": {"match_type": "direct"}}
        state["missing_skills"] = []
        state["ats_score"] = 90.0
        return state

    def fake_tailor(state):
        state["tailored_content"] = {
            "experiences": [],
            "projects": [],
            "skills_emphasized": ["Go", "Docker", "Kubernetes"],
        }
        state["result_id"] = ""
        return state

    def fake_format(state):
        state["formatted_resume"] = ""
        return state

    monkeypatch.setattr(_pipeline, "match_skills_node", fake_match)
    monkeypatch.setattr(_pipeline, "tailor_resume_node", fake_tailor)
    monkeypatch.setattr(_pipeline, "format_resume_node", fake_format)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    response = agent._tailor_active_job("")

    assert "Changes made:" in response
    assert "Emphasized:" in response or "Emphasized" in response


# ── Add missing skill fast-path (Part B) ─────────────────────────────────────

def test_add_missing_skill_fast_path_bypasses_llm(isolated_engine, monkeypatch):
    """'add missing skill X' is handled without calling the LLM."""
    class ShouldNotBeCalled:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for add-skill command")

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: ShouldNotBeCalled())
    _seed_user_and_skill(isolated_engine)

    agent = chat_module.ChatAgent()
    response = agent.chat("add missing skill Docker")

    assert "docker" in response.lower() or "added" in response.lower(), (
        f"Unexpected response: {response!r}"
    )


def test_add_skill_to_profile_fast_path_bypasses_llm(isolated_engine, monkeypatch):
    """'add X to my profile' is handled without calling the LLM."""
    class ShouldNotBeCalled:
        def invoke(self, *_a, **_kw):
            raise AssertionError("LLM must not be called for add-skill command")

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: ShouldNotBeCalled())
    _seed_user_and_skill(isolated_engine)

    agent = chat_module.ChatAgent()
    response = agent.chat("add FastAPI to my profile")

    assert "fastapi" in response.lower() or "added" in response.lower(), (
        f"Unexpected response: {response!r}"
    )


def test_add_skill_fast_path_trace_label(isolated_engine):
    """_infer_fast_path labels add-skill commands correctly."""
    agent = chat_module.ChatAgent()
    assert agent._infer_fast_path("add missing skill Docker", set()) == "add_missing_skill"
    assert agent._infer_fast_path("add skill Rust", set()) == "add_missing_skill"
    assert agent._infer_fast_path("add FastAPI to my profile", set()) == "add_to_profile"
    assert agent._infer_fast_path("add Go to my skills", set()) == "add_to_profile"


# ── Fix 4: Job analysis must not create UserSkill rows ────────────────────────

def test_analyze_job_does_not_create_user_skill_rows(isolated_engine, monkeypatch):
    """_analyze_active_job writes JobSkill rows but never UserSkill rows."""
    from sqlmodel import Session, select
    from database.models import JobDescription, JobSkill, UserSkill
    import agents.job_analyzer as ja_module

    user = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        job = JobDescription(title="Data Eng", company="BigCo",
                             description="Uses Kubernetes, Spark, and Flink")
        session.add(job)
        session.commit()
        job_id = str(job.job_id)

    # Count UserSkill rows before analysis
    with Session(isolated_engine) as session:
        before = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all())

    class _FakeAnalyzer:
        def _extract_skills(self, text):
            return [
                {"name": "Kubernetes", "category": "Tool", "required": True, "weight": 0.9},
                {"name": "Apache Spark", "category": "Data", "required": True, "weight": 0.8},
                {"name": "Flink", "category": "Data", "required": False, "weight": 0.5},
            ]

    monkeypatch.setattr(ja_module, "JobAnalyzerAgent", _FakeAnalyzer)

    agent = chat_module.ChatAgent()
    agent.set_active_job(job_id)
    agent._analyze_active_job("")

    with Session(isolated_engine) as session:
        after = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all())
        job_skills = session.exec(
            select(JobSkill).where(JobSkill.job_id == job.job_id)
        ).all()

    assert after == before, (
        f"Job analysis must not create UserSkill rows. Before: {before}, After: {after}"
    )
    assert len(job_skills) == 3, f"Expected 3 JobSkill rows, got {len(job_skills)}"
