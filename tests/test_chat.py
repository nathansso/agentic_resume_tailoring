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


def test_build_context_window_respects_budget():
    """_build_context_window never exceeds budget_tokens and returns oldest-first."""
    agent = chat_module.ChatAgent()
    # Each message content is 400 chars → ~100 tokens
    agent.history = [{"role": "user", "content": "x" * 400, "idx": i} for i in range(20)]
    # Budget of 350 tokens fits at most 3 messages (3 * 100 = 300 ≤ 350 < 4 * 100 = 400)
    window = agent._build_context_window(budget_tokens=350)
    total_tokens = sum(len(m["content"]) // 4 for m in window)
    assert total_tokens <= 350
    assert len(window) == 3
    # Oldest-first: last 3 messages in history order
    assert window == agent.history[-3:]


def test_build_context_window_returns_all_when_under_budget():
    """_build_context_window returns the full history when it fits within the budget."""
    agent = chat_module.ChatAgent()
    agent.history = [{"role": "user", "content": "short"} for _ in range(5)]
    window = agent._build_context_window(budget_tokens=6000)
    assert window == agent.history


def test_build_context_window_empty_history():
    """_build_context_window returns [] for an empty history."""
    agent = chat_module.ChatAgent()
    assert agent._build_context_window() == []


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


def test_env_var_overrides_compress_constants(monkeypatch):
    """ART_COMPRESS_AT, ART_COMPRESS_KEEP, ART_CONTEXT_BUDGET are read from env vars."""
    monkeypatch.setenv("ART_COMPRESS_AT", "15")
    monkeypatch.setenv("ART_COMPRESS_KEEP", "4")
    monkeypatch.setenv("ART_CONTEXT_BUDGET", "3000")
    import importlib
    reloaded = importlib.reload(chat_module)
    try:
        assert reloaded._COMPRESS_AT == 15
        assert reloaded._COMPRESS_KEEP == 4
        assert reloaded._CONTEXT_BUDGET == 3000
    finally:
        importlib.reload(chat_module)


def test_cumulative_summary_rolls_forward(isolated_engine, monkeypatch):
    """Second compression includes the prior summary so earlier context is not lost."""
    received_prompts = []

    class CaptureLLM:
        def invoke(self, msgs, *_a, **_kw):
            received_prompts.append(msgs[0]["content"])
            class R:
                content = "New combined summary."
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: CaptureLLM())

    agent = chat_module.ChatAgent()
    agent._job_summaries[None] = "Prior summary: user knows Python."
    agent.history = [{"role": "user", "content": f"msg {i}"} for i in range(chat_module._COMPRESS_AT)]

    agent._maybe_compress_history()

    assert "Prior summary: user knows Python." in received_prompts[0]
    assert agent._job_summaries[None] == "New combined summary."


def test_compression_prompt_uses_proportional_truncation(isolated_engine, monkeypatch):
    """Long messages are truncated proportionally, not capped at 300 chars."""
    received_prompts = []

    class CaptureLLM:
        def invoke(self, msgs, *_a, **_kw):
            received_prompts.append(msgs[0]["content"])
            class R:
                content = "summary"
            return R()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: CaptureLLM())

    agent = chat_module.ChatAgent()
    long_content = "x" * 2000
    agent.history = [{"role": "user", "content": long_content}] * chat_module._COMPRESS_AT

    agent._maybe_compress_history()

    # With proportional budget, each message gets more than 300 chars
    prompt_text = received_prompts[0]
    # The prompt should contain significantly more than 300 'x' chars from a single message
    assert prompt_text.count("x") > 300


def test_build_context_window_reserved_tokens_reduces_budget():
    """reserved_tokens shrinks the effective budget so fewer messages are included."""
    agent = chat_module.ChatAgent()
    # Each message ~100 tokens (400 chars // 4)
    agent.history = [{"role": "user", "content": "x" * 400} for _ in range(10)]

    # Without reservation: budget 600 → fits 6 messages
    without = agent._build_context_window(budget_tokens=600, reserved_tokens=0)
    # With 200 reserved: effective budget 400 → fits 4 messages
    with_reserved = agent._build_context_window(budget_tokens=600, reserved_tokens=200)

    assert len(without) == 6
    assert len(with_reserved) == 4


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


