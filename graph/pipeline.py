"""
LangGraph Pipeline — Orchestrates the full resume tailoring flow.

Nodes:
  ingest_resume  → Parse resume into DB (skip if user already has data)
  ingest_job     → Read job description text/file
  analyze_job    → Extract structured skills from job via LLM
  match_skills   → Compare user skills vs job requirements
  tailor_resume  → Generate tailored resume content (with reflection loop)

The pipeline takes a PipelineState and flows through each node,
with conditional edges to skip steps when data already exists.
"""
import logging
from typing import Dict, Any, List, Optional, TypedDict
from uuid import UUID
from pathlib import Path
from sqlmodel import Session, select
from langgraph.graph import StateGraph, END

from database.db import engine
from database.models import User, Experience, UserSkill, JobDescription, UserJobResult
from database.user_utils import require_active_user
from ingestion.job import JobIngestor
from agents.parser import ResumeParserAgent
from agents.job_analyzer import JobAnalyzerAgent
from agents.matcher import SkillMatcherAgent
from agents.tailor import ResumeTailorAgent
from agents.formatter import ResumeFormatterAgent

logger = logging.getLogger(__name__)


class PipelineState(TypedDict):
    # Inputs
    resume_path: str           # Path to resume file (PDF/DOCX/MD)
    job_text: str              # Raw job description text
    job_file: str              # Path to job description file
    # Intermediate
    user_id: str
    job_id: str
    result_id: str
    resume_text: str           # Raw resume text for tailor
    revision_notes: str        # User re-tailor instructions (issue #70)
    plan_override: Dict        # Chat-approved tailoring plan (issue #91), optional
    # Output
    ats_score: float
    matched_skills: Dict
    missing_skills: List[str]
    tailored_content: Dict
    formatted_resume: str      # Final markdown resume
    status: str                # Current pipeline status message


def build_pipeline() -> StateGraph:
    """Build and compile the full tailoring pipeline."""
    graph = StateGraph(PipelineState)

    graph.add_node("ingest_resume", ingest_resume_node)
    graph.add_node("ingest_job", ingest_job_node)
    graph.add_node("analyze_job", analyze_job_node)
    graph.add_node("match_skills", match_skills_node)
    graph.add_node("tailor_resume", tailor_resume_node)
    graph.add_node("format_resume", format_resume_node)

    graph.set_entry_point("ingest_resume")

    # Conditional: skip resume ingestion if user has data
    graph.add_conditional_edges(
        "ingest_resume",
        _resume_next,
        {"ingest_job": "ingest_job"},
    )
    graph.add_edge("ingest_job", "analyze_job")
    graph.add_edge("analyze_job", "match_skills")
    graph.add_edge("match_skills", "tailor_resume")
    graph.add_edge("tailor_resume", "format_resume")
    graph.add_edge("format_resume", END)

    return graph.compile()


# ── Node implementations ─────────────────────────────────────────────

def ingest_resume_node(state: PipelineState) -> PipelineState:
    """Ingest and parse resume if user has no data yet."""
    # Fails closed rather than falling back to an arbitrary user (issue #131).
    # This node sets state["user_id"] for every downstream node, so a wrong
    # resolution here misattributes the entire run.
    user = require_active_user()
    state["user_id"] = str(user.user_id)

    # Check if user already has parsed data
    with Session(engine) as session:
        has_data = session.exec(
            select(Experience).where(Experience.user_id == user.user_id).limit(1)
        ).first()

    if has_data:
        logger.info("User already has resume data — skipping ingestion")
        state["status"] = "Resume data exists, skipping ingestion"
    elif state.get("resume_path"):
        logger.info(f"Ingesting resume: {state['resume_path']}")
        path = state["resume_path"]

        # For markdown files, read directly as text
        if path.endswith(".md"):
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            ingestion_data = {
                "source_file": path,
                "full_text": text,
                "parsed_sections": {},
            }
            state["resume_text"] = text
        else:
            from ingestion.resume import ResumeIngestor
            ingestor = ResumeIngestor()
            ingestion_data = ingestor.ingest(path)
            state["resume_text"] = ingestion_data.get("full_text", "")

        parser = ResumeParserAgent()
        parser.parse_and_save(ingestion_data)
        state["status"] = "Resume ingested and parsed"
    else:
        state["status"] = "No resume path provided and no existing data"

    return state


def ingest_job_node(state: PipelineState) -> PipelineState:
    """Read job description from text or file."""
    ingestor = JobIngestor()
    job_data = ingestor.ingest(
        text=state.get("job_text"),
        file_path=state.get("job_file") or None,
    )
    # Store raw text for downstream
    state["job_text"] = job_data["raw_text"]
    state["status"] = "Job description ingested"
    return state


def analyze_job_node(state: PipelineState) -> PipelineState:
    """Extract structured skills from job description via LLM."""
    analyzer = JobAnalyzerAgent()
    job_data = {"raw_text": state["job_text"], "source": state.get("job_file", "direct_input")}
    job = analyzer.analyze_and_save(job_data)
    state["job_id"] = str(job.job_id)
    state["status"] = f"Job analyzed: {job.title} at {job.company}"
    return state


def match_skills_node(state: PipelineState) -> PipelineState:
    """Compare user skills against job requirements."""
    matcher = SkillMatcherAgent()
    result = matcher.match(
        user_id=UUID(state["user_id"]),
        job_id=UUID(state["job_id"]),
    )
    state["result_id"] = str(result.result_id)
    state["ats_score"] = result.ats_score
    state["matched_skills"] = result.matched_skills
    state["missing_skills"] = result.missing_skills
    state["status"] = f"Skills matched — ATS Score: {result.ats_score}%"
    return state


def tailor_resume_node(state: PipelineState) -> PipelineState:
    """Generate tailored resume content with reflection loop."""
    tailor = ResumeTailorAgent()
    # plan_override is passed only when present so test fakes with the
    # original tailor() signature keep working.
    kwargs = {}
    if state.get("plan_override"):
        kwargs["plan_override"] = state["plan_override"]
    tailored = tailor.tailor(
        user_id=UUID(state["user_id"]),
        job_id=UUID(state["job_id"]),
        result_id=UUID(state["result_id"]),
        resume_text=state.get("resume_text", ""),
        revision_notes=state.get("revision_notes", ""),
        **kwargs,
    )
    state["tailored_content"] = tailored
    state["status"] = "Resume tailored"
    return state


def format_resume_node(state: PipelineState) -> PipelineState:
    """Convert tailored JSON into LaTeX resume source."""
    if not state.get("tailored_content") or "error" in state.get("tailored_content", {}):
        state["formatted_resume"] = ""
        state["status"] = "Skipped formatting — no tailored content"
        return state

    formatter = ResumeFormatterAgent(user_id=UUID(state["user_id"]))
    tex = formatter.format_tex(state["tailored_content"])
    state["formatted_resume"] = tex
    state["status"] = "Resume formatted"
    return state


# ── Routing functions ─────────────────────────────────────────────────

def _resume_next(state: PipelineState) -> str:
    """Always proceed to job ingestion after resume step."""
    return "ingest_job"
