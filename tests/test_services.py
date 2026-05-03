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
