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
from agents.project_scorer import MAX_PROJECTS, score_project, select_top_k
from agents.skill_scorer import _env_float, _env_int, rank_and_select_skills
from agents.skill_postprocessor import normalize_skill_name, should_reject_skill

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
MAX_EXP_BULLETS = _env_int("TAILOR_MAX_EXP_BULLETS", 4)
MIN_EXP_BULLETS = _env_int("TAILOR_MIN_EXP_BULLETS", 1)

# A skill term used more than this many times across the tailored output reads
# as keyword stuffing; the evaluator flags offenders into retry feedback.
MAX_TERM_MENTIONS = _env_int("TAILOR_MAX_TERM_MENTIONS", 3)


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
REORDERABLE_SECTIONS = ["experience", "projects", "skills"]


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

    def tailor(self, user_id: UUID, job_id: UUID, result_id: UUID, resume_text: str = "") -> Dict:
        """
        Run the tailoring pipeline for a given user-job match.
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
                })

            # Priority keywords: top missing JD keywords from latest score_breakdown
            jd_text = job.description if job else ""
            # Relevance-rank experiences and attach per-experience bullet budgets
            # so text volume tracks JD relevance (most relevant = most detail).
            exp_dicts = self._score_and_budget_experiences(exp_dicts, jd_text)
            priority_keywords: List[str] = []
            if result and result.score_breakdown:
                kd = result.score_breakdown.get("keyword_coverage", {})
                priority_keywords = (kd.get("missing_keywords") or [])[:15]

            # Project pre-selection: score projects by JD relevance + depth and
            # dynamically choose the top-k (issue #47), ordered descending by score.
            proj_dicts = self._score_and_select_projects(proj_dicts_all, jd_text)

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
            tailored["_section_order"] = self._ranked_section_order(
                tailored, final_state["matched_skills"], final_state["job_text"]
            )

        # Save to DB
        with Session(engine) as session:
            result = session.exec(
                select(UserJobResult).where(UserJobResult.result_id == result_id)
            ).first()
            if result:
                result.tailored_resume_content = tailored
                result.tailored_score_breakdown = evaluation.get("ats_breakdown", {})
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
            kw_gaps = ev.get("kw_gaps", [])
            over = ev.get("over_repeated", {})
            over_str = ", ".join(f"{t} ({n}x)" for t, n in over.items())
            feedback = (
                f"\n\nPREVIOUS ATTEMPT FEEDBACK:\n"
                f"Skill coverage: {ev.get('coverage_pct', 0)}%. "
                f"Keyword coverage: {ev.get('kw_coverage', 0)}%.\n"
                f"Missing skill emphasis: {', '.join(skill_gaps) or 'none'}.\n"
                f"Missing keywords to incorporate naturally: {', '.join(kw_gaps) or 'none'}.\n"
                + (f"Overused terms — mentioned too often, vary the wording or cut "
                   f"mentions to at most {MAX_TERM_MENTIONS} each: {over_str}.\n" if over_str else "")
                + f"Please address these gaps."
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
             "- Keep projects in the given order; they are pre-ranked by job relevance (most relevant first)\n"
             "- Rewrite experience bullets to highlight relevant skills where truthful\n"
             "- Incorporate PRIORITY JD KEYWORDS naturally into bullets where truthfully applicable\n"
             "- Keep experiences in the given order; they are pre-ranked by job relevance (most relevant first)\n"
             "- Each experience has a bullet_budget: write AT MOST that many bullets for it. "
             "Budgets are relevance-based — spend words on what this job cares about, and keep "
             "only the strongest, most job-relevant bullets for low-budget experiences\n"
             f"- Avoid repeating the same skill or keyword: use each term at most {MAX_TERM_MENTIONS} times "
             "across ALL bullets combined. Vary phrasing instead of restating the same technology\n"
             "- Keep the same structure: experiences and projects sections\n"
             "- Respond with ONLY valid JSON, no extra text"),
            ("user",
             "JOB DESCRIPTION:\n{job_text}\n\n"
             "MATCHED SKILLS:\n{matched_skills}\n\n"
             "MISSING SKILLS:\n{missing_skills}\n\n"
             "PRIORITY JD KEYWORDS — incorporate naturally where truthful (do NOT add if not applicable):\n{priority_keywords}\n\n"
             "CANDIDATE EXPERIENCES:\n{experiences}\n\n"
             "CANDIDATE PROJECTS (with style variants, pre-scored by job relevance + project depth):\n{projects}\n\n"
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
            # Guarantee projects stay in the pre-ranked (descending-score) order even
            # if the LLM reorders or drops them in its JSON output (issue #47).
            if isinstance(tailored, dict) and tailored.get("projects"):
                tailored["projects"] = self._order_projects_by_selection(
                    tailored["projects"], [p["name"] for p in state["projects"]]
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

        # Retry threshold stays priority-keyword based: full-JD coverage of a
        # sections-only output is structurally low and would always trigger retries.
        tailored_str = ATSScoringEngine.flatten_tailored_text(state["tailored_content"]).lower()
        priority_keywords = state.get("priority_keywords", [])
        kw_hits = sum(1 for kw in priority_keywords if kw in tailored_str)
        kw_coverage = kw_hits / len(priority_keywords) if priority_keywords else 1.0
        kw_gaps = [kw for kw in priority_keywords if kw not in tailored_str]
        if not kw_gaps:
            kw_gaps = breakdown["keyword_coverage"]["missing_keywords"]

        evaluation = {
            "coverage_pct": round(skill_coverage_pct, 1),
            "covered": skill["covered"],
            "total": skill["total"],
            "gaps": skill["gaps"],
            "kw_coverage": round(kw_coverage * 100, 1),
            "kw_gaps": kw_gaps[:10],
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
            f"priority keywords {kw_coverage * 100:.1f}% ({kw_hits}/{len(priority_keywords)}), "
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
        Deterministic guarantee behind the prompt's bullet_budget rule: reorder
        the LLM's experiences to the pre-ranked relevance order and truncate any
        that exceed their budget. Experiences the LLM renamed keep their bullets
        and sort after the recognized ones, in original relative order.
        """
        def key(e: Dict) -> tuple:
            return ((e.get("title") or "").strip().lower(),
                    (e.get("company") or "").strip().lower())

        rank = {key(e): i for i, e in enumerate(budgeted)}
        budgets = {key(e): e.get("bullet_budget") for e in budgeted}
        fallback = len(budgeted)

        out = []
        for e in sorted(generated or [], key=lambda e: rank.get(key(e), fallback)):
            budget = budgets.get(key(e))
            bullets = e.get("bullets") or []
            if budget and len(bullets) > budget:
                e = {**e, "bullets": bullets[:budget]}
            out.append(e)
        return out

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
    ) -> List[str]:
        """Pinned sections first, then reorderable sections by relevance score."""
        scores = {
            key: cls._score_section_relevance(key, tailored_content, matched_skills, jd_text)
            for key in REORDERABLE_SECTIONS
        }
        # Stable sort: ties keep the default section order
        ranked = sorted(REORDERABLE_SECTIONS, key=lambda k: scores[k], reverse=True)
        logger.debug("Section relevance: %s", scores)
        return PINNED_SECTIONS + ranked

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
