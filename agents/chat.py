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
    from database.user_utils import get_active_profile
    user = get_active_profile()
    with Session(engine) as session:
        if not user:
            return "No active profile. Complete onboarding first."
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
    from database.user_utils import get_active_profile
    user = get_active_profile()
    with Session(engine) as session:
        if not user:
            return "No active profile. Complete onboarding first."
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
    from database.user_utils import get_active_profile
    user = get_active_profile()
    with Session(engine) as session:
        if not user:
            return "No active profile. Complete onboarding first."
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
        "  ingest github <username>        — fetch a GitHub user's repos\n"
        "  ingest linkedin pdf <path>      — parse a LinkedIn PDF export\n\n"
        "Tailoring:\n"
        "  tailor <job description or file> — tailor your resume to a job\n\n"
        "TUI shortcuts (type in chat):\n"
        "  /ingest  /data  /tailor  /viz  /profile  /copy\n\n"
        "Note: use ctrl+q to quit. ctrl+c is disabled to allow copy/paste."
    )


def run_ingest_resume(file_path: str) -> str:
    """Ingest a resume file into the profile."""
    return services.ingest_resume_file(file_path.strip())


def run_ingest_github(username: str = "") -> str:
    """Fetch GitHub repos and extract skills/projects."""
    username = username.strip()
    if not username:
        from database.user_utils import get_active_profile
        profile = get_active_profile()
        suggestion = f"`ingest github {profile.github_username}`" if (profile and profile.github_username) else "`ingest github <your-username>`"
        return (
            f"Please provide your GitHub username explicitly.\n\n"
            f"Type: {suggestion}\n\n"
            "To include private repos, ensure GITHUB_TOKEN is set in your .env file.\n"
            "Without a token, only public repos will be fetched."
        )
    return services.ingest_github(username)


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
    from database.user_utils import get_active_profile
    user = get_active_profile()
    with Session(engine) as session:
        if not user:
            return "No active profile. Complete onboarding first."
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


def query_skills_vs_jobs() -> str:
    """Show how the user's skills match each saved job description."""
    from database.user_utils import get_active_profile
    user = get_active_profile()
    if not user:
        return "No active profile. Complete onboarding first."
    with Session(engine) as session:
        jobs = session.exec(select(JobDescription)).all()
        if not jobs:
            skill_count = len(session.exec(
                select(UserSkill).where(UserSkill.user_id == user.user_id)
            ).all())
            return (
                f"You have {skill_count} skills on your profile, but no jobs have been added yet.\n\n"
                "Run `tailor <job description>` to score your skills against a job\n"
                "and see exactly what you match and what's missing."
            )
        lines = []
        for job in jobs:
            results = session.exec(
                select(UserJobResult).where(UserJobResult.job_id == job.job_id)
            ).all()
            if not results:
                lines.append(f"\n{job.title} @ {job.company}\n  No match results yet — run `tailor` to score.")
                continue
            latest = max(results, key=lambda r: r.created_at)
            matched = list(latest.matched_skills.keys()) if latest.matched_skills else []
            missing = latest.missing_skills or []
            matched_str = ", ".join(matched[:8]) + (f" (+{len(matched)-8} more)" if len(matched) > 8 else "")
            missing_str = ", ".join(missing[:5]) + (f" (+{len(missing)-5} more)" if len(missing) > 5 else "")
            lines.append(
                f"\n{job.title} @ {job.company} — {latest.ats_score:.0f}% match\n"
                + (f"  Matched: {matched_str}\n" if matched else "  Matched: (none)\n")
                + (f"  Missing: {missing_str}\n" if missing else "  Missing: (none)\n")
            )
        return "Your skills vs saved jobs:" + "".join(lines)


# ── Tool Registry ───────────────────────────────────────────

