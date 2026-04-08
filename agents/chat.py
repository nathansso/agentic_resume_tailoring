"""
Chat Agent — TUI assistant with tool-calling for ART operations.
Uses Ollama LLM with a simple TOOL_CALL protocol to ingest data,
query the knowledge graph, and run tailoring pipelines conversationally.
"""
import re
import logging
from typing import Dict, List
from sqlmodel import Session, select

from llm import get_llm
from database.db import engine
from database.models import (
    User, Skill, UserSkill, Experience, Project,
    JobDescription, JobSkill, UserJobResult,
)

logger = logging.getLogger(__name__)


# ── Tool Functions ──────────────────────────────────────────

def query_skills() -> str:
    """Get all user skills with evidence sources."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "No user profile found. Ingest a resume first."
        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all()
        if not user_skills:
            return "No skills found. Ingest your resume, GitHub, or LinkedIn first."
        lines = []
        for us in user_skills:
            skill = session.get(Skill, us.skill_id)
            if skill:
                source = (us.evidence_source or "unknown").split(":")[0]
                lines.append(
                    f"- {skill.name} | source: {source} "
                    f"| proficiency: {us.proficiency or 'N/A'} "
                    f"| confidence: {us.confidence_score:.1f}"
                )
        return f"Your skills ({len(lines)}):\n" + "\n".join(lines)


def query_experiences() -> str:
    """Get all user experiences."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "No user profile found."
        exps = session.exec(
            select(Experience).where(Experience.user_id == user.user_id)
        ).all()
        if not exps:
            return "No experiences found."
        lines = []
        for e in exps:
            bullets = ""
            if e.bullets:
                bullets = "\n    " + "\n    ".join(str(b) for b in e.bullets[:3])
            lines.append(
                f"- {e.title} @ {e.company} ({e.start_date or '?'} – {e.end_date or '?'}){bullets}"
            )
        return f"Your experiences ({len(lines)}):\n" + "\n".join(lines)


def query_projects() -> str:
    """Get all user projects."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "No user profile found."
        projs = session.exec(
            select(Project).where(Project.user_id == user.user_id)
        ).all()
        if not projs:
            return "No projects found."
        lines = []
        for p in projs:
            url = p.repo_url or "no url"
            desc = (p.description or "")[:80]
            lines.append(f"- {p.name} | {url}\n    {desc}")
        return f"Your projects ({len(lines)}):\n" + "\n".join(lines)


def query_graph_stats() -> str:
    """Get knowledge graph statistics and connections."""
    try:
        from knowledge_graph.builder import SkillGraphBuilder
        builder = SkillGraphBuilder()
        G = builder.build_graph()
        nodes_by_type: Dict[str, int] = {}
        for _, data in G.nodes(data=True):
            t = data.get("type", "unknown")
            nodes_by_type[t] = nodes_by_type.get(t, 0) + 1
        edges_by_rel: Dict[str, int] = {}
        for _, _, data in G.edges(data=True):
            r = data.get("relation", "unknown")
            edges_by_rel[r] = edges_by_rel.get(r, 0) + 1
        lines = [
            f"Knowledge Graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges",
            "\nNodes by type:",
        ]
        for t, c in sorted(nodes_by_type.items()):
            lines.append(f"  {t}: {c}")
        lines.append("\nEdges by relation:")
        for r, c in sorted(edges_by_rel.items()):
            lines.append(f"  {r}: {c}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error building graph: {e}"


def query_skill_evidence(skill_name: str) -> str:
    """Get all evidence for a specific skill."""
    with Session(engine) as session:
        skills = session.exec(
            select(Skill).where(Skill.name.ilike(f"%{skill_name}%"))
        ).all()
        if not skills:
            return f"No skill matching '{skill_name}' found."
        lines = []
        for skill in skills:
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.skill_id == skill.skill_id)
            ).all()
            lines.append(f"\n{skill.name}:")
            for us in user_skills:
                lines.append(
                    f"  - Source: {us.evidence_source or 'unknown'} | "
                    f"Detail: {us.evidence_detail or 'N/A'} | "
                    f"Confidence: {us.confidence_score:.1f}"
                )
        return "\n".join(lines) if lines else f"No evidence found for '{skill_name}'."


def list_jobs() -> str:
    """List all saved job descriptions."""
    with Session(engine) as session:
        jobs = session.exec(select(JobDescription)).all()
        if not jobs:
            return "No jobs saved yet. Add a job description to get started."
        lines = []
        for j in jobs:
            results = session.exec(
                select(UserJobResult).where(UserJobResult.job_id == j.job_id)
            ).all()
            score_str = ""
            if results:
                best = max(r.ats_score for r in results)
                score_str = f" | Best ATS: {best:.1f}%"
            lines.append(f"- {j.title} @ {j.company}{score_str}")
        return f"Saved jobs ({len(lines)}):\n" + "\n".join(lines)


def get_profile_summary() -> str:
    """Get a summary of the user's profile."""
    with Session(engine) as session:
        user = session.exec(select(User).limit(1)).first()
        if not user:
            return "No user profile found. Start by ingesting your resume."
        skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user.user_id)
        ).all()
        exps = session.exec(
            select(Experience).where(Experience.user_id == user.user_id)
        ).all()
        projs = session.exec(
            select(Project).where(Project.user_id == user.user_id)
        ).all()
        results = session.exec(
            select(UserJobResult).where(UserJobResult.user_id == user.user_id)
        ).all()
        sources = set()
        for us in skills:
            if us.evidence_source:
                src = us.evidence_source.split(":")[0]
                sources.add(src)
        return (
            f"Profile: {user.name} ({user.email})\n"
            f"Skills: {len(skills)} | Experiences: {len(exps)} | "
            f"Projects: {len(projs)} | Job matches: {len(results)}\n"
            f"Data sources: {', '.join(sorted(sources)) if sources else 'none'}"
        )


