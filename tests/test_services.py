"""tui/services.py — ingestion diff, profile, and job query tests."""
import pytest
from sqlmodel import Session

import tui.services as services_module
import agents.chat as chat_module
from database.models import Skill, User, UserSkill, JobDescription, UserJobResult
from conftest import _seed_user_and_skill


def test_ingestion_diff_shows_new_skills(isolated_engine):
    """ingest_resume_file diff lists only skills not previously on the profile."""
    import uuid
    from database.user_utils import create_profile
    from database.db import engine as db_engine
    from sqlmodel import Session as S

    user = create_profile("Diff User", "diffuser@local")

    with S(db_engine) as sess:
        existing_skill = Skill(name="Python", category="language")
        sess.add(existing_skill)
        sess.commit()
        sess.refresh(existing_skill)
        sess.add(UserSkill(
            user_id=user.user_id,
            skill_id=existing_skill.skill_id,
            evidence_source="resume",
            confidence_score=0.9,
        ))
        sess.commit()

    pre = services_module._snapshot_user_data(user.user_id)

    with S(db_engine) as sess:
        new_skill = Skill(name="Rust", category="language")
        sess.add(new_skill)
        sess.commit()
        sess.refresh(new_skill)
        sess.add(UserSkill(
            user_id=user.user_id,
            skill_id=new_skill.skill_id,
            evidence_source="resume",
            confidence_score=0.8,
        ))
        sess.commit()

    result = services_module._format_ingestion_diff(
        user.user_id, pre[0], pre[1], pre[2], "test_resume.pdf"
    )

    assert "Rust" in result
    assert "Python" not in result.split("New skills")[1].split("\n")[0]
    assert "New skills (1)" in result
    assert "New experiences (0)" in result


def test_ingestion_diff_no_new_content(isolated_engine):
    """When nothing new is added, diff reports zero changes."""
    from database.user_utils import create_profile

    user = create_profile("Same User", "sameuser@local")
    pre = services_module._snapshot_user_data(user.user_id)
    result = services_module._format_ingestion_diff(
        user.user_id, pre[0], pre[1], pre[2], "repeat_resume.pdf"
    )

    assert "New skills (0)" in result
    assert "already on your profile" in result


def test_suppress_output_restores_streams():
    """_suppress_output context manager restores sys.stdout/stderr after exiting."""
    import sys
    original_stdout = sys.stdout
    original_stderr = sys.stderr

    with services_module._suppress_output():
        assert sys.stdout is not original_stdout
        assert sys.stderr is not original_stderr

    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


def test_ingest_resume_file_missing_path(isolated_engine):
    """ingest_resume_file returns error string for missing file, does not raise."""
    result = services_module.ingest_resume_file("definitely_does_not_exist_12345.md")
    assert "not found" in result.lower() or "error" in result.lower()
    assert isinstance(result, str)


