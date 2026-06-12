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
from agents.ats_scorer import ATSScoringEngine

logger = logging.getLogger(__name__)

MAX_RETRIES = 2


MAX_PROJECTS = 4  # Max projects to pass to the LLM after keyword scoring


class TailorState(TypedDict):
    """State that flows through the LangGraph tailoring pipeline."""
    user_id: str
    job_id: str
    result_id: str
    resume_text: str          # The user's raw resume markdown
    job_text: str             # Raw job description
    matched_skills: Dict      # From SkillMatcherAgent
    missing_skills: List[str]
    priority_keywords: List[str]  # Top missing JD keywords from ATSScoringEngine
    baseline_breakdown: Dict  # Pre-tailor score_breakdown for delta comparison (issue #12)
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

            proj_dicts_all = []
            for p in projects:
                blurbs = session.exec(
                    select(ProjectBlurb).where(ProjectBlurb.project_id == p.project_id)
                ).all()
                proj_dicts_all.append({
                    "name": p.name,
                    "description": p.description,
                    "blurbs": {b.style: b.content for b in blurbs},
                })

            # Priority keywords: top missing JD keywords from latest score_breakdown
            jd_text = job.description if job else ""
            priority_keywords: List[str] = []
            if result and result.score_breakdown:
                kd = result.score_breakdown.get("keyword_coverage", {})
                priority_keywords = (kd.get("missing_keywords") or [])[:15]

            # Project pre-selection: score projects by JD keyword coverage
            proj_dicts = self._score_and_select_projects(
                proj_dicts_all, jd_text, max_projects=MAX_PROJECTS
            )

        initial_state: TailorState = {
            "user_id": str(user_id),
            "job_id": str(job_id),
            "result_id": str(result_id),
            "resume_text": resume_text,
            "job_text": jd_text,
            "matched_skills": result.matched_skills if result else {},
            "missing_skills": result.missing_skills if result else [],
            "priority_keywords": priority_keywords,
            "baseline_breakdown": (result.score_breakdown or {}) if result else {},
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
                result.tailored_score_breakdown = final_state["evaluation"].get("ats_breakdown", {})
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
            ev = state["evaluation"]
            skill_gaps = ev.get("gaps", [])
            kw_gaps = ev.get("kw_gaps", [])
            feedback = (
                f"\n\nPREVIOUS ATTEMPT FEEDBACK:\n"
                f"Skill coverage: {ev.get('coverage_pct', 0)}%. "
                f"Keyword coverage: {ev.get('kw_coverage', 0)}%.\n"
                f"Missing skill emphasis: {', '.join(skill_gaps) or 'none'}.\n"
                f"Missing keywords to incorporate naturally: {', '.join(kw_gaps) or 'none'}.\n"
                f"Please address these gaps."
            )

        matched_str = json.dumps(state["matched_skills"], indent=2)
        missing_str = ", ".join(state["missing_skills"])
        exp_str = json.dumps(state["experiences"], indent=2)
        proj_str = json.dumps(state["projects"], indent=2)
        kw_str = ", ".join(state.get("priority_keywords", []))

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an expert resume tailoring assistant. Your job is to rewrite resume content "
             "to best match a specific job description. Rules:\n"
             "- NEVER fabricate experiences, skills, or metrics the candidate doesn't have\n"
             "- Emphasize matched skills by reordering bullets and adjusting language\n"
             "- For projects with multiple blurb styles, select the style that best fits the job\n"
             "- Rewrite experience bullets to highlight relevant skills where truthful\n"
             "- Incorporate PRIORITY JD KEYWORDS naturally into bullets where truthfully applicable\n"
             "- List the most relevant experiences first based on keyword relevance\n"
             "- Keep the same structure: experiences and projects sections\n"
             "- Respond with ONLY valid JSON, no extra text"),
            ("user",
             "JOB DESCRIPTION:\n{job_text}\n\n"
             "MATCHED SKILLS:\n{matched_skills}\n\n"
             "MISSING SKILLS:\n{missing_skills}\n\n"
             "PRIORITY JD KEYWORDS — incorporate naturally where truthful (do NOT add if not applicable):\n{priority_keywords}\n\n"
             "CANDIDATE EXPERIENCES:\n{experiences}\n\n"
             "CANDIDATE PROJECTS (with style variants, pre-scored by relevance):\n{projects}\n\n"
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
                "priority_keywords": kw_str or "(none)",
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

        # Algorithmic ATS breakdown of the tailored output (issue #12): same
        # engine as the pre-tailor baseline, so the two are directly comparable.
        breakdown = ATSScoringEngine.score_tailored(
            state["tailored_content"],
            state["job_text"],
            matched_skills=state["matched_skills"],
            baseline_breakdown=state.get("baseline_breakdown") or None,
        )

        skill = breakdown["skill_coverage"]
        skill_coverage_pct = skill["score"]

        # Retry threshold stays priority-keyword based: full-JD coverage of a
        # sections-only output is structurally low and would always trigger retries.
        tailored_str = ATSScoringEngine.flatten_tailored_text(state["tailored_content"]).lower()
        priority_keywords = state.get("priority_keywords", [])
        kw_hits = sum(1 for kw in priority_keywords if kw in tailored_str)
        kw_coverage = kw_hits / len(priority_keywords) if priority_keywords else 1.0
        kw_gaps = [kw for kw in priority_keywords if kw not in tailored_str]
        if not kw_gaps:
            kw_gaps = breakdown["keyword_coverage"]["missing_keywords"]

        state["evaluation"] = {
            "coverage_pct": round(skill_coverage_pct, 1),
            "covered": skill["covered"],
            "total": skill["total"],
            "gaps": skill["gaps"],
            "kw_coverage": round(kw_coverage * 100, 1),
            "kw_gaps": kw_gaps[:10],
            "ats_breakdown": breakdown,
        }

        logger.info(
            f"Coverage: skills {skill_coverage_pct:.1f}% ({skill['covered']}/{skill['total']}), "
            f"priority keywords {kw_coverage * 100:.1f}% ({kw_hits}/{len(priority_keywords)}), "
            f"algorithmic composite {breakdown['composite']}"
            + (f" (baseline {breakdown['baseline_composite']}, delta {breakdown['delta']:+})"
               if "delta" in breakdown else "")
        )

        coverage_ok = skill_coverage_pct >= 75 and kw_coverage >= 0.60
        if coverage_ok or state["attempt"] >= MAX_RETRIES:
            state["done"] = True

        return state

    def _should_retry(self, state: TailorState) -> str:
        """Decide whether to retry generation or finish."""
        if state["done"]:
            return "done"
        return "retry"

    @staticmethod
    def _score_and_select_projects(
        proj_dicts: List[Dict],
        jd_text: str,
        max_projects: int = MAX_PROJECTS,
    ) -> List[Dict]:
        """
        Score each project against JD keywords, return top max_projects.
        Adds a 'keyword_score' hint to each selected project so the LLM
        knows which are most relevant.
        """
        if not proj_dicts:
            return []

        jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
        if not jd_keywords:
            return proj_dicts[:max_projects]

        scored = []
        for p in proj_dicts:
            text_parts = [p.get("name") or "", p.get("description") or ""]
            for blurb_content in (p.get("blurbs") or {}).values():
                text_parts.append(blurb_content or "")
            project_text = " ".join(text_parts).lower()
            hits = sum(1 for kw in jd_keywords if kw in project_text)
            score = hits / len(jd_keywords)
            scored.append({**p, "keyword_score": round(score, 3)})

        scored.sort(key=lambda x: x["keyword_score"], reverse=True)
        selected = scored[:max_projects]
        logger.debug(
            "Project selection: %s",
            [(p["name"], p["keyword_score"]) for p in selected],
        )
        return selected