# ── Tool Registry ───────────────────────────────────────────

TOOL_MAP = {
    "query_skills": lambda args: query_skills(),
    "query_experiences": lambda args: query_experiences(),
    "query_projects": lambda args: query_projects(),
    "query_graph_stats": lambda args: query_graph_stats(),
    "query_skill_evidence": lambda args: query_skill_evidence(args),
    "list_jobs": lambda args: list_jobs(),
    "get_profile_summary": lambda args: get_profile_summary(),
}

# Direct-match shortcuts (bypass LLM for instant response)
SHORTCUTS = {
    "skills": query_skills,
    "show skills": query_skills,
    "my skills": query_skills,
    "experiences": query_experiences,
    "show experiences": query_experiences,
    "my experiences": query_experiences,
    "projects": query_projects,
    "show projects": query_projects,
    "my projects": query_projects,
    "graph": query_graph_stats,
    "graph stats": query_graph_stats,
    "jobs": list_jobs,
    "show jobs": list_jobs,
    "profile": get_profile_summary,
    "status": get_profile_summary,
    "my profile": get_profile_summary,
}


# ── Chat Agent ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are ART Assistant, a helpful resume tailoring chatbot.
You help users manage their professional profile and tailor resumes to job descriptions.

When the user asks you to do something, respond with a TOOL_CALL on its own line:

TOOL_CALL: tool_name(arg)

Available tools:
- query_skills() — Show all user skills with evidence sources
- query_experiences() — Show all user experiences
- query_projects() — Show all user projects
- query_graph_stats() — Show knowledge graph statistics
- query_skill_evidence(skill_name) — Show evidence for a specific skill
- list_jobs() — List all saved job descriptions
- get_profile_summary() — Get profile overview

Rules:
- For data queries, call the appropriate tool
- Be concise and conversational
- If the user just wants to chat about their career, respond naturally
- You can call multiple tools by putting each TOOL_CALL on its own line"""


class ChatAgent:
    """
    Conversational agent for the TUI. Routes user messages to tools
    or answers questions about the user's profile/skills/jobs.
    """

    def __init__(self):
        self.llm = get_llm(temperature=0.3)
        self.history: List[Dict[str, str]] = []

    def chat(self, user_message: str) -> str:
        """Process a user message and return a response."""
        # Check for direct shortcuts first
        lower = user_message.lower().strip()
        if lower in SHORTCUTS:
            return SHORTCUTS[lower]()

        self.history.append({"role": "user", "content": user_message})

        # Build messages for LLM
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Keep last 20 messages for context window
        messages.extend(self.history[-20:])

        try:
            response = self.llm.invoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return f"LLM error: {e}"

        # Resolve any TOOL_CALL lines
        resolved = self._resolve_tool_calls(text)

        if resolved != text:
            # Tool was called — send result back for natural summary
            self.history.append({"role": "assistant", "content": text})
            self.history.append(
                {"role": "system", "content": f"Tool result:\n{resolved}"}
            )
            summary_messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            summary_messages.extend(self.history[-20:])
            summary_messages.append(
                {"role": "user", "content": "Summarize the tool result for the user. Be concise."}
            )
            try:
                response2 = self.llm.invoke(summary_messages)
                final = response2.content if hasattr(response2, "content") else str(response2)
            except Exception:
                final = resolved
            self.history.append({"role": "assistant", "content": final})
            return final
        else:
            self.history.append({"role": "assistant", "content": text})
            return text

    def _resolve_tool_calls(self, text: str) -> str:
        """Parse TOOL_CALL lines and execute them."""
        pattern = r"TOOL_CALL:\s*(\w+)\(([^)]*)\)"
        matches = re.findall(pattern, text)
        if not matches:
            return text

        results = []
        for tool_name, args in matches:
            args = args.strip().strip("'\"")
            if tool_name in TOOL_MAP:
                try:
                    result = TOOL_MAP[tool_name](args)
                    results.append(result)
                except Exception as e:
                    results.append(f"[{tool_name}] Error: {e}")
            else:
                results.append(f"Unknown tool: {tool_name}")

        return "\n\n".join(results)
