"""
Resume Tailoring Agent — Uses LangGraph for a generate-then-evaluate loop.

Flow:
  1. Generate tailored resume content based on matched/missing skills
  2. Evaluate coverage of job requirements in the tailored output
  3. Retry with feedback until an attempt clears the high "great" bar or the
     iteration budget (MAX_RETRIES) is spent — generation is stochastic, so we
     run the budget rather than stopping at the first acceptable attempt
  4. Save the best-scoring attempt (not the last) to UserJobResult (issue #58)
"""
import logging
import json
import re
from typing import Dict, Any, List, Optional, TypedDict, Annotated
from uuid import UUID
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from langgraph.graph import StateGraph, END

from llm import get_llm
from database.db import engine
from database.models import (
    User, Experience, Project, ProjectBlurb, Skill, UserSkill,
    JobDescription, JobSkill, UserJobResult, Achievement,
)
from agents.ats_scorer import ATSScoringEngine
from agents.project_scorer import MAX_PROJECTS, score_project, select_top_k
from agents.skill_scorer import _env_float, _env_int, rank_and_select_skills
from agents.skill_postprocessor import normalize_skill_name, should_reject_skill
from agents.keyword_planner import score_keywords, assign_keywords, evaluate_placement

logger = logging.getLogger(__name__)

# Iteration budget for the generate→evaluate loop. Generation is stochastic
# (temp 0.3), so we run the full budget by default and ship the best-scoring
# attempt rather than the last (issue #58). The high "great" bar is an early
# exit: once an attempt clears it, further iteration isn't worth the LLM call.
MAX_RETRIES = _env_int("TAILOR_MAX_RETRIES", 2)
GREAT_SKILL_COVERAGE = _env_float("TAILOR_GREAT_SKILL_COVERAGE", 90.0)
GREAT_KW_COVERAGE = _env_float("TAILOR_GREAT_KW_COVERAGE", 0.80)

# Per-experience bullet budgets: the most JD-relevant experience gets up to
# MAX_EXP_BULLETS bullets, the least relevant as few as MIN_EXP_BULLETS, so
# text volume tracks relevance instead of every experience getting equal space.
# The floor is 2 (not 1): a single-bullet experience reads as an afterthought,
# and issue #72 asks that we revise/keep experiences rather than starve them.
MAX_EXP_BULLETS = _env_int("TAILOR_MAX_EXP_BULLETS", 4)
MIN_EXP_BULLETS = _env_int("TAILOR_MIN_EXP_BULLETS", 2)

# Date strings the extractor emits when a real date is absent. They are stored
# verbatim and would otherwise render as "Not specified -- Present" (issue #72);
# tailoring coerces them to None so the formatter omits the date instead.
_PLACEHOLDER_DATE_TOKENS = {
    "", "not specified", "unknown", "unspecified", "n/a", "na", "none", "tbd", "-",
}

# A skill term used more than this many times across the tailored output reads
# as keyword stuffing; the evaluator flags offenders into retry feedback.
MAX_TERM_MENTIONS = _env_int("TAILOR_MAX_TERM_MENTIONS", 3)

# Revision faithfulness (issue #72): mean token overlap between a revised bullet
# and its closest source bullet. Below this, the "revision" drifted far enough
# from the source to read as a rewrite; the evaluator nudges the retry to stay
# closer to the original wording. Lenient by design — keyword insertion and
# tightening legitimately lower overlap.
FAITHFULNESS_MIN = _env_float("TAILOR_FAITHFULNESS_MIN", 0.2)