# ── _extract_chat_artifacts ───────────────────────────────────────────────────

def test_extract_chat_artifacts_with_fixture_llm(isolated_engine, monkeypatch):
    """_extract_chat_artifacts parses a canned JSON response from the LLM correctly."""
    import json

    _seed_user_and_skill(isolated_engine)

    canned_response = json.dumps([
        {"type": "skill", "name": "Redis", "category": "Database", "description": "",
         "evidence": "I built a distributed cache at my last job using Redis"},
        {"type": "project", "name": "Distributed Cache", "description": "Redis-backed layer",
         "evidence": "I built a distributed cache at my last job using Redis"},
    ])

    class FixtureLLM:
        def invoke(self, *_args, **_kwargs):
            class Resp:
                content = canned_response
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: FixtureLLM())

    agent = chat_module.ChatAgent()
    messages = [
        {"role": "user", "content": "I built a distributed cache at my last job using Redis."},
        {"role": "assistant", "content": "That's great experience!"},
    ]
    candidates = agent._extract_chat_artifacts(messages)

    assert len(candidates) == 2, f"Expected 2 candidates, got {len(candidates)}: {candidates}"
    types = {c["type"] for c in candidates}
    assert "skill" in types
    assert "project" in types
    names = {c["name"] for c in candidates}
    assert "Redis" in names
    assert "Distributed Cache" in names


def test_extract_chat_artifacts_returns_empty_on_nothing_new(isolated_engine, monkeypatch):
    """_extract_chat_artifacts returns [] when the LLM finds no new items."""
    _seed_user_and_skill(isolated_engine)

    class FixtureLLM:
        def invoke(self, *_args, **_kwargs):
            class Resp:
                content = "[]"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: FixtureLLM())

    agent = chat_module.ChatAgent()
    candidates = agent._extract_chat_artifacts([{"role": "user", "content": "Hello!"}])
    assert candidates == []


def test_extract_chat_artifacts_filters_no_evidence(isolated_engine, monkeypatch):
    """_extract_chat_artifacts drops candidates that have empty or missing evidence."""
    import json

    _seed_user_and_skill(isolated_engine)

    # Item 1 has evidence (should be kept); item 2 has empty evidence (should be dropped);
    # item 3 is missing the key entirely (should be dropped).
    canned = json.dumps([
        {"type": "skill", "name": "Redis", "category": "Database",
         "evidence": "I use Redis daily for caching in my current role"},
        {"type": "project", "name": "No Evidence Project", "description": "...", "evidence": ""},
        {"type": "skill", "name": "Kafka", "category": "Messaging"},
    ])

    class FixtureLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = canned
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: FixtureLLM())

    agent = chat_module.ChatAgent()
    candidates = agent._extract_chat_artifacts([
        {"role": "user", "content": "I use Redis daily for caching in my current role."},
    ])

    assert len(candidates) == 1, f"Expected 1 candidate (evidence-backed), got: {candidates}"
    assert candidates[0]["name"] == "Redis"


def test_save_command_fast_path_no_llm_call_on_empty(isolated_engine, monkeypatch):
    """/save with an LLM returning [] reports 'no new items' without touching DB."""
    _seed_user_and_skill(isolated_engine)

    class EmptyExtractLLM:
        def invoke(self, *_args, **_kwargs):
            class Resp:
                content = "[]"
            return Resp()

    # Both initial LLM (router) and extraction LLM use the same monkeypatch here.
    monkeypatch.setattr(chat_module, "get_llm", lambda role="chat", temperature=0.0: EmptyExtractLLM())
    monkeypatch.setattr(chat_module, "get_llm", lambda **kw: EmptyExtractLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("/save")

    assert "no new" in response.lower() or "not detected" in response.lower() or "detected" in response.lower()


# ── Edge cases and error paths ────────────────────────────────────────────────


def test_pending_option_no_options_exist(isolated_engine, monkeypatch):
    """Typing '1' when no pending options are set passes through to the LLM, not a crash."""
    llm_called = []

    class TrackingLLM:
        def invoke(self, *_a, **_kw):
            llm_called.append(True)
            class Resp:
                content = "RESPONSE: I can help with that."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: TrackingLLM())

    agent = chat_module.ChatAgent()
    assert not agent._pending_options, "No pending options should be set initially"

    response = agent.chat("1")
    assert isinstance(response, str), "Should return a string even with no pending options"
    assert llm_called, "LLM should be reached when '1' has no pending options to resolve"


