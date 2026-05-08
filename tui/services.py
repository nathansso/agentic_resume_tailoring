"""
DB query functions and ingestion service functions for the TUI.
Query functions return plain data (lists/dicts) for widget rendering.
Ingestion functions return plain-English result strings and never raise.
"""
import contextlib
import io
import logging
import sys
from pathlib import Path
from typing import Optional
from uuid import UUID

_ENV_PATH = Path(__file__).parent.parent / ".env"

from sqlmodel import Session, select

from database.db import engine
from database.models import (
    ChatMessage, Experience, JobDescription, Project,
    Skill, User, UserJobResult, UserSkill,
)

logger = logging.getLogger(__name__)


@contextlib.contextmanager
def _suppress_output():
    """Redirect stdout/stderr during heavy ingestion to prevent TUI corruption."""
    buf = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = buf
    sys.stderr = buf
    try:
        yield
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def _snapshot_user_data(user_id: UUID) -> tuple[set, set, set]:
    """Return (skill_ids, exp_ids, proj_ids) for the user before ingestion."""
    with Session(engine) as session:
        skill_ids = {us.skill_id for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()}
        exp_ids = {str(e.experience_id) for e in session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all()}
        proj_ids = {str(p.project_id) for p in session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all()}
    return skill_ids, exp_ids, proj_ids


def _format_ingestion_diff(
    user_id: UUID,
    pre_skill_ids: set,
    pre_exp_ids: set,
    pre_proj_ids: set,
    label: str,
) -> str:
    """Return a human-readable summary of what was added during ingestion."""
    with Session(engine) as session:
        # New skills: skill_ids not seen before this ingestion
        new_skill_names = []
        for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all():
            if us.skill_id not in pre_skill_ids:
                skill = session.get(Skill, us.skill_id)
                if skill and skill.name not in new_skill_names:
                    new_skill_names.append(skill.name)
                pre_skill_ids.add(us.skill_id)  # dedupe within this diff

        # New experiences
        new_exps = []
        for e in session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all():
            if str(e.experience_id) not in pre_exp_ids:
                new_exps.append(f"{e.title} @ {e.company}")

        # New projects
        new_projs = []
        for p in session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all():
            if str(p.project_id) not in pre_proj_ids:
                new_projs.append(p.name)

        total_skills = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all())
        total_exps = len(session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all())
        total_projs = len(session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all())

    lines = [f"Ingested: {label}", ""]

    if new_skill_names:
        preview = ", ".join(new_skill_names[:12])
        if len(new_skill_names) > 12:
            preview += f" (+{len(new_skill_names) - 12} more)"
        lines.append(f"New skills ({len(new_skill_names)}): {preview}")
    else:
        lines.append("New skills (0): all skills already on your profile")

    if new_exps:
        lines.append(f"New experiences ({len(new_exps)}): " + ", ".join(new_exps[:5]))
    else:
        lines.append("New experiences (0): none")

    if new_projs:
        lines.append(f"New projects ({len(new_projs)}): " + ", ".join(new_projs[:5]))
    else:
        lines.append("New projects (0): none")

    lines.append(f"\nProfile total: {total_skills} skills · {total_exps} experiences · {total_projs} projects")
    return "\n".join(lines)


def get_first_user_id() -> Optional[UUID]:
    from database.user_utils import get_active_profile
    user = get_active_profile()
    return user.user_id if user else None


def get_graph_summary(user_id: Optional[UUID]) -> dict:
    """Return structured graph data: top_skills, by_category, evidence."""
    if user_id is None:
        return {"top_skills": [], "by_category": {}, "evidence": {}}
    try:
        from knowledge_graph.builder import SkillGraphBuilder
        G = SkillGraphBuilder().build_graph()
    except Exception:
        return {"top_skills": [], "by_category": {}, "evidence": {}}

    skill_nodes = [(n, d) for n, d in G.nodes(data=True) if d.get("type") == "Skill"]

    # Top skills by in-degree (how many projects/experiences reference them)
    scored = sorted(
        [(d.get("name", n), G.in_degree(n)) for n, d in skill_nodes],
        key=lambda x: x[1],
        reverse=True,
    )
    top_skills = [{"name": name, "connections": count} for name, count in scored[:10]]

    # Count per category
    by_category: dict[str, int] = {}
    for _, d in skill_nodes:
        cat = d.get("category") or "Uncategorized"
        by_category[cat] = by_category.get(cat, 0) + 1

    # Evidence: for top 5 skills, list which projects/experiences reference them
    evidence: dict[str, list[str]] = {}
    for node, d in skill_nodes:
        name = d.get("name", node)
        if name in {s["name"] for s in top_skills[:5]}:
            sources = [
                G.nodes[p].get("name", p)
                for p in G.predecessors(node)
            ]
            if sources:
                evidence[name] = sources

    return {"top_skills": top_skills, "by_category": by_category, "evidence": evidence}


