"""
Chat Agent — TUI assistant with tool-calling for ART operations.
Uses a role-based LLM with a simple TOOL_CALL protocol to ingest data,
query the knowledge graph, and run tailoring pipelines conversationally.
"""
import re
import time
import logging
from difflib import SequenceMatcher
from typing import Dict, List
from sqlmodel import Session, select

from llm import get_llm
from database.db import engine
from tui import services
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


def get_help_text() -> str:
    """Return a concise command listing (no LLM call)."""
    return (
        "Available commands:\n"
        "  skills / my skills              — list all your skills\n"
        "  experiences / my exp            — list work experience\n"
        "  projects / my projects          — list projects\n"
        "  graph / knowledge graph         — show graph stats\n"
        "  jobs / current job              — list saved jobs\n"
        "  profile / status                — show profile summary\n"
        "  evidence for <skill>            — show evidence for a skill\n\n"
        "Ingestion commands:\n"
        "  ingest resume <path>            — parse a resume file (MD, PDF, DOCX)\n"
        "  ingest github                   — fetch your GitHub repos\n"
        "  ingest github <username>        — fetch a specific user's repos\n"
        "  ingest linkedin pdf <path>      — parse a LinkedIn PDF export\n\n"
        "Tailoring:\n"
        "  tailor <job description or file> — tailor your resume to a job\n\n"
        "Use F1–F4 in the TUI for quick access to these actions."
    )


def run_ingest_resume(file_path: str) -> str:
    """Ingest a resume file into the profile."""
    return services.ingest_resume_file(file_path.strip())


def run_ingest_github(username: str = "") -> str:
    """Fetch GitHub repos and extract skills/projects."""
    return services.ingest_github(username.strip())


def run_ingest_linkedin_pdf(file_path: str) -> str:
    """Parse a LinkedIn PDF export into the profile."""
    return services.ingest_linkedin_pdf(file_path.strip())


def run_tailor(job_input: str) -> str:
    """Run the full tailoring pipeline for a job description text or file path."""
    import json
    from pathlib import Path
    from graph.pipeline import build_pipeline
    job_input = job_input.strip()
    if not job_input:
        return "Provide a job description or file path to tailor against."
    job_file = job_input if Path(job_input).exists() else ""
    job_text = "" if job_file else job_input
    try:
        result = build_pipeline().invoke({
            "resume_path": "", "job_text": job_text, "job_file": job_file,
            "user_id": "", "job_id": "", "result_id": "", "resume_text": "",
            "ats_score": 0.0, "matched_skills": {}, "missing_skills": [],
            "tailored_content": {}, "formatted_resume": "", "status": "",
        })
    except Exception as e:
        return f"Tailoring failed: {e}"
    lines = [
        f"Tailoring complete — ATS Score: {result['ats_score']}%",
        f"Status: {result['status']}",
    ]
    matched = result.get("matched_skills", {})
    missing = result.get("missing_skills", [])
    if matched:
        lines.append(f"Matched skills ({len(matched)}): {', '.join(list(matched)[:10])}")
    if missing:
        lines.append(f"Missing skills ({len(missing)}): {', '.join(missing[:10])}")
    tc = result.get("tailored_content", {})
    if tc and "error" not in tc:
        Path("tailored_output.json").write_text(json.dumps(tc, indent=2), encoding="utf-8")
        if result.get("formatted_resume"):
            Path("tailored_resume.md").write_text(result["formatted_resume"], encoding="utf-8")
        lines.append("Saved: tailored_output.json, tailored_resume.md")
    return "\n".join(lines)


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
    "get_help_text": lambda args: get_help_text(),
    "run_ingest_resume": lambda args: run_ingest_resume(args),
    "run_ingest_github": lambda args: run_ingest_github(args),
    "run_ingest_linkedin_pdf": lambda args: run_ingest_linkedin_pdf(args),
    "run_tailor": lambda args: run_tailor(args),
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
    "knowledge graph": query_graph_stats,
    "my graph": query_graph_stats,
    "jobs": list_jobs,
    "show jobs": list_jobs,
    "job": list_jobs,
    "current job": list_jobs,
    "active job": list_jobs,
    "profile": get_profile_summary,
    "status": get_profile_summary,
    "my profile": get_profile_summary,
    "help": get_help_text,
    "what can you do": get_help_text,
    "ingest": get_help_text,
    "ingest github": lambda: run_ingest_github(""),
}