def test_graph_summary_returns_structure(isolated_engine, monkeypatch):
    """get_graph_summary returns a dict with top_skills, by_category, evidence keys."""
    monkeypatch.setattr(services_module, "engine", isolated_engine)

    result = services_module.get_graph_summary(None)
    assert "top_skills" in result
    assert "by_category" in result
    assert "evidence" in result

    with Session(isolated_engine) as session:
        user = User(name="Bob", email="bob@test.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        uid = user.user_id

    result2 = services_module.get_graph_summary(uid)
    assert isinstance(result2["top_skills"], list)
    assert isinstance(result2["by_category"], dict)
    assert isinstance(result2["evidence"], dict)


def test_get_profile_data_returns_none_without_profile(isolated_engine):
    """get_profile_data returns None when no active profile exists."""
    result = services_module.get_profile_data()
    assert result is None


def test_get_profile_data_returns_structure(isolated_engine):
    """get_profile_data returns a dict with expected keys when profile exists."""
    _seed_user_and_skill(isolated_engine)
    result = services_module.get_profile_data()
    assert result is not None
    for key in ("user_id", "name", "github_username", "linkedin_url",
                "skills", "experiences", "projects", "sources"):
        assert key in result, f"Missing key: {key}"
    assert result["name"] == "Test User"
    assert result["skills"] >= 1


def test_update_profile_persists_changes(isolated_engine):
    """update_profile writes new name/github/linkedin back to the DB."""
    _seed_user_and_skill(isolated_engine)
    data = services_module.get_profile_data()
    assert data is not None

    msg = services_module.update_profile(
        data["user_id"], "Updated Name", "newgithub", "https://linkedin.com/in/test"
    )
    assert "updated" in msg.lower()

    updated = services_module.get_profile_data()
    assert updated["name"] == "Updated Name"
    assert updated["github_username"] == "newgithub"
    assert updated["linkedin_url"] == "https://linkedin.com/in/test"


def test_update_profile_persists_contact_fields(isolated_engine):
    """update_profile saves phone, email, and location to the User row."""
    from sqlmodel import select as _sel
    _seed_user_and_skill(isolated_engine)
    data = services_module.get_profile_data()
    assert data is not None

    msg = services_module.update_profile(
        data["user_id"],
        name="Test User",
        github_username="",
        linkedin_url="",
        phone="555-9876",
        email="contact@example.com",
        location="Austin, TX",
    )
    assert "updated" in msg.lower(), f"Unexpected response: {msg!r}"

    updated = services_module.get_profile_data()
    assert updated["phone"] == "555-9876"
    assert updated["email"] == "contact@example.com"
    assert updated["location"] == "Austin, TX"


def test_get_profile_data_returns_contact_fields(isolated_engine):
    """get_profile_data includes email, phone, and location keys."""
    _seed_user_and_skill(isolated_engine)
    data = services_module.get_profile_data()
    assert data is not None
    assert "email" in data
    assert "phone" in data
    assert "location" in data


def test_ingest_github_repo_invalid_ref(isolated_engine):
    """services.ingest_github_repo with an invalid ref returns an error string and does not raise."""
    result = services_module.ingest_github_repo("not-a-valid-ref")
    assert isinstance(result, str)
    assert "invalid" in result.lower() or "error" in result.lower()
    assert "not-a-valid-ref" in result


def test_delete_job_removes_job_and_results(isolated_engine):
    """delete_job removes the JobDescription and all its UserJobResult rows."""
    from sqlmodel import Session as S
    from database.models import JobDescription, UserJobResult
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)

    with S(isolated_engine) as session:
        job = JobDescription(title="Delete Me", company="Goner Inc", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        jid = job.job_id
        session.add(UserJobResult(user_id=user.user_id, job_id=jid, ats_score=55.0))
        session.commit()

    msg = services_module.delete_job(str(jid))
    assert "deleted" in msg.lower()

    with S(isolated_engine) as session:
        assert session.get(JobDescription, jid) is None
        from sqlmodel import select as sel
        leftovers = session.exec(
            sel(UserJobResult).where(UserJobResult.job_id == jid)
        ).all()
        assert len(leftovers) == 0


def test_delete_job_cleans_up_related_rows(isolated_engine):
    """delete_job removes JobSkill and ChatMessage rows so the parent delete never fails."""
    from sqlmodel import Session as S, select as sel
    from database.models import (
        ChatMessage, JobDescription, JobSkill, Skill, UserJobResult,
    )
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(isolated_engine)

    with S(isolated_engine) as session:
        skill = Skill(name="Go", category="language")
        session.add(skill)
        job = JobDescription(title="Go Role", company="Acme", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        session.refresh(skill)
        jid = job.job_id
        session.add(JobSkill(job_id=jid, skill_id=skill.skill_id))
        session.add(ChatMessage(job_id=jid, role="user", content="hello"))
        session.add(UserJobResult(user_id=user.user_id, job_id=jid, ats_score=70.0))
        session.commit()

    msg = services_module.delete_job(str(jid))
    assert "deleted" in msg.lower(), f"Expected deleted, got: {msg}"

    with S(isolated_engine) as session:
        assert session.get(JobDescription, jid) is None
        assert session.exec(sel(JobSkill).where(JobSkill.job_id == jid)).first() is None
        assert session.exec(sel(ChatMessage).where(ChatMessage.job_id == jid)).first() is None
        assert session.exec(sel(UserJobResult).where(UserJobResult.job_id == jid)).first() is None


def test_github_token_round_trip(tmp_path, monkeypatch):
    """get_github_token / save_github_token round-trip via a temp .env file."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(services_module, "_ENV_PATH", env_file)

    assert services_module.get_github_token() == ""

    services_module.save_github_token("ghp_testtoken123")
    assert services_module.get_github_token() == "ghp_testtoken123"

    services_module.save_github_token("")
    assert services_module.get_github_token() == ""


def test_update_resume_path_and_delete_resume(isolated_engine):
    """update_resume_path sets the path; delete_resume clears it without touching skills."""
    _seed_user_and_skill(isolated_engine)
    data = services_module.get_profile_data()
    assert data is not None
    user_id = data["user_id"]

    services_module.update_resume_path(user_id, "/path/to/my_resume.pdf")
    assert services_module.get_resume_path(user_id) == "/path/to/my_resume.pdf"

    services_module.delete_resume(user_id)
    assert services_module.get_resume_path(user_id) is None

    # Skills must be untouched after resume delete
    data_after = services_module.get_profile_data()
    assert data_after["skills"] >= 1


def test_ingest_github_repo_summary_mentions_single_repo(isolated_engine, monkeypatch):
    """ingest_github_repo summary clearly says 'single repo'."""
    import ingestion.github as gh_module
    import agents.parser as parser_module
    from database.user_utils import create_profile

    create_profile("Test User", "test@local")

    fake_repo = {
        "name": "evals",
        "description": "Evals for LLMs",
        "url": "https://github.com/openai/evals",
        "stars": 10,
        "updated_at": "2024-01-01T00:00:00Z",
        "languages": ["Python"],
        "readme": "# evals",
        "dependencies": {},
        "owner": "openai",
    }
    monkeypatch.setattr(gh_module.GitHubIngestor, "fetch_repo", lambda owner, repo_name, token="": fake_repo)
    monkeypatch.setattr(parser_module.ResumeParserAgent, "parse_and_save", lambda self, data: None)

    result = services_module.ingest_github_repo("openai/evals")
    assert "single repo" in result.lower()
    assert "openai" in result
    assert "evals" in result


# ── PRD 10 — Persistent chat history ──────────────────────────────────────────

def test_save_and_load_chat_history_for_job(isolated_engine):
    """save_chat_message + load_chat_history round-trip for a specific job."""
    from sqlmodel import Session as S
    from database.models import JobDescription

    with S(isolated_engine) as session:
        job = JobDescription(title="Test Job", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    services_module.save_chat_message(job_id, "user", "Hello agent")
    services_module.save_chat_message(job_id, "assistant", "Hello user")

    history = services_module.load_chat_history(job_id)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "Hello agent"}
    assert history[1] == {"role": "assistant", "content": "Hello user"}


def test_load_chat_history_landing_context(isolated_engine):
    """load_chat_history with job_id=None returns landing-context messages."""
    services_module.save_chat_message(None, "user", "landing message")
    services_module.save_chat_message(None, "assistant", "landing reply")

    history = services_module.load_chat_history(None)
    assert len(history) == 2
    assert history[0] == {"role": "user", "content": "landing message"}
    assert history[1] == {"role": "assistant", "content": "landing reply"}


def test_load_chat_history_limit(isolated_engine):
    """load_chat_history with limit=2 returns only the 2 most recent messages, oldest-first."""
    from sqlmodel import Session as S
    from database.models import JobDescription

    with S(isolated_engine) as session:
        job = JobDescription(title="Limit Test", company="Ltd", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    for i in range(4):
        services_module.save_chat_message(job_id, "user", f"msg {i}")

    history = services_module.load_chat_history(job_id, limit=2)
    assert len(history) == 2
    # Should be the 2 most recent (msg 2 and msg 3), in oldest-first order.
    assert history[0]["content"] == "msg 2"
    assert history[1]["content"] == "msg 3"


def test_prune_chat_messages_caps_at_limit(isolated_engine):
    """Saving more than _MAX_CHAT_MESSAGES_PER_JOB messages prunes oldest entries."""
    from sqlmodel import Session as S
    from database.models import JobDescription

    with S(isolated_engine) as session:
        job = JobDescription(title="Prune Test", company="Co", description="")
        session.add(job)
        session.commit()
        session.refresh(job)
        job_id = str(job.job_id)

    limit = services_module._MAX_CHAT_MESSAGES_PER_JOB
    for i in range(limit + 5):
        services_module.save_chat_message(job_id, "user", f"msg {i}")

    history = services_module.load_chat_history(job_id, limit=limit + 10)
    assert len(history) == limit, f"Expected {limit} messages, got {len(history)}"
    # Oldest 5 are pruned (one per save once over the limit), newest 100 retained.
    assert history[-1]["content"] == f"msg {limit + 4}"
    assert history[0]["content"] == "msg 5"


# ── add_skill_to_profile ───────────────────────────────────────────────────────

def test_add_skill_to_profile_creates_skill_and_link(isolated_engine):
    """add_skill_to_profile creates the Skill row and UserSkill link."""
    from sqlmodel import select as _sel
    user = _seed_user_and_skill(isolated_engine)

    result = services_module.add_skill_to_profile(user.user_id, "Docker")
    assert "added" in result.lower(), f"Unexpected response: {result!r}"

    with Session(isolated_engine) as session:
        skill = session.exec(_sel(Skill).where(Skill.name == "Docker")).first()
        assert skill is not None
        link = session.exec(
            _sel(UserSkill).where(
                UserSkill.user_id == user.user_id,
                UserSkill.skill_id == skill.skill_id,
            )
        ).first()
        assert link is not None
        assert link.evidence_source == "manual"


def test_add_skill_to_profile_duplicate_returns_message(isolated_engine):
    """Adding the same skill twice returns 'already in your profile'."""
    user = _seed_user_and_skill(isolated_engine)

    services_module.add_skill_to_profile(user.user_id, "Kubernetes")
    result = services_module.add_skill_to_profile(user.user_id, "Kubernetes")
    assert "already" in result.lower(), f"Expected 'already' message, got: {result!r}"


def test_add_skill_to_profile_case_insensitive_match(isolated_engine):
    """A skill with the same name (different case) reuses the existing Skill row."""
    from sqlmodel import select as _sel
    user = _seed_user_and_skill(isolated_engine)

    # Pre-create skill with lowercase name
    with Session(isolated_engine) as session:
        skill = Skill(name="fastapi")
        session.add(skill)
        session.commit()

    result = services_module.add_skill_to_profile(user.user_id, "FastAPI")
    assert "added" in result.lower()

    with Session(isolated_engine) as session:
        skills = session.exec(_sel(Skill).where(Skill.name == "fastapi")).all()
        assert len(skills) == 1, "Should reuse existing Skill row"


def test_add_skill_to_profile_empty_name_returns_error(isolated_engine):
    """An empty skill name returns a helpful error without raising."""
    user = _seed_user_and_skill(isolated_engine)
    result = services_module.add_skill_to_profile(user.user_id, "")
    assert "provide" in result.lower() or "name" in result.lower()


# ── Skills data quality (PRD 05) ──────────────────────────────────────────────

def test_format_skill_source_github_repo(isolated_engine):
    """GitHub repo sources display as 'GitHub: <repo-name>'."""
    assert services_module._format_skill_source("github:octocat/hello-world") == "GitHub: hello-world"
    assert services_module._format_skill_source("github:openai/evals") == "GitHub: evals"


def test_format_skill_source_github_user_level(isolated_engine):
    """Account-level GitHub ingestion displays as 'GitHub: <username>'."""
    assert services_module._format_skill_source("github:myuser") == "GitHub: myuser"


def test_format_skill_source_resume(isolated_engine):
    """File paths and 'resume' literal are both displayed as 'resume'."""
    assert services_module._format_skill_source("resume") == "resume"
    assert services_module._format_skill_source("/home/user/my_resume.pdf") == "resume"
    assert services_module._format_skill_source("C:/Users/foo/resume.docx") == "resume"
    assert services_module._format_skill_source("") == "resume"


def test_format_skill_source_manual(isolated_engine):
    """Manual-added skills display as 'manual'."""
    assert services_module._format_skill_source("manual") == "manual"
    assert services_module._format_skill_source("manual:some-target") == "manual"


def test_get_skills_deduplicates_by_name(isolated_engine):
    """get_skills merges multiple UserSkill rows for the same skill into one display row."""
    from sqlmodel import select as _sel
    user = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        # Add a second UserSkill row for Python from GitHub (different source)
        python_skill = session.exec(_sel(Skill).where(Skill.name == "Python")).first()
        assert python_skill is not None
        session.add(UserSkill(
            user_id=user.user_id,
            skill_id=python_skill.skill_id,
            evidence_source="github:myuser/my-repo",
            confidence_score=0.8,
            proficiency=4,
        ))
        session.commit()

    rows = services_module.get_skills(user.user_id)
    python_rows = [r for r in rows if r["name"].lower() == "python"]

    assert len(python_rows) == 1, f"Expected 1 merged Python row, got {len(python_rows)}"
    merged = python_rows[0]
    assert "resume" in merged["source"]
    assert "GitHub: my-repo" in merged["source"]
    # Highest confidence (0.95) wins; proficiency from that entry
    assert float(merged["confidence"]) >= 0.8


def test_get_skills_dedup_uses_highest_confidence_proficiency(isolated_engine):
    """The merged skill row uses the proficiency from the highest-confidence entry."""
    from sqlmodel import select as _sel
    user = _seed_user_and_skill(isolated_engine)

    with Session(isolated_engine) as session:
        skill = Skill(name="Go", category="Language")
        session.add(skill)
        session.flush()
        session.add(UserSkill(
            user_id=user.user_id, skill_id=skill.skill_id,
            evidence_source="resume", confidence_score=0.6, proficiency=2,
        ))
        session.add(UserSkill(
            user_id=user.user_id, skill_id=skill.skill_id,
            evidence_source="github:owner/repo", confidence_score=0.9, proficiency=4,
        ))
        session.commit()

    rows = services_module.get_skills(user.user_id)
    go_rows = [r for r in rows if r["name"].lower() == "go"]
    assert len(go_rows) == 1
    assert go_rows[0]["proficiency"] == "4"  # from the higher-confidence github entry


def test_ingest_resume_normalizes_source_to_resume(isolated_engine, tmp_path, monkeypatch):
    """ingest_resume_file passes source_file='resume' to parse_and_save for all file types."""
    import agents.parser as parser_module
    _seed_user_and_skill(isolated_engine)

    captured = []

    class _FakeParser:
        def parse_and_save(self, data):
            captured.append(data.get("source_file"))

    monkeypatch.setattr(parser_module, "ResumeParserAgent", lambda: _FakeParser())

    resume1 = tmp_path / "resume_v1.md"
    resume1.write_text("# Resume\n- Python\n")
    services_module.ingest_resume_file(str(resume1))

    resume2 = tmp_path / "resume_v2.md"
    resume2.write_text("# Resume v2\n- FastAPI\n")
    services_module.ingest_resume_file(str(resume2))

    assert captured == ["resume", "resume"], (
        f"Both ingestions should use source_file='resume', got: {captured}"
    )