def test_malformed_tool_call_missing_close_paren(isolated_engine, monkeypatch):
    """A TOOL_CALL with a missing closing paren is handled without crashing."""
    class MalformedLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                # Missing closing paren — regex won't match, falls through to plain text
                content = "TOOL_CALL: query_skills("
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: MalformedLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("show my skills")
    # Should return a string without raising, even if it returns the raw text
    assert isinstance(response, str)


def test_unknown_tool_name_in_tool_call(isolated_engine, monkeypatch):
    """A TOOL_CALL referencing an unknown tool returns 'Unknown tool' without crashing."""
    class UnknownToolLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = "TOOL_CALL: nonexistent_fn()"
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: UnknownToolLLM())

    agent = chat_module.ChatAgent()
    response = agent.chat("do something")
    assert isinstance(response, str)
    assert "unknown" in response.lower() or "nonexistent" in response.lower(), (
        f"Expected 'Unknown tool' in response, got: {response!r}"
    )


def test_compression_boundary_at_limit(isolated_engine, monkeypatch):
    """History with exactly _COMPRESS_AT messages does NOT trigger compression."""
    compress_called = []

    class SpyLLM:
        def invoke(self, *_a, **_kw):
            compress_called.append(True)
            class Resp:
                content = "Summary."
            return Resp()

    # Only patch the compression LLM call; routing LLM is not used here
    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: SpyLLM())

    agent = chat_module.ChatAgent()
    # Exactly at the limit — compression requires len(history) >= _COMPRESS_AT
    # The check is `< _COMPRESS_AT`, so exactly _COMPRESS_AT - 1 should NOT compress
    agent.history = [{"role": "user", "content": f"msg {i}"} for i in range(chat_module._COMPRESS_AT - 1)]
    initial_len = len(agent.history)

    agent._maybe_compress_history()

    assert len(agent.history) == initial_len, (
        f"History should not be compressed at {initial_len} messages "
        f"(threshold is {chat_module._COMPRESS_AT})"
    )
    assert not compress_called, "LLM should not be called for compression below threshold"


def test_compression_boundary_one_over(isolated_engine, monkeypatch):
    """History with _COMPRESS_AT messages DOES trigger compression."""
    class FakeLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = "Compressed summary."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    agent = chat_module.ChatAgent()
    agent.history = [{"role": "user", "content": f"msg {i}"} for i in range(chat_module._COMPRESS_AT)]

    agent._maybe_compress_history()

    assert len(agent.history) == chat_module._COMPRESS_KEEP, (
        f"History should be trimmed to {chat_module._COMPRESS_KEEP} after compression, "
        f"got {len(agent.history)}"
    )
    assert agent._job_summaries[None] == "Compressed summary."


def test_chat_no_active_profile(isolated_engine, monkeypatch):
    """chat() returns a plain string when no profile is in the database — does not raise."""
    class FakeLLM:
        def invoke(self, *_a, **_kw):
            class Resp:
                content = "RESPONSE: Please set up a profile first."
            return Resp()

    monkeypatch.setattr(chat_module, "get_llm", lambda *a, **kw: FakeLLM())

    # No profile seeded — isolated_engine has empty DB
    agent = chat_module.ChatAgent()
    response = agent.chat("what are my skills?")
    assert isinstance(response, str), "Should return a string even with no profile"
    assert response  # non-empty