def get_profile_data() -> Optional[dict]:
    """Return the active profile's editable fields and stats, or None if no profile."""
    from database.user_utils import get_active_profile
    user = get_active_profile()
    if not user:
        return None
    with Session(engine) as session:
        skill_count = len(session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all())
        exp_count = len(session.exec(
            select(Experience).where(Experience.user_id == user.user_id)
        ).all())
        proj_count = len(session.exec(
            select(Project).where(Project.user_id == user.user_id)
        ).all())
        sources: set[str] = set()
        for us in session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all():
            if us.evidence_source:
                sources.add(us.evidence_source.split(":")[0])
    return {
        "user_id": user.user_id,
        "name": user.name or "",
        "github_username": user.github_username or "",
        "linkedin_url": user.linkedin_url or "",
        "skills": skill_count,
        "experiences": exp_count,
        "projects": proj_count,
        "sources": sorted(sources),
    }


def update_profile(user_id: UUID, name: str, github_username: str, linkedin_url: str) -> str:
    """Update the active profile's personal info fields."""
    from datetime import datetime
    with Session(engine) as session:
        user = session.get(User, user_id)
        if not user:
            return "Profile not found."
        user.name = name.strip() or user.name
        user.github_username = github_username.strip() or None
        user.linkedin_url = linkedin_url.strip() or None
        user.updated_at = datetime.utcnow()
        session.add(user)
        session.commit()
    return "Profile updated."


def ingest_github_for_profile(user_id: Optional[UUID], username: str) -> str:
    """Ingest GitHub repos for the active profile."""
    return ingest_github(username)