# Broader command phrase map for semantic-ish matching.
COMMAND_PHRASES = {
    "query_skills": [
        "show my skills",
        "show skills",
        "list skills",
        "skills",
        "what skills do i have",
        "display all my skills",
    ],
    "query_experiences": [
        "show my experience",
        "show experiences",
        "list experiences",
        "work experience",
        "my experience",
    ],
    "query_projects": [
        "show projects",
        "list projects",
        "my projects",
        "project list",
    ],
    "query_graph_stats": [
        "graph",
        "graph stats",
        "knowledge graph",
        "graph summary",
        "my graph",
        "show graph",
    ],
    "list_jobs": [
        "show jobs",
        "list jobs",
        "my jobs",
        "saved jobs",
        "job",
        "current job",
        "active job",
    ],
    "get_profile_summary": [
        "profile",
        "status",
        "profile summary",
        "show profile",
    ],
    "get_help_text": [
        "help",
        "what can you do",
        "ingest",
        "commands",
        "what commands",
        "show help",
        "ingest resume",
        "ingest linkedin",
    ],
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
        self.llm = get_llm(role="chat", temperature=0.2)
        self.history: List[Dict[str, str]] = []

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()

    def _semantic_command_match(self, user_message: str) -> str | None:
        """Return command/tool response for near-match command text, else None."""
        normalized = self._normalize(user_message)
        if not normalized:
            return None

        # 1) Exact shortcut hit (fast path).
        if normalized in SHORTCUTS:
            return SHORTCUTS[normalized]()

        # 1b) Argument-parsing fast-paths for ingestion and tailoring.
        m = re.match(r"^ingest resume (.+)$", normalized)
        if m:
            return run_ingest_resume(m.group(1).strip())

        m = re.match(r"^ingest github\s*(\S*)$", normalized)
        if m:
            return run_ingest_github(m.group(1).strip())

        m = re.match(r"^ingest linkedin(?:\s+pdf)?\s+(.+\.pdf)$", normalized)
        if m:
            return run_ingest_linkedin_pdf(m.group(1).strip())

        m = re.match(r"^tailor\s+(.+)$", normalized)
        if m:
            return run_tailor(m.group(1).strip())

        # 2) Dedicated skill evidence parser.
        evidence_match = re.search(
            r"(?:evidence|proof|support)\s+(?:for|of)\s+([a-z0-9\-\+\.# ]+)$",
            normalized,
        )
        if evidence_match:
            skill_name = evidence_match.group(1).strip()
            if skill_name:
                return query_skill_evidence(skill_name)

        # 3) Phrase + fuzzy similarity routing.
        best_tool = None
        best_score = 0.0

        for tool_name, phrases in COMMAND_PHRASES.items():
            for phrase in phrases:
                if phrase in normalized or normalized in phrase:
                    score = 1.0
                else:
                    score = SequenceMatcher(None, normalized, phrase).ratio()
                if score > best_score:
                    best_score = score
                    best_tool = tool_name

        # 4) Token-based fallback for common asks.
        tokens = set(normalized.split())

        def _has_token_close_to(targets: set[str], threshold: float = 0.8) -> bool:
            for tok in tokens:
                for target in targets:
                    if tok == target:
                        return True
                    if SequenceMatcher(None, tok, target).ratio() >= threshold:
                        return True
            return False

        action_words = {"show", "list", "display", "all", "my"}
        has_action = bool(tokens & action_words) or _has_token_close_to({"show", "list", "display"}, 0.75)

        if _has_token_close_to({"skill", "skills"}, 0.78) and has_action:
            return query_skills()
        if _has_token_close_to({"experience", "experiences"}, 0.8) and has_action:
            return query_experiences()
        if _has_token_close_to({"project", "projects"}, 0.8) and has_action:
            return query_projects()

        # Slightly permissive threshold to catch typos/non-exact phrasing.
        if best_tool and best_score >= 0.72:
            if best_tool == "query_skills":
                return query_skills()
            if best_tool == "query_experiences":
                return query_experiences()
            if best_tool == "query_projects":
                return query_projects()
            if best_tool == "query_graph_stats":
                return query_graph_stats()
            if best_tool == "list_jobs":
                return list_jobs()
            if best_tool == "get_profile_summary":
                return get_profile_summary()

        return None

    def chat(self, user_message: str) -> str:
        """Process a user message and return a response."""
        t0 = time.perf_counter()

        # Route command-like queries directly for speed and full-fidelity output.
        routed = self._semantic_command_match(user_message)
        if routed is not None:
            ms = (time.perf_counter() - t0) * 1000
            logger.debug("[chat] path=fast_path duration=%.1fms", ms)
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": routed})
            return routed

        # Short unrecognized messages — return clarification without LLM.
        tokens = user_message.strip().split()
        if len(tokens) < 4:
            ms = (time.perf_counter() - t0) * 1000
            logger.debug("[chat] path=fast_path duration=%.1fms", ms)
            clarification = (
                "I'm not sure what you mean. Try a command like 'skills', "
                "'projects', or 'help' to see what I can do."
            )
            self.history.append({"role": "user", "content": user_message})
            self.history.append({"role": "assistant", "content": clarification})
            return clarification

        self.history.append({"role": "user", "content": user_message})

        # Build messages for LLM
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        # Keep smaller window for lower latency while preserving context.
        messages.extend(self.history[-12:])

        try:
            response = self.llm.invoke(messages)
            text = response.content if hasattr(response, "content") else str(response)
        except Exception as e:
            logger.error(f"LLM error: {e}")
            return f"LLM error: {e}"

        ms = (time.perf_counter() - t0) * 1000
        from config import LLM_PROVIDER
        logger.debug("[chat] path=llm provider=%s role=chat duration=%.1fms", LLM_PROVIDER, ms)

        # Resolve any TOOL_CALL lines
        resolved = self._resolve_tool_calls(text)

        if resolved != text:
            self.history.append({"role": "assistant", "content": text})
            # Return tool output directly (faster and preserves full lists).
            self.history.append({"role": "assistant", "content": resolved})
            return resolved
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