TOOL_MAP = {
    "query_skills": lambda args: query_skills(),
    "query_skills_vs_jobs": lambda args: query_skills_vs_jobs(),
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

# Direct-match shortcuts — only help and ingestion entry-points bypass the LLM.
# Data queries (skills, experiences, projects, jobs, graph) are handled via LLM TOOL_CALL.
SHORTCUTS = {
    "help": get_help_text,
    "what can you do": get_help_text,
    "commands": get_help_text,
    "show help": get_help_text,
}


# ── Chat Agent ──────────────────────────────────────────────

SYSTEM_PROMPT = """You are ART Assistant, a helpful resume tailoring chatbot.
You help users manage their professional profile and tailor resumes to job descriptions.

When the user asks you to do something, respond with a TOOL_CALL on its own line:

TOOL_CALL: tool_name(arg)

Available tools:
- query_skills_vs_jobs() — Show user skills scored against each saved job (PREFERRED for skill queries)
- query_skills() — Raw skill list with evidence sources (use when the user explicitly wants all skills)
- query_experiences() — Show all user experiences
- query_projects() — Show all user projects
- query_graph_stats() — Show knowledge graph statistics
- query_skill_evidence(skill_name) — Show evidence for a specific skill
- list_jobs() — List all saved job descriptions
- get_profile_summary() — Get profile overview
- run_ingest_resume(file_path) — Ingest a resume file (PDF, DOCX, MD)
- run_ingest_github(username) — Fetch GitHub repos for a username and extract skills/projects
- run_ingest_linkedin_pdf(file_path) — Parse a LinkedIn PDF export
- run_tailor(job_description_or_path) — Tailor the resume to a job description

Rules:
- For skill queries, prefer query_skills_vs_jobs() — it shows match context, not just a flat list
- For data queries, call the appropriate tool
- For ingestion requests, ALWAYS call the appropriate tool — never describe what to do instead
- run_ingest_github requires a username argument; if the user hasn't provided one, ask for it
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
        self._pending_options: dict[str, callable] = {}

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", text.lower())).strip()

    def _last_bot_asked_question(self) -> bool:
        """Return True if the most recent assistant message ended with a question mark."""
        for msg in reversed(self.history):
            if msg["role"] == "assistant":
                return msg["content"].rstrip().endswith("?")
        return False

    def _ingest_github_with_options(self) -> str:
        """Return a numbered-choice message for GitHub ingestion."""
        from database.user_utils import get_active_profile
        profile = get_active_profile()
        if profile and profile.github_username:
            username = profile.github_username
            self._pending_options = {
                "1": lambda u=username: services.ingest_github(u),
                "2": lambda: (
                    "Type `ingest github <username>` with your preferred username.\n"
                    "Example: `ingest github nathansso`"
                ),
            }
            return (
                f"Found GitHub username in your profile: {username}\n\n"
                f"  1. Ingest repos for {username}\n"
                "  2. Use a different username\n\n"
                "Reply with 1 or 2, or type `ingest github <username>` directly."
            )
        else:
            return (
                "To ingest your GitHub repos, type:\n\n"
                "  `ingest github <username>`\n\n"
                "Example: `ingest github nathansso`\n\n"
                "Tip: Save your username in Profile to skip this prompt next time."
            )

    def _semantic_command_match(self, user_message: str) -> str | None:
        """Return command/tool response for near-match command text, else None."""
        normalized = self._normalize(user_message)
        if not normalized:
            return None

        tokens = set(normalized.split())

        def _has_token_close_to(targets: set[str], threshold: float = 0.8) -> bool:
            for tok in tokens:
                for target in targets:
                    if tok == target:
                        return True
                    if SequenceMatcher(None, tok, target).ratio() >= threshold:
                        return True
            return False

        # 0) Resolve pending numbered options (user replied "1", "2", etc.).
        stripped = user_message.strip()
        if self._pending_options and stripped in self._pending_options:
            fn = self._pending_options[stripped]
            self._pending_options.clear()
            return fn()

        # 1) Exact shortcut hit (fast path). Ingestion keywords use instance methods.
        if normalized == "ingest github":
            return self._ingest_github_with_options()
        if normalized == "ingest":
            self._pending_options = {
                "1": self._ingest_github_with_options,
                "2": lambda: (
                    "To ingest a resume, type:\n\n"
                    "  `ingest resume <path>`\n\n"
                    "Example: `ingest resume /path/to/resume.pdf`\n"
                    "Supported formats: PDF, DOCX, MD"
                ),
                "3": lambda: (
                    "To ingest a LinkedIn PDF, type:\n\n"
                    "  `ingest linkedin pdf <path>`\n\n"
                    "Example: `ingest linkedin pdf /path/to/linkedin.pdf`"
                ),
            }
            return (
                "What would you like to ingest?\n\n"
                "  1. GitHub repos\n"
                "  2. Resume (PDF, DOCX, MD)\n"
                "  3. LinkedIn PDF export\n\n"
                "Reply with 1, 2, or 3."
            )
        if normalized in SHORTCUTS:
            return SHORTCUTS[normalized]()

        # 1b) Argument-parsing fast-paths — use raw message to preserve file paths.
        raw = user_message.strip()
        m = re.match(r"(?i)^ingest resume\s+(.+)$", raw)
        if m:
            return run_ingest_resume(m.group(1).strip())

        m = re.match(r"(?i)^ingest github\s+(\S+)$", raw)
        if m:
            return run_ingest_github(m.group(1).strip())

        m = re.match(r"(?i)^ingest linkedin(?:\s+pdf)?\s+(.+\.pdf)$", raw)
        if m:
            return run_ingest_linkedin_pdf(m.group(1).strip())

        m = re.match(r"(?i)^tailor\s+(.+)$", raw)
        if m:
            return run_tailor(m.group(1).strip())

        # 1c) Ingestion intent from token combos — takes priority over data queries.
        # Catches freeform phrasing like "i want to ingest skill from my github".
        ingest_verbs = {"ingest", "import", "fetch", "pull", "add", "load", "parse", "upload"}
        if tokens & ingest_verbs:
            if _has_token_close_to({"github"}, 0.85):
                m2 = re.search(r"github\s+(\S+)", normalized)
                if m2 and m2.group(1) not in {"repos", "username", "user", "account", "profile", "my"}:
                    return run_ingest_github(m2.group(1))
                return self._ingest_github_with_options()
            if _has_token_close_to({"resume", "cv"}, 0.85):
                m2 = re.search(r"(?:resume|cv)\s+(\S+\.(?:pdf|docx?|md))", normalized)
                if m2:
                    return run_ingest_resume(m2.group(1))
                return (
                    "Please provide the resume file path:\n\n"
                    "  `ingest resume <path>`\n\n"
                    "Example: `ingest resume /path/to/resume.pdf`\n"
                    "Supported formats: PDF, DOCX, MD"
                )
            if _has_token_close_to({"linkedin"}, 0.85):
                m2 = re.search(r"linkedin\s+(\S+\.pdf)", normalized)
                if m2:
                    return run_ingest_linkedin_pdf(m2.group(1))
                return (
                    "Please provide the LinkedIn PDF path:\n\n"
                    "  `ingest linkedin pdf <path>`\n\n"
                    "Example: `ingest linkedin pdf /path/to/linkedin.pdf`"
                )

        # 2) Dedicated skill evidence parser.
        evidence_match = re.search(
            r"(?:evidence|proof|support)\s+(?:for|of)\s+([a-z0-9\-\+\.# ]+)$",
            normalized,
        )
        if evidence_match:
            skill_name = evidence_match.group(1).strip()
            if skill_name:
                return query_skill_evidence(skill_name)

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