def get_skills(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()
        rows = []
        for us in user_skills:
            skill = session.get(Skill, us.skill_id)
            if skill:
                source = (us.evidence_source or "unknown").split(":")[0]
                rows.append({
                    "name": skill.name,
                    "category": skill.category or "Uncategorized",
                    "source": source,
                    "proficiency": str(us.proficiency or "N/A"),
                    "confidence": f"{us.confidence_score:.1f}",
                })
        return rows


def get_experiences(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        exps = session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all()
        return [
            {
                "title": e.title,
                "company": e.company,
                "start": e.start_date or "?",
                "end": e.end_date or "?",
            }
            for e in exps
        ]


def get_projects(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        projs = session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all()
        return [
            {
                "name": p.name,
                "url": p.repo_url or "—",
                "desc": (p.description or "")[:60],
            }
            for p in projs
        ]


def get_jobs() -> list[dict]:
    with Session(engine) as session:
        jobs = session.exec(select(JobDescription)).all()
        result = []
        for job in jobs:
            results = session.exec(
                select(UserJobResult).where(UserJobResult.job_id == job.job_id)
            ).all()
            score = ""
            if results:
                best = max(r.ats_score for r in results)
                score = f" [{best:.0f}%]"
            result.append({
                "job_id": str(job.job_id),
                "title": job.title,
                "company": job.company,
                "score": score,
                "status": getattr(job, "status", "created"),
            })
        return result


def get_job_details(job_uuid: str) -> Optional[dict]:
    with Session(engine) as session:
        job = session.get(JobDescription, UUID(job_uuid))
        if not job:
            return None
        results = session.exec(
            select(UserJobResult).where(UserJobResult.job_id == job.job_id)
        ).all()
        detail: dict = {
            "title": job.title,
            "company": job.company,
            "status": getattr(job, "status", "created"),
            "description": job.description or "",
        }
        if results:
            latest = max(results, key=lambda r: r.created_at)
            detail["ats_score"] = latest.ats_score
            detail["matched_skills"] = list(latest.matched_skills.keys())[:10] if latest.matched_skills else []
            detail["missing_skills"] = latest.missing_skills[:10] if latest.missing_skills else []
        return detail


def compute_app_state() -> str:
    """Return 'setup' or 'profile_ready' based on current DB state."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "setup"
        skill = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id).limit(1)
        ).first()
        return "profile_ready" if skill else "setup"


# ── GitHub token (stored in .env, never in SQLite) ──────────

def get_github_token() -> str:
    """Read GITHUB_TOKEN from .env. Returns '' if not set."""
    from dotenv import dotenv_values
    return dotenv_values(_ENV_PATH).get("GITHUB_TOKEN", "") or ""


def save_github_token(token: str) -> None:
    """Write GITHUB_TOKEN to .env via dotenv. If token is '', remove the key. Never log the value."""
    from dotenv import set_key, unset_key
    _ENV_PATH.touch()
    if token:
        set_key(str(_ENV_PATH), "GITHUB_TOKEN", token)  # token value intentionally not logged
    else:
        unset_key(str(_ENV_PATH), "GITHUB_TOKEN")


# ── Resume path (stored on User row) ────────────────────────

def get_resume_path(user_id: UUID) -> Optional[str]:
    """Return resume_path for the given user, or None."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        return user.resume_path if user else None


def update_resume_path(user_id: UUID, path: str) -> None:
    """Set resume_path on the User row."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.resume_path = path
            session.add(user)
            session.commit()


def add_skill_to_profile(user_id: UUID, skill_name: str, target: Optional[str] = None) -> str:
    """Add a skill to the user's profile. Returns plain-English result. Never raises."""
    try:
        skill_name = skill_name.strip()
        if not skill_name:
            return "Please provide a skill name."
        with Session(engine) as session:
            skill = session.exec(select(Skill).where(Skill.name == skill_name)).first()
            if not skill:
                all_skills = session.exec(select(Skill)).all()
                skill = next((s for s in all_skills if s.name.lower() == skill_name.lower()), None)
            if not skill:
                skill = Skill(name=skill_name)
                session.add(skill)
                session.flush()
            existing = session.exec(
                select(UserSkill).where(
                    UserSkill.user_id == user_id,
                    UserSkill.skill_id == skill.skill_id,
                )
            ).first()
            if existing:
                return f"'{skill_name}' is already in your profile."
            evidence = f"manual:{target}" if target else "manual"
            user_skill = UserSkill(
                user_id=user_id,
                skill_id=skill.skill_id,
                proficiency=3,
                evidence_source="manual",
                confidence_score=0.7,
                evidence_detail=evidence,
            )
            session.add(user_skill)
            session.commit()
        return f"Added '{skill_name}' to your profile."
    except Exception as e:
        logger.error("add_skill_to_profile failed: %s", e)
        return f"Failed to add skill: {e}"


def delete_resume(user_id: UUID) -> None:
    """Clear resume_path on the User row. Does not delete the file or any ingested data."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.resume_path = None
            session.add(user)
            session.commit()


def delete_job(job_uuid: str) -> str:
    """Delete a JobDescription and all its UserJobResult rows.
    Returns plain-English result. Never raises."""
    try:
        from uuid import UUID as _UUID
        jid = _UUID(job_uuid)
        with Session(engine) as session:
            for row in session.exec(
                select(UserJobResult).where(UserJobResult.job_id == jid)
            ).all():
                session.delete(row)
            session.commit()
            job = session.get(JobDescription, jid)
            if job:
                session.delete(job)
                session.commit()
        return "Job deleted."
    except Exception as e:
        return f"Failed to delete job: {e}"


# ── Chat history (persisted per job) ────────────────────────

def save_chat_message(job_id: Optional[str], role: str, content: str) -> None:
    """Persist one message to the ChatMessage table. Never raises."""
    try:
        jid = UUID(job_id) if job_id else None
        with Session(engine) as session:
            session.add(ChatMessage(job_id=jid, role=role, content=content))
            session.commit()
    except Exception as e:
        logger.warning("save_chat_message failed: %s", e)


def load_chat_history(job_id: Optional[str], limit: int = 20) -> list[dict]:
    """Return the last `limit` messages for this job as {role, content} dicts, oldest-first.
    Returns [] if none found or on error."""
    try:
        jid = UUID(job_id) if job_id else None
        with Session(engine) as session:
            msgs = session.exec(
                select(ChatMessage)
                .where(ChatMessage.job_id == jid)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            ).all()
        return [{"role": m.role, "content": m.content} for m in reversed(msgs)]
    except Exception as e:
        logger.warning("load_chat_history failed: %s", e)
        return []


# ── Ingestion service functions ─────────────────────────────
# Each returns a plain-English result string and never raises.

def ingest_resume_file(file_path: str) -> str:
    """Parse a resume file (MD, PDF, DOCX) and save to DB."""
    from pathlib import Path
    path = Path(file_path)
    if not path.exists():
        return f"File not found: {file_path}"
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            if file_path.endswith(".md"):
                ingestion_data = {
                    "source_file": file_path,
                    "full_text": path.read_text(encoding="utf-8"),
                    "parsed_sections": {},
                }
            else:
                from ingestion.resume import ResumeIngestor
                ingestion_data = ResumeIngestor().ingest(file_path)
            from agents.parser import ResumeParserAgent
            ResumeParserAgent().parse_and_save(ingestion_data)
        if user:
            return _format_ingestion_diff(user.user_id, pre[0], pre[1], pre[2], path.name)
        return f"Resume ingested: {path.name}."
    except Exception as e:
        return f"Ingestion failed: {e}"


def ingest_github(username: str = "") -> str:
    """Fetch GitHub repos for a user and save skills/projects to DB."""
    from config import GITHUB_USERNAME
    target = username.strip() or GITHUB_USERNAME
    if not target:
        return "No GitHub username provided and GITHUB_USERNAME is not set in .env."
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repos = GitHubIngestor(username=target).ingest()
            if not repos:
                return f"No new or updated repos found for {target}."
            lines = []
            for repo in repos:
                desc = repo.get("description") or "No description"
                langs = ", ".join(repo.get("languages", []))
                lines += [
                    f"Project: {repo['name']}", f"Description: {desc}",
                    f"Languages: {langs}", f"URL: {repo.get('url', '')}",
                ]
                if repo.get("readme"):
                    lines.append(f"README:\n{repo['readme']}")
                for dep_file, dep_content in repo.get("dependencies", {}).items():
                    lines.append(f"{dep_file}:\n{dep_content}")
                lines.append("")
            ResumeParserAgent().parse_and_save({
                "source_file": f"github:{target}",
                "full_text": "\n".join(lines),
                "parsed_sections": {},
            })
        if user:
            return _format_ingestion_diff(user.user_id, pre[0], pre[1], pre[2], f"github:{target} ({len(repos)} repos)")
        return f"GitHub ingested: {len(repos)} repos parsed for {target}."
    except Exception as e:
        return f"GitHub ingestion failed: {e}"


def parse_github_repo_ref(repo_ref: str) -> "tuple[str, str] | None":
    """Parse a GitHub repo ref into (owner, repo_name). Accepts owner/repo or full GitHub URLs."""
    import re
    ref = repo_ref.strip().rstrip("/")
    m = re.match(r'^https?://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?$', ref)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r'^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$', ref)
    if m:
        return m.group(1), m.group(2)
    return None


def ingest_github_repo(repo_ref: str) -> str:
    """Fetch a single GitHub repo and save to DB. Returns a plain-English summary string and never raises."""
    parsed = parse_github_repo_ref(repo_ref)
    if not parsed:
        return (
            f"Invalid GitHub repo ref: '{repo_ref}'. "
            "Use owner/repo (e.g. openai/evals) or https://github.com/owner/repo."
        )
    owner, repo_name = parsed
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repo = GitHubIngestor.fetch_repo(owner, repo_name)
            if not repo:
                return f"Could not fetch {owner}/{repo_name}. Check the owner/repo name and your network connection."
            langs = ", ".join(repo.get("languages", [])) or "unknown"
            lines = [
                f"Project: {repo['name']}",
                f"Description: {repo.get('description') or 'No description'}",
                f"Languages: {langs}",
                f"URL: {repo.get('url', '')}",
            ]
            if repo.get("readme"):
                lines.append(f"README:\n{repo['readme']}")
            for dep_file, dep_content in repo.get("dependencies", {}).items():
                lines.append(f"{dep_file}:\n{dep_content}")
            ResumeParserAgent().parse_and_save({
                "source_file": f"github:{owner}/{repo_name}",
                "full_text": "\n".join(lines),
                "parsed_sections": {},
            })
        has_readme = "yes" if repo.get("readme") else "no"
        has_deps = "yes" if repo.get("dependencies") else "no"
        if user:
            diff = _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2],
                f"single repo: {owner}/{repo_name}",
            )
            return (
                f"Single repo ingest: {owner}/{repo_name}\n"
                f"Owner: {owner} | Languages: {langs} | README: {has_readme} | Dependency files: {has_deps}\n\n"
                + diff
            )
        return (
            f"Single repo ingested: {owner}/{repo_name}\n"
            f"Owner: {owner} | Languages: {langs} | README: {has_readme} | Dependency files: {has_deps}"
        )
    except Exception as e:
        return f"Repo ingestion failed: {e}"


def ingest_linkedin_pdf(file_path: str) -> str:
    """Parse a LinkedIn PDF export and save to DB."""
    from pathlib import Path
    if not Path(file_path).exists():
        return f"File not found: {file_path}"
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.linkedin import LinkedInIngestor
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            data = LinkedInIngestor().ingest_pdf(file_path)
            ResumeParserAgent().parse_and_save(data)
        if user:
            return _format_ingestion_diff(user.user_id, pre[0], pre[1], pre[2], Path(file_path).name)
        return f"LinkedIn PDF ingested: {Path(file_path).name}."
    except Exception as e:
        return f"LinkedIn PDF ingestion failed: {e}"
