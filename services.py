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

from sqlmodel import Session, delete, select

from database.db import engine
from database.models import (
    ChatMessage, Education, Experience, JobDescription, JobSkill, Project,
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
        G = SkillGraphBuilder(user_id).build_graph()
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
    default_email = "user@example.com"
    return {
        "user_id": user.user_id,
        "name": user.name or "",
        "github_username": user.github_username or "",
        "linkedin_url": user.linkedin_url or "",
        "email": "" if (not user.email or user.email == default_email) else user.email,
        "phone": user.phone or "",
        "location": user.location or "",
        "skills": skill_count,
        "experiences": exp_count,
        "projects": proj_count,
        "sources": sorted(sources),
    }


def update_profile(
    user_id: UUID,
    name: str,
    github_username: str,
    linkedin_url: str,
    phone: str = "",
    email: str = "",
    location: str = "",
    portfolio_url: str = "",
) -> str:
    """Update the active profile's personal info fields."""
    from datetime import datetime
    try:
        with Session(engine) as session:
            user = session.get(User, user_id)
            if not user:
                return "Profile not found."
            user.name = name.strip() or user.name
            user.github_username = github_username.strip() or None
            user.linkedin_url = linkedin_url.strip() or None
            user.phone = phone.strip() or None
            user.location = location.strip() or None
            user.portfolio_url = portfolio_url.strip() or None
            if email.strip() and email.strip() != "user@example.com":
                user.email = email.strip()
            user.updated_at = datetime.utcnow()
            session.add(user)
            session.commit()
    except Exception as e:
        logger.error("update_profile failed: %s", e)
        return f"Failed to save profile: {e}"
    return "Profile updated."


def ingest_github_for_profile(user_id: Optional[UUID], username: str) -> str:
    """Ingest GitHub repos for the active profile."""
    return ingest_github(username)


def _format_skill_source(evidence_source: str) -> str:
    """Convert a raw evidence_source value to a human-readable display label."""
    src = (evidence_source or "").strip()
    if src.startswith("github:"):
        ref = src[len("github:"):]   # "username" or "owner/repo"
        return f"GitHub: {ref.split('/')[-1]}"
    if src.startswith("manual"):
        return "manual"
    return "resume"  # any file path, "resume" literal, or unknown → resume


def get_skills(user_id: Optional[UUID]) -> list[dict]:
    if user_id is None:
        return []
    with Session(engine) as session:
        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()

        # Deduplicate by normalized skill name; merge sources and keep highest confidence.
        merged: dict[str, dict] = {}
        for us in user_skills:
            skill = session.get(Skill, us.skill_id)
            if not skill:
                continue
            key = skill.name.lower().strip()
            source = _format_skill_source(us.evidence_source or "")
            confidence = us.confidence_score or 0.0

            if key not in merged:
                merged[key] = {
                    "name": skill.name,
                    "category": skill.category or "Uncategorized",
                    "proficiency": us.proficiency,
                    "confidence": confidence,
                    "sources": {source} if source else set(),
                    "is_core": bool(us.is_core),
                }
            else:
                if confidence > merged[key]["confidence"]:
                    merged[key]["confidence"] = confidence
                    if us.proficiency is not None:
                        merged[key]["proficiency"] = us.proficiency
                if source:
                    merged[key]["sources"].add(source)
                merged[key]["is_core"] = merged[key]["is_core"] or bool(us.is_core)

        rows = []
        for entry in merged.values():
            prof = entry["proficiency"]
            rows.append({
                "name": entry["name"],
                "category": entry["category"],
                "source": ", ".join(sorted(entry["sources"])) if entry["sources"] else "",
                "proficiency": str(prof) if prof is not None else "N/A",
                "confidence": f"{entry['confidence']:.1f}",
                "is_core": entry["is_core"],
            })
        return rows


def set_skill_core(user_id: UUID, skill_name: str, is_core: bool) -> str:
    """Pin/unpin a skill as 'core' on the user's profile. Returns plain-English. Never raises.

    A pinned skill is always rendered in the tailored skills section, bypassing
    the JD-relevance cap (issue #54). Applies to every UserSkill row for the
    skill (matched case-insensitively by name).
    """
    try:
        skill_name = (skill_name or "").strip()
        if not skill_name:
            return "Please provide a skill name."
        with Session(engine) as session:
            skill = session.exec(select(Skill).where(Skill.name == skill_name)).first()
            if not skill:
                all_skills = session.exec(select(Skill)).all()
                skill = next(
                    (s for s in all_skills if s.name.lower() == skill_name.lower()), None
                )
            if not skill:
                return f"'{skill_name}' is not in your profile."
            display_name = skill.name
            links = session.exec(
                select(UserSkill).where(
                    UserSkill.user_id == user_id,
                    UserSkill.skill_id == skill.skill_id,
                )
            ).all()
            if not links:
                return f"'{skill_name}' is not in your profile."
            for link in links:
                link.is_core = is_core
                session.add(link)
            session.commit()
        if is_core:
            return f"Pinned '{display_name}' as a core skill."
        return f"Unpinned '{display_name}'."
    except Exception as e:
        logger.error("set_skill_core failed: %s", e)
        return f"Failed to update skill: {e}"


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


def get_education(user_id: Optional[UUID]) -> list[dict]:
    """This user's education rows for the Data Explorer (issue #73 follow-up)."""
    if user_id is None:
        return []
    with Session(engine) as session:
        entries = session.exec(
            select(Education)
            .where(Education.user_id == user_id)
            .order_by(Education.created_at)
        ).all()
        return [
            {
                "institution": e.institution,
                "degree": e.degree or "—",
                "location": e.location or "",
                "start": e.start_date or "",
                "end": e.end_date or "",
                "gpa": e.gpa or "",
            }
            for e in entries
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
    try:
        parsed_uuid = UUID(job_uuid)
    except (ValueError, AttributeError):
        return None
    with Session(engine) as session:
        job = session.get(JobDescription, parsed_uuid)
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


# ── GitHub OAuth device flow (TUI) ───────────────────────────

def start_github_device_flow() -> dict:
    """Initiate GitHub device flow. Returns { user_code, verification_uri, device_code, interval } or raises."""
    import requests as _req
    from config import GITHUB_CLIENT_ID
    if not GITHUB_CLIENT_ID:
        raise RuntimeError("GITHUB_CLIENT_ID is not set — cannot start device flow")
    resp = _req.post(
        "https://github.com/login/device/code",
        headers={"Accept": "application/json"},
        json={"client_id": GITHUB_CLIENT_ID, "scope": "repo read:user"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "device_code": data["device_code"],
        "user_code": data["user_code"],
        "verification_uri": data["verification_uri"],
        "interval": data.get("interval", 5),
        "expires_in": data.get("expires_in", 900),
    }


def poll_github_device_flow(device_code: str, interval: int = 5) -> str | None:
    """Poll once for a device-flow access token. Returns token string, None if still pending, or raises on error."""
    import time
    import requests as _req
    from config import GITHUB_CLIENT_ID
    time.sleep(interval)
    resp = _req.post(
        "https://github.com/login/oauth/access_token",
        headers={"Accept": "application/json"},
        json={
            "client_id": GITHUB_CLIENT_ID,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("access_token"):
        return data["access_token"]
    error = data.get("error", "")
    if error in ("authorization_pending", "slow_down"):
        return None
    raise RuntimeError(f"Device flow error: {error} — {data.get('error_description', '')}")


# ── LLM provider + API key (stored in .env, never in SQLite) ─

def get_llm_config() -> tuple[str, bool]:
    """Return (provider, has_key) read from .env and os.environ.

    has_key is True if the API key for the current provider is set.
    """
    import os
    from dotenv import dotenv_values
    vals = dotenv_values(_ENV_PATH)
    provider = vals.get("LLM_PROVIDER") or os.environ.get("LLM_PROVIDER") or "anthropic"
    if provider == "anthropic":
        has_key = bool(vals.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
    else:
        has_key = bool(vals.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    return provider, has_key


def save_llm_config(provider: str, api_key: str) -> None:
    """Persist LLM provider + API key to .env and os.environ for immediate effect.

    Setting os.environ means get_llm() picks up the new key on the very next call
    without requiring a restart. Never logs the key value.
    """
    import os
    from dotenv import set_key
    _ENV_PATH.touch()
    set_key(str(_ENV_PATH), "LLM_PROVIDER", provider)
    key_name = "ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY"
    set_key(str(_ENV_PATH), key_name, api_key)  # key value intentionally not logged
    os.environ["LLM_PROVIDER"] = provider
    os.environ[key_name] = api_key


def save_llm_provider_only(provider: str) -> None:
    """Persist only the LLM_PROVIDER to .env and os.environ, leaving existing keys untouched."""
    import os
    from dotenv import set_key
    _ENV_PATH.touch()
    set_key(str(_ENV_PATH), "LLM_PROVIDER", provider)
    os.environ["LLM_PROVIDER"] = provider


# ── Resume path (stored on User row) ────────────────────────

def get_resume_path(user_id: UUID) -> Optional[str]:
    """Return resume_path for the given user, or None."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        return user.resume_path if user else None


def get_resume_style(user_id: UUID) -> Optional[dict]:
    """Return the parsed style profile for the user's ingested resume, or None."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        return user.resume_style if user else None


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
            # Refresh the cached embedding for the new skill (issue #54).
            try:
                from agents.skill_embeddings import ensure_skill_embeddings
                ensure_skill_embeddings(session, [skill.skill_id])
            except Exception as exc:
                logger.warning("Skill embedding refresh skipped: %s", exc)
        return f"Added '{skill_name}' to your profile."
    except Exception as e:
        logger.error("add_skill_to_profile failed: %s", e)
        return f"Failed to add skill: {e}"


def create_artifact_from_chat(
    user_id: UUID,
    artifact_type: str,
    data: dict,
    source_context: str = "chat",
) -> str:
    """Create a skill, project, or experience row from structured chat-extracted data.

    artifact_type: 'skill' | 'project' | 'experience'
    data: type-specific fields (name/category for skill; name/description/repo_url for project;
          title/company/description for experience).  Must include a non-empty 'evidence' key:
          a verbatim quote or paraphrase from the conversation that supports saving this artifact.
    source_context: free-form back-reference (e.g. 'chat:job_<id>') stored on the artifact.
    Returns a plain-English result string. Never raises.
    """
    try:
        # Evidence is required — callers must supply a quote/paraphrase from the conversation
        # that supports this artifact.  This prevents the LLM from hallucinating artifacts that
        # were never actually discussed.
        evidence = (data.get("evidence") or "").strip()
        if not evidence:
            return (
                "Evidence is required to save this artifact from chat. "
                "Describe what was said in the conversation that supports this."
            )

        artifact_type = artifact_type.lower().strip()
        if artifact_type == "skill":
            name = (data.get("name") or "").strip()
            if not name:
                return "Skill name is required."
            category = (data.get("category") or "").strip() or None
            with Session(engine) as session:
                skill = session.exec(select(Skill).where(Skill.name == name)).first()
                if not skill:
                    all_skills = session.exec(select(Skill)).all()
                    skill = next((s for s in all_skills if s.name.lower() == name.lower()), None)
                if not skill:
                    skill = Skill(name=name, category=category)
                    session.add(skill)
                    session.flush()
                existing = session.exec(
                    select(UserSkill).where(
                        UserSkill.user_id == user_id,
                        UserSkill.skill_id == skill.skill_id,
                    )
                ).first()
                if existing:
                    return f"'{name}' is already in your profile."
                session.add(UserSkill(
                    user_id=user_id,
                    skill_id=skill.skill_id,
                    proficiency=3,
                    evidence_source="chat",
                    confidence_score=0.7,
                    evidence_detail=evidence,  # verbatim quote from the conversation
                ))
                session.commit()
            return f"Added skill '{name}' to your profile (source: chat)."
        elif artifact_type == "project":
            name = (data.get("name") or "").strip()
            if not name:
                return "Project name is required."
            # Fall back to the evidence quote when no separate description is provided.
            description = (data.get("description") or evidence).strip() or None
            repo_url = (data.get("repo_url") or "").strip() or None
            with Session(engine) as session:
                existing = session.exec(
                    select(Project).where(
                        Project.user_id == user_id,
                        Project.name == name,
                    )
                ).first()
                if existing:
                    return f"Project '{name}' is already in your profile."
                session.add(Project(
                    user_id=user_id,
                    name=name,
                    description=description,
                    repo_url=repo_url,
                ))
                session.commit()
            return f"Added project '{name}' to your profile."
        elif artifact_type == "experience":
            title = (data.get("title") or "").strip()
            company = (data.get("company") or "").strip()
            if not title or not company:
                return "Experience title and company are required."
            # Fall back to the evidence quote when no separate description is provided.
            description = (data.get("description") or evidence).strip() or None
            with Session(engine) as session:
                existing = session.exec(
                    select(Experience).where(
                        Experience.user_id == user_id,
                        Experience.title == title,
                        Experience.company == company,
                    )
                ).first()
                if existing:
                    return f"Experience '{title} @ {company}' is already in your profile."
                session.add(Experience(
                    user_id=user_id,
                    title=title,
                    company=company,
                    description=description,
                ))
                session.commit()
            return f"Added experience '{title} @ {company}' to your profile."
        else:
            return f"Unknown artifact type: '{artifact_type}'. Use 'skill', 'project', or 'experience'."
    except Exception as e:
        logger.error("create_artifact_from_chat failed: %s", e)
        return f"Failed to create artifact: {e}"


def delete_resume(user_id: UUID) -> None:
    """Clear resume_path on the User row. Does not delete the file or any ingested data."""
    with Session(engine) as session:
        user = session.get(User, user_id)
        if user:
            user.resume_path = None
            session.add(user)
            session.commit()


def delete_job(job_uuid: str) -> str:
    """Delete a JobDescription and all dependent rows (UserJobResult, JobSkill, ChatMessage).
    Returns plain-English result. Never raises."""
    try:
        from uuid import UUID as _UUID
        jid = _UUID(job_uuid)
        with Session(engine) as session:
            session.exec(delete(UserJobResult).where(UserJobResult.job_id == jid))
            session.exec(delete(JobSkill).where(JobSkill.job_id == jid))
            session.exec(delete(ChatMessage).where(ChatMessage.job_id == jid))
            session.commit()
            job = session.get(JobDescription, jid)
            if job:
                session.delete(job)
                session.commit()
        return "Job deleted."
    except Exception as e:
        return f"Failed to delete job: {e}"


# ── Chat history (persisted per job) ────────────────────────

_MAX_CHAT_MESSAGES_PER_JOB = 100


def _acting_user_id() -> Optional[UUID]:
    """The current acting user's id (request binding or CLI pointer), or None."""
    from database.user_utils import get_active_profile
    user = get_active_profile()
    return user.user_id if user else None


def save_chat_message(job_id: Optional[str], role: str, content: str) -> None:
    """Persist one message to the ChatMessage table. Never raises.

    Stamped with the acting user so landing-context messages (job_id=None)
    stay isolated between users (issue #73).
    """
    try:
        jid = UUID(job_id) if job_id else None
        uid = _acting_user_id()
        with Session(engine) as session:
            session.add(ChatMessage(job_id=jid, user_id=uid, role=role, content=content))
            session.commit()
        _prune_chat_messages(jid, user_id=uid)
    except Exception as e:
        logger.warning("save_chat_message failed: %s", e)


def _prune_chat_messages(
    jid: Optional[UUID],
    keep: int = _MAX_CHAT_MESSAGES_PER_JOB,
    user_id: Optional[UUID] = None,
) -> None:
    """Delete oldest messages beyond `keep` for the given job_id. Never raises.

    Landing context (jid=None) prunes only the given user's messages.
    """
    try:
        with Session(engine) as session:
            query = (
                select(ChatMessage.message_id)
                .where(ChatMessage.job_id == jid)
                .order_by(ChatMessage.created_at.desc())
            )
            if jid is None:
                query = query.where(ChatMessage.user_id == user_id)
            ids = session.exec(query).all()
            if len(ids) > keep:
                to_delete = list(ids[keep:])
                session.exec(delete(ChatMessage).where(ChatMessage.message_id.in_(to_delete)))
                session.commit()
    except Exception:
        pass


def load_chat_history(
    job_id: Optional[str], limit: int = 20, user_id: Optional[UUID] = None
) -> list[dict]:
    """Return the last `limit` messages for this job as {role, content} dicts, oldest-first.
    Returns [] if none found or on error.

    Landing context (job_id=None) is scoped to `user_id` — or the acting user
    when not passed — so users never see each other's landing chat (issue #73).
    Job contexts are scoped by job_id; callers verify job ownership.
    """
    try:
        jid = UUID(job_id) if job_id else None
        with Session(engine) as session:
            query = (
                select(ChatMessage)
                .where(ChatMessage.job_id == jid)
                .order_by(ChatMessage.created_at.desc())
                .limit(limit)
            )
            if jid is None:
                uid = user_id if user_id is not None else _acting_user_id()
                query = query.where(ChatMessage.user_id == uid)
            msgs = session.exec(query).all()
        return [{"role": m.role, "content": m.content, "created_at": m.created_at.isoformat()} for m in reversed(msgs)]
    except Exception as e:
        logger.warning("load_chat_history failed: %s", e)
        return []


def save_chat_summary(job_id: Optional[str], summary: str) -> None:
    """Persist a conversation summary to JobDescription.chat_summary. Never raises."""
    if not job_id:
        return
    try:
        jid = UUID(job_id)
        with Session(engine) as session:
            job = session.get(JobDescription, jid)
            if job:
                job.chat_summary = summary
                session.add(job)
                session.commit()
    except Exception as e:
        logger.warning("save_chat_summary failed: %s", e)


def load_chat_summary(job_id: Optional[str]) -> Optional[str]:
    """Return the persisted chat summary for this job, or None if absent or on error."""
    if not job_id:
        return None
    try:
        jid = UUID(job_id)
        with Session(engine) as session:
            job = session.get(JobDescription, jid)
            return job.chat_summary if job else None
    except Exception as e:
        logger.warning("load_chat_summary failed: %s", e)
        return None


# ── Ingestion service functions ─────────────────────────────
# Each returns a plain-English result string and never raises.

def _backfill_contact_fields(user: User, style: dict) -> None:
    """Fill in header contact fields from a freshly-ingested resume (issue #75).

    Only sets a field when it's currently empty — never overwrites a value
    the user already has (manually entered, or from GitHub/LinkedIn connect).
    """
    values = (style or {}).get("header", {}).get("contact_values", {})
    if not values:
        return
    if not user.linkedin_url and values.get("linkedin"):
        user.linkedin_url = values["linkedin"]
    if not user.github_username and values.get("github"):
        user.github_username = values["github"]
    if not user.portfolio_url and values.get("portfolio"):
        user.portfolio_url = values["portfolio"]
    if not user.phone and values.get("phone"):
        user.phone = values["phone"]
    if not user.location and values.get("location"):
        user.location = values["location"]


def ingest_resume_file(file_path: str, display_name: str | None = None) -> str:
    """Parse a resume file (MD, PDF, DOCX) and save to DB.

    display_name: label to show in the result summary — pass the original upload
    filename when file_path is a server-side temp file.
    """
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
                    "source_file": "resume",  # normalized: prevents multi-resume duplicate rows
                    "full_text": path.read_text(encoding="utf-8"),
                    "parsed_sections": {},
                }
            else:
                from ingestion.resume import ResumeIngestor
                ingestion_data = ResumeIngestor().ingest(file_path)
                ingestion_data["source_file"] = "resume"  # normalize regardless of ingestor value
            from agents.parser import ResumeParserAgent
            ResumeParserAgent().parse_and_save(ingestion_data)
        if user:
            from ingestion.resume import extract_style_profile
            full_text = ingestion_data.get("full_text", "")
            style = ingestion_data.get("resume_style") or extract_style_profile(full_text)
            with Session(engine) as session:
                db_user = session.get(User, user.user_id)
                if db_user:
                    db_user.resume_markdown = full_text
                    db_user.resume_style = style
                    _backfill_contact_fields(db_user, style)
                    session.add(db_user)
                    session.commit()
            return _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2], display_name or path.name
            )
        return f"Resume ingested: {display_name or path.name}."
    except Exception as e:
        return f"Ingestion failed: {e}"


def _build_repo_metrics(repos: list) -> dict:
    """Map repo name -> GitHub signals for project complexity scoring (issue #46)."""
    return {
        repo["name"]: {
            "stars": repo.get("stars", 0),
            "languages": repo.get("languages", []),
            "readme_length": len(repo.get("readme") or ""),
        }
        for repo in repos
    }


_GITHUB_RATE_LIMIT_MESSAGE = (
    "GitHub API rate limit reached. This server ingests without a dedicated "
    "GitHub token, so unauthenticated requests share a 60/hour limit across all "
    "users — try again in a few minutes, or connect your GitHub account "
    "(Profile menu) for a much higher limit."
)


def ingest_github(username: str = "", token: str | None = None) -> str:
    """Fetch GitHub repos for a user and save skills/projects to DB.

    token: OAuth or PAT to use. Falls back to GITHUB_TOKEN env var if not provided.
    """
    from config import GITHUB_USERNAME, GITHUB_TOKEN
    target = username.strip() or GITHUB_USERNAME
    if not target:
        return "No GitHub username provided and GITHUB_USERNAME is not set in .env."
    auth_token = token or GITHUB_TOKEN
    try:
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor, GitHubRateLimitError
        from agents.parser import ResumeParserAgent
        init_db()
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repos = GitHubIngestor(username=target, token=auth_token).ingest()
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
                "repo_metrics": _build_repo_metrics(repos),
            })
        if user:
            return _format_ingestion_diff(user.user_id, pre[0], pre[1], pre[2], f"github:{target} ({len(repos)} repos)")
        return f"GitHub ingested: {len(repos)} repos parsed for {target}."
    except GitHubRateLimitError:
        return _GITHUB_RATE_LIMIT_MESSAGE
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


def ingest_github_repo(repo_ref: str, token: str | None = None) -> str:
    """Fetch a single GitHub repo and save to DB. Returns a plain-English summary string and never raises.

    token: OAuth or PAT to use. Falls back to GITHUB_TOKEN env var if not provided.
    """
    parsed = parse_github_repo_ref(repo_ref)
    if not parsed:
        return (
            f"Invalid GitHub repo ref: '{repo_ref}'. "
            "Use owner/repo (e.g. openai/evals) or https://github.com/owner/repo."
        )
    owner, repo_name = parsed
    try:
        from config import GITHUB_TOKEN
        from database.db import init_db
        from database.user_utils import get_active_profile
        from ingestion.github import GitHubIngestor, GitHubRateLimitError
        from agents.parser import ResumeParserAgent
        init_db()
        auth_token = token or GITHUB_TOKEN
        user = get_active_profile()
        pre = _snapshot_user_data(user.user_id) if user else (set(), set(), set())
        with _suppress_output():
            repo = GitHubIngestor.fetch_repo(owner, repo_name, token=auth_token)
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
                "repo_metrics": _build_repo_metrics([repo]),
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
    except GitHubRateLimitError:
        return _GITHUB_RATE_LIMIT_MESSAGE
    except Exception as e:
        return f"Repo ingestion failed: {e}"


def _set_linkedin_status(
    user_id: Optional[UUID],
    status: Optional[str],
    error: Optional[str] = None,
    ingested_url: Optional[str] = None,
) -> None:
    """Record the LinkedIn ingestion lifecycle on the user row."""
    if user_id is None:
        return
    from datetime import datetime
    try:
        with Session(engine) as session:
            db_user = session.get(User, user_id)
            if not db_user:
                return
            db_user.linkedin_ingest_status = status
            db_user.linkedin_ingest_error = error
            if status == "done":
                db_user.linkedin_ingested_at = datetime.utcnow()
                if ingested_url:
                    db_user.linkedin_ingested_url = ingested_url
            session.add(db_user)
            session.commit()
    except Exception as e:
        logger.error("Failed to update LinkedIn ingest status: %s", e)


def ingest_linkedin(profile_url: str, user_id: Optional[UUID] = None) -> str:
    """
    Scrape a LinkedIn profile via Bright Data and save it to the DB.

    Records the ingestion lifecycle (importing/done/failed) on the user row so
    the UI can poll for progress. Never raises — returns a plain-English result.
    """
    from database.db import init_db
    from database.user_utils import get_active_profile
    from ingestion.linkedin import LinkedInIngestor, LinkedInIngestionError
    from agents.parser import ResumeParserAgent

    init_db()
    if user_id is None:
        user = get_active_profile()
        user_id = user.user_id if user else None
    else:
        # Point the parser at this user (parse_and_save uses the active profile).
        # Context-scoped, not the shared pointer file — background LinkedIn
        # ingests for different users must not race each other (issue #73).
        from database.user_utils import set_request_user
        set_request_user(user_id)

    _set_linkedin_status(user_id, "importing")
    pre = _snapshot_user_data(user_id) if user_id else (set(), set(), set())
    try:
        with _suppress_output():
            data = LinkedInIngestor().ingest_brightdata(profile_url)
            ResumeParserAgent().parse_and_save(data)
    except LinkedInIngestionError as e:
        _set_linkedin_status(user_id, "failed", error=str(e))
        return f"LinkedIn import failed: {e}"
    except Exception as e:
        logger.error("LinkedIn ingestion failed: %s", e)
        _set_linkedin_status(user_id, "failed", error=str(e))
        return f"LinkedIn import failed: {e}"

    ingested_url = data.get("source_file", "").replace("linkedin:", "")
    _set_linkedin_status(user_id, "done", ingested_url=ingested_url)
    if user_id:
        return _format_ingestion_diff(user_id, pre[0], pre[1], pre[2], "LinkedIn")
    return "LinkedIn profile ingested."


def ingest_linkedin_pdf(file_path: str, display_name: str | None = None) -> str:
    """Parse a LinkedIn PDF export and save to DB.

    display_name: label to show in the result summary — pass the original upload
    filename when file_path is a server-side temp file.
    """
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
            return _format_ingestion_diff(
                user.user_id, pre[0], pre[1], pre[2], display_name or Path(file_path).name
            )
        return f"LinkedIn PDF ingested: {display_name or Path(file_path).name}."
    except Exception as e:
        return f"LinkedIn PDF ingestion failed: {e}"
