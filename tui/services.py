"""
DB query functions and ingestion service functions for the TUI.
Query functions return plain data (lists/dicts) for widget rendering.
Ingestion functions return plain-English result strings and never raise.
"""
from typing import Optional
from uuid import UUID

from sqlmodel import Session, select

from database.db import engine
from database.models import (
    Experience, JobDescription, Project,
    Skill, User, UserJobResult, UserSkill,
)


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
        detail: dict = {"title": job.title, "company": job.company}
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
        init_db()
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
        return f"Resume ingested: {path.name}. Your skills and experiences have been updated."
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
        from ingestion.github import GitHubIngestor
        from agents.parser import ResumeParserAgent
        init_db()
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
        return f"GitHub ingested: {len(repos)} repos parsed for {target}."
    except Exception as e:
        return f"GitHub ingestion failed: {e}"


def ingest_linkedin_pdf(file_path: str) -> str:
    """Parse a LinkedIn PDF export and save to DB."""
    from pathlib import Path
    if not Path(file_path).exists():
        return f"File not found: {file_path}"
    try:
        from database.db import init_db
        from ingestion.linkedin import LinkedInIngestor
        from agents.parser import ResumeParserAgent
        init_db()
        data = LinkedInIngestor().ingest_pdf(file_path)
        ResumeParserAgent().parse_and_save(data)
        return f"LinkedIn PDF ingested: {Path(file_path).name}."
    except Exception as e:
        return f"LinkedIn PDF ingestion failed: {e}"