def _as_obj(value, default):
    """Normalise a JSON column that may round-trip as a JSON string on SQLite."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default
    return value if value is not None else default

# Section ordering (issue #22). The name/contact header is rendered by the
# formatter above all sections and is never part of section_order; education
# stays pinned at the top of the reorderable body.
PINNED_SECTIONS = ["education"]
REORDERABLE_SECTIONS = ["experience", "projects", "skills", "achievements"]


class TailorState(TypedDict):
    """State that flows through the LangGraph tailoring pipeline."""
    user_id: str
    job_id: str
    result_id: str
    resume_text: str          # The user's raw resume markdown
    job_text: str             # Raw job description
    revision_notes: str       # User instructions for iterative re-tailoring (issue #70)
    matched_skills: Dict      # From SkillMatcherAgent
    missing_skills: List[str]
    priority_keywords: List[str]  # Top missing JD keywords, signal-ranked (issue #72)
    keyword_assignments: Dict     # {item_key: [keyword,...]} contextual placement (issue #72)
    baseline_breakdown: Dict  # Pre-tailor score_breakdown for delta comparison (issue #12)
    experiences: List[Dict]
    projects: List[Dict]
    tailored_content: Dict    # The most recent generated tailored resume sections
    evaluation: Dict          # Self-evaluation of the most recent attempt
    best_content: Dict        # Highest-scoring attempt seen so far (issue #58)
    best_evaluation: Dict     # Evaluation backing best_content
    best_score: float         # Composite ATS score of best_content (-1 = none yet)
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

    def tailor(
        self,
        user_id: UUID,
        job_id: UUID,
        result_id: UUID,
        resume_text: str = "",
        revision_notes: str = "",
    ) -> Dict:
        """
        Run the tailoring pipeline for a given user-job match.
        *revision_notes* carries user instructions for iterative re-tailoring
        (issue #70); empty means a plain tailoring run.
        Returns the tailored resume content dict.
        """
        with Session(engine) as session:
            # Load context
            job = session.exec(select(JobDescription).where(JobDescription.job_id == job_id)).first()
            result = session.exec(select(UserJobResult).where(UserJobResult.result_id == result_id)).first()
            # Defensive: JSON columns can round-trip as a JSON string on SQLite;
            # normalise so downstream .get()/iteration is safe.
            if result:
                result.score_breakdown = _as_obj(result.score_breakdown, {})
                result.matched_skills = _as_obj(result.matched_skills, {})
                result.missing_skills = _as_obj(result.missing_skills, [])
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

            # Skill evidence rows for the knowledge-graph degree signal (issue #46)
            evidence_rows = session.exec(
                select(UserSkill.skill_id, UserSkill.evidence_source, UserSkill.evidence_detail)
                .where(UserSkill.user_id == user_id)
            ).all()

            proj_dicts_all = []
            for p in projects:
                blurbs = session.exec(
                    select(ProjectBlurb).where(ProjectBlurb.project_id == p.project_id)
                ).all()
                proj_dicts_all.append({
                    "name": p.name,
                    "description": p.description,
                    "blurbs": {b.style: b.content for b in blurbs},
                    "linked_skills": self._count_linked_skills(p.name, p.repo_url, evidence_rows),
                    "metrics": _as_obj(p.metrics, {}),
                    "repo_url": p.repo_url,
                    "demo_url": p.demo_url,
                    "start_date": p.start_date,
                    "end_date": p.end_date,
                })

            jd_text = job.description if job else ""
            # Drop malformed/duplicate experience rows and coerce placeholder
            # dates before ranking, so junk rows never reach the resume (issue #72).
            exp_dicts = self._filter_and_dedupe_experiences(exp_dicts)
            # Relevance-rank experiences and attach per-experience bullet budgets
            # so text volume tracks JD relevance (most relevant = most detail).
            exp_dicts = self._score_and_budget_experiences(exp_dicts, jd_text)

            # Project pre-selection: score projects by JD relevance + depth and
            # dynamically choose the top-k (issue #47), ordered descending by score.
            proj_dicts = self._score_and_select_projects(proj_dicts_all, jd_text)

            # Signal-rank the missing JD keywords and assign each to the specific
            # experience/project whose own content supports it (issue #72), so the
            # generator inserts keywords contextually instead of stapling the first
            # 15 onto arbitrary bullets. Corpus + skill names drive the ranking.
            missing_keywords: List[str] = []
            if result and result.score_breakdown:
                kd = result.score_breakdown.get("keyword_coverage", {})
                missing_keywords = kd.get("missing_keywords") or []
            corpus = [d for d in session.exec(select(JobDescription.description)).all() if d]
            skill_id_rows = session.exec(
                select(UserSkill.skill_id).where(UserSkill.user_id == user_id)
            ).all()
            skill_terms = [
                s for s in session.exec(
                    select(Skill.name).where(Skill.skill_id.in_(skill_id_rows))
                ).all()
            ] if skill_id_rows else []

            priority_keywords, keyword_assignments = self._plan_keywords(
                exp_dicts, proj_dicts, jd_text, missing_keywords, skill_terms, corpus
            )

        initial_state: TailorState = {
            "user_id": str(user_id),
            "job_id": str(job_id),
            "result_id": str(result_id),
            "resume_text": resume_text,
            "job_text": jd_text,
            "revision_notes": revision_notes.strip(),
            "matched_skills": result.matched_skills if result else {},
            "missing_skills": result.missing_skills if result else [],
            "priority_keywords": priority_keywords,
            "keyword_assignments": keyword_assignments,
            "baseline_breakdown": (result.score_breakdown or {}) if result else {},
            "experiences": exp_dicts,
            "projects": proj_dicts,
            "tailored_content": {},
            "evaluation": {},
            "best_content": {},
            "best_evaluation": {},
            "best_score": -1.0,
            "attempt": 0,
            "done": False,
        }

        final_state = self.graph.invoke(initial_state)

        # Best-of-N: ship the highest-scoring attempt, not whatever the last retry
        # produced — generation is stochastic and a later attempt can regress
        # (issue #58). Fall back to the last content when no attempt scored (e.g.
        # every generation errored), so the error path below still triggers.
        tailored = final_state.get("best_content") or final_state["tailored_content"]
        evaluation = final_state.get("best_evaluation") or final_state["evaluation"]

        # Job-specific section order (issue #22) and JD-ranked skills (issue #54),
        # persisted with the content so the stateless export endpoints can read
        # them later. Skills are ranked first so the section-order relevance signal
        # (which flattens skills_ranked) sees the tailored skill set.
        if tailored and "error" not in tailored:
            ranked_skills = self._rank_skills(
                user_id, job_id, final_state["job_text"], final_state["matched_skills"]
            )
            if ranked_skills:
                tailored["skills_ranked"] = ranked_skills
            # Achievements pass through verbatim from the knowledge graph — never
            # LLM-rewritten or fabricated (keep-all). Injected before ranking so
            # the section is scored and placed like any other reorderable section.
            achievements = self._load_achievements(user_id)
            if achievements:
                tailored["achievements"] = achievements
            tailored["_section_order"] = self._ranked_section_order(
                tailored, final_state["matched_skills"], final_state["job_text"],
                self._ingested_section_order(user_id),
            )
            # One-page guarantee at the source (issue #34 follow-up): trim the
            # stored content so the editor .tex, live preview, and exports all
            # fit a single page — not just the PDF export path.
            from agents.formatter import ResumeFormatterAgent
            tailored = ResumeFormatterAgent(user_id).fit_content_to_one_page(
                tailored, section_order=tailored.get("_section_order")
            )

        # Save to DB
        with Session(engine) as session:
            result = session.exec(
                select(UserJobResult).where(UserJobResult.result_id == result_id)
            ).first()
            if result:
                result.tailored_resume_content = tailored
                result.tailored_score_breakdown = evaluation.get("ats_breakdown", {})
                if revision_notes.strip():
                    result.revision_notes = revision_notes.strip()
                # Re-tailoring supersedes any manual .tex edits (issue #71):
                # the saved source no longer matches the tailored content.
                result.edited_tex = None
                result.edited_tex_updated_at = None
                session.add(result)
                session.commit()

        logger.info(
            "Tailoring complete after %d attempt(s); shipped best (composite %s)",
            final_state["attempt"], final_state.get("best_score"),
        )
        return tailored

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
            over = ev.get("over_repeated", {})
            over_str = ", ".join(f"{t} ({n}x)" for t, n in over.items())
            # Per-item placement gaps: which assigned keyword is still missing from
            # the item it belongs on, and which keyword leaked onto the wrong item.
            place_gaps = ev.get("placement_gaps", {})
            misplaced = ev.get("placement_misplaced", {})
            gap_lines = "; ".join(
                f"{self._label_for_key(key, state)}: add {', '.join(kws)}"
                for key, kws in place_gaps.items()
            )
            misplaced_lines = "; ".join(
                f"{', '.join(kws)} landed on {self._label_for_key(key, state)} but belongs elsewhere — remove it there"
                for key, kws in misplaced.items()
            )
            drift = ev.get("faithfulness_drift", [])
            feedback = (
                f"\n\nPREVIOUS ATTEMPT FEEDBACK:\n"
                f"Skill coverage: {ev.get('coverage_pct', 0)}%. "
                f"Keyword placement precision: {ev.get('kw_coverage', 0)}%.\n"
                f"Missing skill emphasis: {', '.join(skill_gaps) or 'none'}.\n"
                + (f"Weave these assigned keywords into the right item: {gap_lines}.\n" if gap_lines else "")
                + (f"Misplaced keywords: {misplaced_lines}.\n" if misplaced_lines else "")
                + (f"These experiences drifted too far from their source bullets — "
                   f"revise the original wording, don't rewrite: {', '.join(drift)}.\n" if drift else "")
                + (f"Overused terms — mentioned too often, vary the wording or cut "
                   f"mentions to at most {MAX_TERM_MENTIONS} each: {over_str}.\n" if over_str else "")
                + f"Please address these gaps."
            )

        revision = ""
        if state.get("revision_notes"):
            revision = (
                "\n\nUSER REVISION REQUEST — the candidate reviewed a previous tailored "
                "version and asked for these changes; follow them where truthful:\n"
                f"{state['revision_notes']}"
            )

        matched_str = json.dumps(state["matched_skills"], indent=2)
        missing_str = ", ".join(state["missing_skills"])
        exp_str = json.dumps(state["experiences"], indent=2)
        # repo_url/demo_url are re-attached deterministically after generation
        # (see _merge_project_links) rather than trusted to the LLM round trip.
        proj_str = json.dumps(
            [{k: v for k, v in p.items() if k not in ("repo_url", "demo_url")}
             for p in state["projects"]],
            indent=2,
        )
        kw_str = ", ".join(state.get("priority_keywords", []))

        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an expert resume tailoring assistant. You REVISE existing resume content to "
             "match a job description — you do not rewrite it from scratch. Rules:\n"
             "- NEVER fabricate experiences, skills, or metrics the candidate doesn't have\n"
             "- REVISE, don't rewrite: keep each bullet's underlying facts, numbers, and meaning. "
             "Reorder, tighten, and adjust wording to surface relevance — but a reader who saw the "
             "original must recognize the revised bullet as the same accomplishment\n"
             "- Preserve any `[text](url)` markdown links present in the source bullets verbatim; "
             "never strip or invent URLs\n"
             "- Each experience and project carries a `suggested_keywords` list: these were matched "
             "to THAT item because its own content supports them. Weave an item's suggested_keywords "
             "into THAT item's bullets ONLY where truthful. Do NOT move a keyword to a different item, "
             "and do NOT force in a keyword the item cannot honestly support\n"
             "- For projects with multiple blurb styles, select the style that best fits the job\n"
             "- Keep projects and experiences in the given order; they are pre-ranked by job relevance\n"
             "- Each experience has a bullet_budget: write AT MOST that many bullets for it. Prefer "
             "revising and keeping strong bullets over dropping them; only drop a bullet to respect "
             "the budget, and never invent filler to reach it\n"
             f"- Avoid repeating the same skill or keyword: use each term at most {MAX_TERM_MENTIONS} times "
             "across ALL bullets combined. Vary phrasing instead of restating the same technology\n"
             "- Keep the same structure: experiences and projects sections\n"
             "- Respond with ONLY valid JSON, no extra text"),
            ("user",
             "JOB DESCRIPTION:\n{job_text}\n\n"
             "MATCHED SKILLS:\n{matched_skills}\n\n"
             "MISSING SKILLS:\n{missing_skills}\n\n"
             "PRIORITY JD KEYWORDS (overall signal ranking; placement is governed per-item by each "
             "item's suggested_keywords below):\n{priority_keywords}\n\n"
             "CANDIDATE EXPERIENCES (each with source bullets to revise and its suggested_keywords):\n{experiences}\n\n"
             "CANDIDATE PROJECTS (with style variants and suggested_keywords, pre-scored by relevance + depth):\n{projects}\n\n"
             "{revision}"
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
                "revision": revision,
                "feedback": feedback,
            })
            # Guarantee projects stay in the pre-ranked (descending-score) order even
            # if the LLM reorders or drops them in its JSON output (issue #47).
            if isinstance(tailored, dict) and tailored.get("projects"):
                tailored["projects"] = self._order_projects_by_selection(
                    tailored["projects"], [p["name"] for p in state["projects"]]
                )
                tailored["projects"] = self._merge_project_links(
                    tailored["projects"], state["projects"]
                )
            # Same guarantee for experiences: relevance order + bullet budgets
            # hold even when the LLM ignores the prompt rules.
            if isinstance(tailored, dict) and tailored.get("experiences"):
                tailored["experiences"] = self._enforce_bullet_budgets(
                    tailored["experiences"], state["experiences"]
                )
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

        # Keyword coverage is now PLACEMENT precision (issue #72): a keyword counts
        # only when it lands in the specific item it was assigned to, so the loop
        # rewards contextual placement rather than blind attachment anywhere.
        assignments = state.get("keyword_assignments") or {}
        placement = evaluate_placement(assignments, self._rendered_by_key(state["tailored_content"]))
        kw_coverage = placement["precision"]

        # Faithfulness: flag experiences whose revision drifted far from source.
        drift = self._faithfulness_drift(state["tailored_content"], state.get("experiences") or [])

        evaluation = {
            "coverage_pct": round(skill_coverage_pct, 1),
            "covered": skill["covered"],
            "total": skill["total"],
            "gaps": skill["gaps"],
            "kw_coverage": round(kw_coverage * 100, 1),
            "placement_gaps": placement["gaps"],
            "placement_misplaced": placement["misplaced"],
            "faithfulness_drift": drift,
            "over_repeated": self._over_repeated_terms(state["tailored_content"]),
            "ats_breakdown": breakdown,
        }
        state["evaluation"] = evaluation

        # Best-of-N: retain the highest-scoring attempt by algorithmic composite,
        # since a stochastic retry can regress and we must never ship a worse
        # output than one we already produced (issue #58).
        composite = breakdown["composite"]
        if composite > state["best_score"]:
            state["best_score"] = composite
            state["best_content"] = state["tailored_content"]
            state["best_evaluation"] = evaluation

        logger.info(
            f"Coverage: skills {skill_coverage_pct:.1f}% ({skill['covered']}/{skill['total']}), "
            f"keyword placement {kw_coverage * 100:.1f}% ({placement['placed']}/{placement['total']}), "
            f"algorithmic composite {breakdown['composite']}"
            + (f" (baseline {breakdown['baseline_composite']}, delta {breakdown['delta']:+})"
               if "delta" in breakdown else "")
        )

        # Stop when an attempt clears the high "great" bar (early exit — further
        # iteration isn't worth the LLM call) or the iteration budget is spent.
        # Below the bar we keep iterating and keep the best; we no longer halt at
        # a mediocre "good enough" floor (issue #58).
        great = skill_coverage_pct >= GREAT_SKILL_COVERAGE and kw_coverage >= GREAT_KW_COVERAGE
        if great or state["attempt"] >= MAX_RETRIES:
            state["done"] = True

        return state

    def _should_retry(self, state: TailorState) -> str:
        """Decide whether to retry generation or finish."""
        if state["done"]:
            return "done"
        return "retry"

    @staticmethod
    def _score_and_budget_experiences(exp_dicts: List[Dict], jd_text: str) -> List[Dict]:
        """
        Order experiences by JD relevance (keyword overlap, like project
        selection) and attach a bullet_budget: the most relevant experience may
        keep up to MAX_EXP_BULLETS bullets, the least relevant as few as
        MIN_EXP_BULLETS. No JD signal → order and budgets left untouched.
        """
        jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
        if not exp_dicts or not jd_keywords:
            return exp_dicts

        scored = []
        for e in exp_dicts:
            text = " ".join(
                [e.get("title") or "", e.get("description") or ""]
                + list(e.get("bullets") or [])
            )
            tokens = ATSScoringEngine._extract_keywords(text)
            rel = len(tokens & jd_keywords) / len(tokens) if tokens else 0.0
            scored.append((rel, e))

        max_rel = max(rel for rel, _ in scored)
        out = []
        # Stable sort: relevance ties keep the original (chronological) order.
        for rel, e in sorted(scored, key=lambda t: t[0], reverse=True):
            norm = rel / max_rel if max_rel > 0 else 1.0
            budget = MIN_EXP_BULLETS + round(norm * (MAX_EXP_BULLETS - MIN_EXP_BULLETS))
            out.append({**e, "relevance_score": round(rel, 3), "bullet_budget": budget})
        return out

    @staticmethod
    def _enforce_bullet_budgets(generated: List[Dict], budgeted: List[Dict]) -> List[Dict]:
        """
        Deterministic guarantee behind the prompt's bullet_budget rule (issue #72):

        - Reorder the LLM's experiences to the pre-ranked relevance order and
          truncate any that exceed their budget.
        - Re-attach dates and canonical title/company from the source rows rather
          than trusting the LLM round trip — the model must not author or malform
          dates (same rationale as project links, issue #75).
        - Restore experiences the model silently dropped: it may shorten an
          experience, never delete one. The number restored is bounded by how many
          the model actually omitted, so a *renamed* experience (count preserved)
          is treated as a replacement, not a deletion, and is not duplicated.

        Experiences the LLM renamed keep their bullets and sort after the
        recognized ones, in original relative order.
        """
        def key(e: Dict) -> tuple:
            return ((e.get("title") or "").strip().lower(),
                    (e.get("company") or "").strip().lower())

        rank = {key(e): i for i, e in enumerate(budgeted)}
        source = {key(e): e for e in budgeted}
        fallback = len(budgeted)

        def _trim(bullets: List, budget) -> List:
            bullets = bullets or []
            return bullets[:budget] if budget and len(bullets) > budget else bullets

        out: List[Dict] = []
        seen: set = set()
        for e in sorted(generated or [], key=lambda e: rank.get(key(e), fallback)):
            k = key(e)
            src = source.get(k)
            if src is not None:
                seen.add(k)
                e = {
                    **e,
                    "title": src.get("title", e.get("title")),
                    "company": src.get("company", e.get("company")),
                    "start_date": src.get("start_date"),
                    "end_date": src.get("end_date"),
                    "bullets": _trim(e.get("bullets"), src.get("bullet_budget")),
                }
            out.append(e)

        # Restore only as many missing experiences as the model actually dropped
        # (len(source) - len(generated)); renames preserve the count and restore 0.
        n_missing = max(0, len(budgeted) - len(generated or []))
        if n_missing:
            unseen = [src for k, src in source.items() if k not in seen]
            for src in unseen[:n_missing]:
                out.append({
                    "title": src.get("title"),
                    "company": src.get("company"),
                    "start_date": src.get("start_date"),
                    "end_date": src.get("end_date"),
                    "bullets": _trim(src.get("bullets"), src.get("bullet_budget")),
                })

        out.sort(key=lambda e: rank.get(key(e), fallback))
        return out

    # Experience strings that mean "no real date" — coerced to None at tailor
    # time so the formatter omits the date rather than printing the placeholder.
    @staticmethod
    def _clean_exp_date(value) -> Optional[str]:
        v = str(value or "").strip()
        return None if v.lower() in _PLACEHOLDER_DATE_TOKENS else v

    @staticmethod
    def _norm_exp_text(value) -> str:
        """Lowercase, strip punctuation to spaces, collapse whitespace."""
        s = re.sub(r"[^a-z0-9 ]+", " ", str(value or "").lower())
        return re.sub(r"\s+", " ", s).strip()

    @classmethod
    def _fuzzy_eq(cls, a, b) -> bool:
        """Equal after normalization, ignoring spacing ('IDXExchange' ==
        'IDX Exchange'), or containment for sufficiently long strings."""
        na, nb = cls._norm_exp_text(a), cls._norm_exp_text(b)
        if not na or not nb:
            return False
        if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
            return True
        shorter, longer = sorted((na, nb), key=len)
        return len(shorter) >= 10 and shorter in longer

    @classmethod
    def _exp_matches(cls, a: Dict, b: Dict) -> bool:
        return (cls._fuzzy_eq(a.get("title"), b.get("title"))
                and cls._fuzzy_eq(a.get("company"), b.get("company")))

    @staticmethod
    def _exp_richness(e: Dict) -> tuple:
        """Sort key for choosing the best of duplicate rows: more bullets, then
        real dates, then longer description."""
        has_dates = bool(e.get("start_date")) + bool(e.get("end_date"))
        return (len(e.get("bullets") or []), has_dates, len((e.get("description") or "").strip()))

    @classmethod
    def _filter_and_dedupe_experiences(cls, exp_dicts: List[Dict]) -> List[Dict]:
        """
        Clean the experience set before ranking (issue #72):
          1. Coerce placeholder dates ("Not specified", "unknown", …) to None.
          2. Fuzzy-dedupe near-identical rows, keeping the richest of each group
             (e.g. 'IDX Exchange' with 4 bullets over 'IDXExchange' with 0).
          3. Drop content-empty stubs — rows with no bullets and no description —
             which render as a bare heading with blank space.
        """
        cleaned = [
            {
                **e,
                "start_date": cls._clean_exp_date(e.get("start_date")),
                "end_date": cls._clean_exp_date(e.get("end_date")),
            }
            for e in (exp_dicts or [])
        ]

        kept: List[Dict] = []
        for e in cleaned:
            idx = next((i for i, k in enumerate(kept) if cls._exp_matches(e, k)), None)
            if idx is None:
                kept.append(e)
            elif cls._exp_richness(e) > cls._exp_richness(kept[idx]):
                kept[idx] = e

        return [
            e for e in kept
            if (e.get("bullets") or (e.get("description") or "").strip())
        ]

    # ── Contextual keyword planning (issue #72) ──────────────────────────────

    @staticmethod
    def _exp_key(e: Dict) -> str:
        return f"exp:{(e.get('title') or '').strip().lower()}|{(e.get('company') or '').strip().lower()}"

    @staticmethod
    def _proj_key(p: Dict) -> str:
        return f"proj:{(p.get('name') or '').strip().lower()}"

    @classmethod
    def _label_for_key(cls, key: str, state: "TailorState") -> str:
        """Human-readable label (title/name) for an item key, for retry feedback."""
        if key.startswith("exp:"):
            for e in state.get("experiences") or []:
                if cls._exp_key(e) == key:
                    return e.get("title") or key
        elif key.startswith("proj:"):
            for p in state.get("projects") or []:
                if cls._proj_key(p) == key:
                    return p.get("name") or key
        return key

    @staticmethod
    def _exp_source_text(e: Dict) -> str:
        return " ".join([
            e.get("title") or "", e.get("company") or "", e.get("description") or "",
            *(e.get("bullets") or []),
        ])

    @staticmethod
    def _proj_source_text(p: Dict) -> str:
        return " ".join([
            p.get("name") or "", p.get("description") or "",
            *((p.get("blurbs") or {}).values()),
        ])

    @classmethod
    def _plan_keywords(
        cls,
        exp_dicts: List[Dict],
        proj_dicts: List[Dict],
        jd_text: str,
        missing_keywords: List[str],
        skill_terms: List[str],
        corpus: List[str],
    ) -> tuple:
        """
        Signal-rank the missing JD keywords and assign each to the one item whose
        source text supports it (issue #72). Mutates the exp/proj dicts to carry a
        per-item 'suggested_keywords' list for the generator, and returns
        (priority_keywords, assignments) for the state.
        """
        scored = score_keywords(missing_keywords, jd_text, corpus, skill_terms)
        items = (
            [{"key": cls._exp_key(e), "source_text": cls._exp_source_text(e)} for e in exp_dicts]
            + [{"key": cls._proj_key(p), "source_text": cls._proj_source_text(p)} for p in proj_dicts]
        )
        assignments = assign_keywords(scored, items, jd_text)
        for e in exp_dicts:
            e["suggested_keywords"] = assignments.get(cls._exp_key(e), [])
        for p in proj_dicts:
            p["suggested_keywords"] = assignments.get(cls._proj_key(p), [])
        return [kw for kw, _ in scored], assignments

    @classmethod
    def _rendered_by_key(cls, tailored_content: Dict) -> Dict[str, str]:
        """Map each generated item to its rendered bullet text, keyed like the
        assignments, so placement can be scored per item (issue #72)."""
        out: Dict[str, str] = {}
        for e in tailored_content.get("experiences") or []:
            out[cls._exp_key(e)] = " ".join(e.get("bullets") or [])
        for p in tailored_content.get("projects") or []:
            out[cls._proj_key(p)] = " ".join(p.get("bullets") or [])
        return out

    @staticmethod
    def _faithfulness_drift(tailored_content: Dict, source_experiences: List[Dict]) -> List[str]:
        """
        Item labels whose revised bullets drifted far from their source bullets
        (issue #72) — a signal that the model rewrote rather than revised. Uses
        mean best-Jaccard of each source bullet against the generated bullets;
        below FAITHFULNESS_MIN the item is flagged. Items with no source bullets
        (nothing to preserve) are skipped.
        """
        def toks(s: str) -> set:
            return ATSScoringEngine._extract_keywords(s or "")

        src_by_key = {
            f"{(e.get('title') or '').strip().lower()}|{(e.get('company') or '').strip().lower()}": e
            for e in source_experiences
        }
        drifted: List[str] = []
        for gen in tailored_content.get("experiences") or []:
            key = f"{(gen.get('title') or '').strip().lower()}|{(gen.get('company') or '').strip().lower()}"
            src = src_by_key.get(key)
            if not src:
                continue
            src_bullets = src.get("bullets") or []
            gen_toks = [toks(b) for b in (gen.get("bullets") or [])]
            if not src_bullets or not gen_toks:
                continue
            overlaps = []
            for sb in src_bullets:
                st = toks(sb)
                if not st:
                    continue
                best = max(
                    (len(st & gt) / len(st | gt) if (st | gt) else 0.0) for gt in gen_toks
                )
                overlaps.append(best)
            if overlaps and sum(overlaps) / len(overlaps) < FAITHFULNESS_MIN:
                drifted.append(gen.get("title") or key)
        return drifted

    @classmethod
    def _over_repeated_terms(cls, tailored_content: Dict) -> Dict[str, int]:
        """
        Skill terms mentioned more than MAX_TERM_MENTIONS times across the whole
        tailored output (bullets + skills), boundary-aware so "sql" does not
        match inside "mysql". Fed back to the generator as anti-stuffing gaps.
        """
        terms = {
            str(t).lower()
            for t in tailored_content.get("skills_emphasized") or []
            if t
        }
        text = ATSScoringEngine.flatten_tailored_text(tailored_content).lower()
        over: Dict[str, int] = {}
        for term in terms:
            count = len(re.findall(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))
            if count > MAX_TERM_MENTIONS:
                over[term] = count
        return dict(sorted(over.items(), key=lambda kv: -kv[1]))

    @staticmethod
    def _rank_skills(
        user_id: UUID, job_id: UUID, jd_text: str, matched_skills: Dict
    ) -> List[Dict]:
        """
        Rank the user's skills by JD relevance and select the most relevant subset
        (issue #54). Returns [{name, category, score}, ...] in render order, or an
        empty list when there is no JD signal (caller falls back to the full list).

        Phase 2: blends a semantic component from the cached skill/JD embeddings
        (issue #54). Embeddings degrade gracefully — if unavailable, scoring falls
        back to the lexical + metadata signals.
        """
        from agents.skill_embeddings import ensure_job_embedding, load_skill_vectors

        with Session(engine) as session:
            rows = session.exec(
                select(UserSkill).where(UserSkill.user_id == user_id)
            ).all()
            # Dedup by canonical name — a skill may have several UserSkill rows
            # (one per evidence source). Merge: keep the strongest proficiency /
            # confidence and treat the skill as pinned if any row is pinned.
            by_name: Dict[str, Dict] = {}
            id_by_name: Dict[str, UUID] = {}
            for us in rows:
                skill = session.exec(
                    select(Skill).where(Skill.skill_id == us.skill_id)
                ).first()
                if not skill or should_reject_skill(skill.name):
                    continue
                cname = normalize_skill_name(skill.name)
                entry = by_name.get(cname)
                if entry is None:
                    by_name[cname] = {
                        "name": cname,
                        "category": skill.category or "Other",
                        "proficiency": us.proficiency,
                        "confidence": us.confidence_score,
                        "is_core": bool(us.is_core),
                    }
                    id_by_name[cname] = skill.skill_id
                else:
                    if (us.proficiency or 0) > (entry["proficiency"] or 0):
                        entry["proficiency"] = us.proficiency
                    if (us.confidence_score or 0) > (entry["confidence"] or 0):
                        entry["confidence"] = us.confidence_score
                    entry["is_core"] = entry["is_core"] or bool(us.is_core)
            skills: List[Dict] = list(by_name.values())
            # JD corpus for IDF: every stored job description (term-rarity signal).
            corpus = [
                d for d in session.exec(select(JobDescription.description)).all() if d
            ]
            # Phase 2: cached semantic vectors (shared with the matcher).
            vecs_by_id = load_skill_vectors(session, list(id_by_name.values()))
            skill_vectors = {
                name: vecs_by_id[sid]
                for name, sid in id_by_name.items()
                if sid in vecs_by_id
            }
            jd_vector = None
            job = session.exec(
                select(JobDescription).where(JobDescription.job_id == job_id)
            ).first()
            if job:
                jd_vector = ensure_job_embedding(session, job)

        if not skills:
            return []
        ranked = rank_and_select_skills(
            skills, jd_text, matched_skills or {}, corpus, skill_vectors, jd_vector
        )
        return ranked or []

    @staticmethod
    def _score_section_relevance(
        section_key: str,
        tailored_content: Dict,
        matched_skills: Dict,
        jd_text: str,
    ) -> float:
        """
        Fraction of a section's content tokens that overlap the matched skill
        names and JD keywords. Higher = more relevant to this job (issue #22).
        """
        text = ATSScoringEngine.flatten_section_text(tailored_content, section_key)
        tokens = ATSScoringEngine._extract_keywords(text)
        if not tokens:
            return 0.0
        relevant = ATSScoringEngine._extract_keywords(
            " ".join(matched_skills or {}) + " " + (jd_text or "")
        )
        if not relevant:
            return 0.0
        return len(tokens & relevant) / len(tokens)

    @classmethod
    def _ranked_section_order(
        cls,
        tailored_content: Dict,
        matched_skills: Dict,
        jd_text: str,
        ingested_order: Optional[List[str]] = None,
    ) -> List[str]:
        """Pinned sections first, then reorderable sections by relevance score.

        Ties fall back to the user's ingested resume order (so a section with no
        strong JD signal — e.g. achievements — lands where it sat in the resume
        we ingested), and to the default list order for anything the ingested
        resume did not name.
        """
        # Default seed order: the ingested resume's reorderable sections in their
        # original positions, then any reorderable section it didn't mention.
        seed = [k for k in (ingested_order or []) if k in REORDERABLE_SECTIONS]
        for k in REORDERABLE_SECTIONS:
            if k not in seed:
                seed.append(k)
        # Achievements is optional: only place it when the user actually has some,
        # so a user without achievements keeps a clean section order.
        if not tailored_content.get("achievements"):
            seed = [k for k in seed if k != "achievements"]
        scores = {
            key: cls._score_section_relevance(key, tailored_content, matched_skills, jd_text)
            for key in seed
        }
        # Stable sort over the seed: ties keep the ingested/default order
        ranked = sorted(seed, key=lambda k: scores[k], reverse=True)
        logger.debug("Section relevance: %s", scores)
        return PINNED_SECTIONS + ranked

    @staticmethod
    def _load_achievements(user_id: UUID) -> List[Dict]:
        """This user's achievements in resume-document order, verbatim from the
        knowledge graph. Copied into tailored_content unchanged — never rewritten
        or filtered (keep-all)."""
        with Session(engine) as session:
            rows = session.exec(
                select(Achievement).where(Achievement.user_id == user_id)
                .order_by(Achievement.created_at)
            ).all()
            return [
                {
                    "title": a.title,
                    "description": a.description or "",
                    "issuer": a.issuer or "",
                    "date": a.date or "",
                }
                for a in rows
            ]

    @staticmethod
    def _ingested_section_order(user_id: UUID) -> List[str]:
        """The section order captured from the user's ingested resume style, used
        as the tie-break default when JD relevance gives no clear signal."""
        try:
            from services import get_resume_style
            style = get_resume_style(user_id) or {}
            order = style.get("section_order")
            return list(order) if isinstance(order, list) else []
        except Exception:
            return []

    @staticmethod
    def _count_linked_skills(project_name: str, repo_url: str, evidence_rows: List) -> int:
        """
        Knowledge-graph degree: distinct skills whose evidence references this
        project, matched by repo slug (owner/repo from repo_url) or project
        name against UserSkill.evidence_source/evidence_detail (issue #46).
        """
        needles = set()
        if repo_url:
            m = re.search(r"github\.com/([^/\s]+/[^/\s]+?)(?:\.git)?/?$", repo_url)
            if m:
                needles.add(m.group(1).lower())
        if project_name:
            needles.add(project_name.lower())
        if not needles:
            return 0

        skill_ids = set()
        for skill_id, source, detail in evidence_rows:
            haystack = f"{source or ''} {detail or ''}".lower()
            if any(n in haystack for n in needles):
                skill_ids.add(skill_id)
        return len(skill_ids)

    @staticmethod
    def _order_projects_by_selection(
        generated: List[Dict], order_names: List[str]
    ) -> List[Dict]:
        """
        Reorder LLM-generated projects to match the pre-ranked selection order
        (descending by score, issue #47). Projects are keyed by 'name'; any whose
        name isn't in `order_names` (e.g. renamed by the LLM) are kept in their
        original relative order and appended at the end. Stable on ties.
        """
        rank = {name: i for i, name in enumerate(order_names)}
        fallback = len(order_names)
        return sorted(
            generated,
            key=lambda p: rank.get(p.get("name"), fallback),
        )

    @staticmethod
    def _merge_project_links(generated: List[Dict], source: List[Dict]) -> List[Dict]:
        """Re-attach repo_url/demo_url by project name (issue #75).

        The LLM rewrite is unreliable for verbatim field passthrough, so these
        links are sourced from the DB-backed project list rather than the
        model's JSON output — never fabricated, never silently dropped.
        """
        links = {p["name"]: (p.get("repo_url"), p.get("demo_url")) for p in source}
        out = []
        for p in generated:
            repo_url, demo_url = links.get(p.get("name"), (None, None))
            out.append({**p, "repo_url": repo_url, "demo_url": demo_url})
        return out

    # Keys consumed by the selection scorer but not meant for the LLM prompt
    _SCORING_INPUT_KEYS = ("linked_skills", "metrics")

    @classmethod
    def _score_and_select_projects(
        cls,
        proj_dicts: List[Dict],
        jd_text: str,
    ) -> List[Dict]:
        """
        Rank projects by composite selection score (ATS relevance + complexity,
        issue #46) and dynamically select the top-k via the drop-off + bounds rule
        (issue #47). Adds a 'selection_score' and per-component 'selection_breakdown'
        hint to each selected project so the LLM knows which are most relevant, and
        returns them ordered descending by score.
        """
        if not proj_dicts:
            return []

        def strip(p: Dict) -> Dict:
            return {k: v for k, v in p.items() if k not in cls._SCORING_INPUT_KEYS}

        jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
        if not jd_keywords:
            # No JD signal to score against — fall back to the first MAX_PROJECTS.
            return [strip(p) for p in proj_dicts[:MAX_PROJECTS]]

        scored = []
        for p in proj_dicts:
            breakdown = score_project(p, jd_text)
            scored.append({
                **strip(p),
                "selection_score": breakdown["composite"],
                "selection_breakdown": {
                    "relevance": breakdown["relevance"]["score"],
                    "complexity": breakdown["complexity"]["score"],
                    "recency": breakdown["recency"]["score"],
                },
            })

        scored.sort(key=lambda x: x["selection_score"], reverse=True)
        selected = select_top_k(scored)
        logger.debug(
            "Project selection (k=%d of %d): %s",
            len(selected), len(scored),
            [(p["name"], p["selection_score"], p["selection_breakdown"]) for p in selected],
        )
        return selected
