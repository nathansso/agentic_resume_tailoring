"""
Resume Tailoring Agent — Uses LangGraph for a generate-then-evaluate loop.

Flow:
  1. Generate tailored resume content based on matched/missing skills
  2. Evaluate coverage of job requirements in the tailored output
  3. If coverage is insufficient, retry with feedback (up to MAX_RETRIES)
  4. Save final tailored content to UserJobResult
"""
import logging
import json
from typing import Dict, Any, List, TypedDict, Annotated
from uuid import UUID
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END

from llm import get_llm
from database.db import engine
from database.models import (
    User, Experience, Project, ProjectBlurb, Skill, UserSkill,
    JobDescription, JobSkill, UserJobResult,
)

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


class TailorState(TypedDict):
    """State that flows through the LangGraph tailoring pipeline."""
    user_id: str
    job_id: str
    result_id: str
    resume_text: str          # The user's raw resume markdown
    job_text: str             # Raw job description
    matched_skills: Dict      # From SkillMatcherAgent
    missing_skills: List[str]
    experiences: List[Dict]
    projects: List[Dict]
    tailored_content: Dict    # The generated tailored resume sections
    evaluation: Dict          # Self-evaluation results
    attempt: int
    done: bool


class ResumeTailorAgent:
    """
    Generates a tailored resume by:
    - Selecting the best project blurb styles for the target job
    - Rewriting experience bullets to emphasize matched skills
    - Self-evaluating coverage and retrying if needed
    """

    def __init__(self):
        self.llm = get_llm(role="tailor", temperature=0.3)
        self.graph = self._build_graph()

    def tailor(self, user_id: UUID, job_id: UUID, result_id: UUID, resume_text: str = "") -> Dict:
        """
        Run the tailoring pipeline for a given user-job match.
        Returns the tailored resume content dict.
        """
        with Session(engine) as session:
            # Load context
            job = session.exec(select(JobDescription).where(JobDescription.job_id == job_id)).first()
            result = session.exec(select(UserJobResult).where(UserJobResult.result_id == result_id)).first()
            experiences = session.exec(select(Experience).where(Experience.user_id == user_id)).all()
            projects = session.exec(select(Project).where(Project.user_id == user_id)).all()

            exp_dicts = [
                {
                    "title": e.title,
                    "company": e.company,
                    "start_date": e.start_date,
                    "end_date": e.end_date,
                    "description": e.description,
                    "bullets": e.bullets or [],
                }
                for e in experiences
            ]

            proj_dicts = []
            for p in projects:
                blurbs = session.exec(
                    select(ProjectBlurb).where(ProjectBlurb.project_id == p.project_id)
                ).all()
                proj_dicts.append({
                    "name": p.name,
                    "description": p.description,
                    "blurbs": {b.style: b.content for b in blurbs},
                })

        initial_state: TailorState = {
            "user_id": str(user_id),
            "job_id": str(job_id),
            "result_id": str(result_id),
            "resume_text": resume_text,
            "job_text": job.description if job else "",
            "matched_skills": result.matched_skills if result else {},
            "missing_skills": result.missing_skills if result else [],
            "experiences": exp_dicts,
            "projects": proj_dicts,
            "tailored_content": {},
            "evaluation": {},
            "attempt": 0,
            "done": False,
        }

        final_state = self.graph.invoke(initial_state)

        # Save to DB
        with Session(engine) as session:
            result = session.exec(
                select(UserJobResult).where(UserJobResult.result_id == result_id)
            ).first()
            if result:
                result.tailored_resume_content = final_state["tailored_content"]
                session.add(result)
                session.commit()

        logger.info(f"Tailoring complete after {final_state['attempt']} attempt(s)")
        return final_state["tailored_content"]

    def _build_graph(self) -> StateGraph:
        """Build the LangGraph state graph for the tailoring loop."""
        graph = StateGraph(TailorState)

        graph.add_node("generate", self._generate_node)
        graph.add_node("evaluate", self._evaluate_node)

        graph.set_entry_point("generate")
        graph.add_edge("generate", "evaluate")
        graph.add_conditional_edges(
            "evaluate",
            self._should_retry,
            {"retry": "generate", "done": END},
        )

        return graph.compile()

    def _generate_node(self, state: TailorState) -> TailorState:
        """Generate tailored resume content."""
        logger.info(f"Generating tailored content (attempt {state['attempt'] + 1})...")

        feedback = ""
        if state["evaluation"]:
            feedback = (
                f"\n\nPREVIOUS ATTEMPT FEEDBACK:\n"
                f"Coverage was {state['evaluation'].get('coverage_pct', 0)}%. "
                f"Missing emphasis on: {', '.join(state['evaluation'].get('gaps', []))}. "
                f"Please address these gaps."
            )

        matched_str = json.dumps(state["matched_skills"], indent=2)
        missing_str = ", ".join(state["missing_skills"])
        exp_str = json.dumps(state["experiences"], indent=2)
        proj_str = json.dumps(state["projects"], indent=2)

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an expert resume tailoring assistant. Your job is to rewrite resume content "
             "to best match a specific job description. Rules:\n"
             "- NEVER fabricate experiences, skills, or metrics the candidate doesn't have\n"
             "- Emphasize matched skills by reordering bullets and adjusting language\n"
             "- For projects with multiple blurb styles, select the style that best fits the job\n"
             "- Rewrite experience bullets to highlight relevant skills where truthful\n"
             "- Keep the same structure: experiences and projects sections\n"
             "- Respond with ONLY valid JSON, no extra text"),
            ("user",
             "JOB DESCRIPTION:\n{job_text}\n\n"
             "MATCHED SKILLS:\n{matched_skills}\n\n"
             "MISSING SKILLS (do NOT fabricate these):\n{missing_skills}\n\n"
             "CANDIDATE EXPERIENCES:\n{experiences}\n\n"
             "CANDIDATE PROJECTS (with style variants):\n{projects}\n\n"
             "{feedback}\n\n"
             "Return JSON with this structure:\n"
             '{{"experiences": [{{"title": "...", "company": "...", "start_date": "...", '
             '"end_date": "...", "bullets": ["..."]}}], '
             '"projects": [{{"name": "...", "selected_style": "...", "bullets": ["..."]}}], '
             '"skills_emphasized": ["skill1", "skill2"]}}')
        ])

        chain = prompt | self.llm | JsonOutputParser()
        try:
            tailored = chain.invoke({
                "job_text": state["job_text"][:3000],  # Truncate to fit context
                "matched_skills": matched_str,
                "missing_skills": missing_str,
                "experiences": exp_str,
                "projects": proj_str,
                "feedback": feedback,
            })
            state["tailored_content"] = tailored
        except Exception as e:
            logger.error(f"Generation failed: {e}")
            state["tailored_content"] = {"error": str(e)}

        state["attempt"] = state["attempt"] + 1
        return state

    def _evaluate_node(self, state: TailorState) -> TailorState:
        """Evaluate how well the tailored content covers job requirements."""
        logger.info("Evaluating tailored content...")

        if "error" in state["tailored_content"]:
            state["evaluation"] = {"coverage_pct": 0, "gaps": ["generation_failed"]}
            state["done"] = True
            return state

        # Count how many matched skills appear in the tailored output
        tailored_str = json.dumps(state["tailored_content"]).lower()
        matched = state["matched_skills"]
        total = len(matched)
        covered = 0
        gaps = []

        for skill_name in matched:
            if skill_name.lower() in tailored_str:
                covered += 1
            else:
                gaps.append(skill_name)

        coverage_pct = (covered / total * 100) if total > 0 else 100

        state["evaluation"] = {
            "coverage_pct": round(coverage_pct, 1),
            "covered": covered,
            "total": total,
            "gaps": gaps,
        }

        logger.info(f"Coverage: {coverage_pct:.1f}% ({covered}/{total} skills mentioned)")

        if coverage_pct >= 75 or state["attempt"] >= MAX_RETRIES:
            state["done"] = True

        return state

    def _should_retry(self, state: TailorState) -> str:
        """Decide whether to retry generation or finish."""
        if state["done"]:
            return "done"
        return "retry"
