import re
import logging
from uuid import UUID
from typing import Dict, List, Set

from sqlmodel import Session, select

from database.models import User, UserSkill, Skill, Experience, Project

logger = logging.getLogger(__name__)

# ── Stop-word list ────────────────────────────────────────────────────────────
# Ported from srbhr/resume-matcher apps/frontend/lib/utils/keyword-matcher.ts
# Includes standard English stop words + job-posting-specific filler words.

_STOP_WORDS: Set[str] = {
    # Articles / pronouns
    "a", "an", "the", "i", "me", "my", "myself", "we", "our", "ours",
    "ourselves", "you", "your", "yours", "yourself", "yourselves", "he",
    "him", "his", "himself", "she", "her", "hers", "herself", "it", "its",
    "itself", "they", "them", "their", "theirs", "themselves", "what",
    "which", "who", "whom", "this", "that", "these", "those",
    # Common verbs
    "am", "is", "are", "was", "were", "be", "been", "being", "have", "has",
    "had", "having", "do", "does", "did", "doing", "will", "would", "could",
    "should", "might", "must", "shall", "can", "need", "dare", "ought",
    "used",
    # Prepositions / conjunctions
    "and", "but", "if", "or", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "into",
    "through", "during", "before", "after", "above", "below", "to", "from",
    "up", "down", "in", "out", "on", "off", "over", "under", "again",
    "further", "then", "once", "nor", "so", "yet", "both", "either",
    "neither", "not", "only",
    # Common filler
    "here", "there", "when", "where", "why", "how", "all", "each", "every",
    "few", "more", "most", "other", "some", "such", "no", "own", "same",
    "than", "too", "very", "just", "also", "now", "etc", "within",
    # Job-posting-specific filler (high-signal suppressors)
    "role", "position", "job", "work", "working", "team", "company",
    "looking", "seeking", "required", "requirements", "responsibilities",
    "qualifications", "preferred", "experience", "years", "year", "ability",
    "skills", "knowledge", "strong", "excellent", "good", "great", "well",
    "include", "including", "includes", "may", "like", "etc", "via",
    "e.g", "i.e", "such",
}

_MIN_WORD_LEN = 3
_SPLIT_PATTERN = re.compile(r"[^a-z0-9-]+")
_PURE_NUMBER = re.compile(r"^\d+$")

# ── Seniority level detection ─────────────────────────────────────────────────

_LEVELS = [
    ("intern",   ["intern", "internship"]),
    ("junior",   ["junior", "entry level", "entry-level", "associate", " jr "]),
    ("mid",      ["mid level", "mid-level", "2+ years", "3+ years", "4+ years"]),
    ("senior",   ["senior", "sr.", "5+ years", "6+ years", "7+ years", "8+ years",
                  "9+ years", "10+ years"]),
    ("lead",     ["lead", "staff", "principal", "tech lead", "team lead"]),
    ("manager",  ["manager", "director", " vp ", "head of"]),
]

_WEIGHTS = {
    "skill_coverage":  0.45,
    "keyword_coverage": 0.30,
    "section_presence": 0.15,
    "role_level":       0.10,
}


class ATSScoringEngine:
    """
    Computes a multi-factor ATS score grounded in the resume-matcher algorithm.

    Components
    ----------
    keyword_coverage : fraction of JD keywords found in resume text (resume-matcher approach)
    section_presence : profile completeness check via DB
    role_level       : seniority alignment between JD and resume
    """

    def score(
        self,
        user_id: UUID,
        job_id: UUID,
        session: Session,
        skill_coverage_score: float,
    ) -> Dict:
        """Return full score_breakdown dict with four components."""
        from database.models import JobDescription

        job = session.get(JobDescription, job_id)
        jd_text = job.description if job else ""

        resume_text = self._build_resume_text(user_id, session)

        kd = self._keyword_coverage(resume_text, jd_text)
        sp = self._section_presence(user_id, session)
        rl = self._role_level(resume_text, jd_text)

        weights = _WEIGHTS

        components = {
            "skill_coverage":  {"score": round(skill_coverage_score, 1), **_weight_meta(weights["skill_coverage"])},
            "keyword_coverage": {**kd, **_weight_meta(weights["keyword_coverage"])},
            "section_presence": {**sp, **_weight_meta(weights["section_presence"])},
            "role_level":       {**rl, **_weight_meta(weights["role_level"])},
        }

        composite = sum(
            components[k]["score"] * weights[k] for k in weights
        )
        components["composite"] = round(composite, 1)

        return components

    @classmethod
    def score_tailored(
        cls,
        tailored_content: Dict,
        jd_text: str,
        matched_skills: Dict,
        baseline_breakdown: Dict | None = None,
    ) -> Dict:
        """
        Score agentic tailored output with the same algorithmic components as
        score(), so the tailored resume can be compared against the pre-tailor
        baseline (issue #12).

        Returns the same breakdown shape as score(), plus `baseline_composite`
        and `delta` when a baseline breakdown is supplied.
        """
        text = cls.flatten_tailored_text(tailored_content)
        haystack = text.lower()

        skill_names = list(matched_skills or {})
        covered = [s for s in skill_names if s.lower() in haystack]
        gaps = [s for s in skill_names if s.lower() not in haystack]
        skill_score = (len(covered) / len(skill_names) * 100) if skill_names else 100.0

        kd = cls._keyword_coverage(text, jd_text)
        sp = cls._tailored_section_presence(tailored_content)
        rl = cls._role_level(text, jd_text)

        components = {
            "skill_coverage": {
                "score": round(skill_score, 1),
                "covered": len(covered),
                "total": len(skill_names),
                "gaps": gaps,
                **_weight_meta(_WEIGHTS["skill_coverage"]),
            },
            "keyword_coverage": {**kd, **_weight_meta(_WEIGHTS["keyword_coverage"])},
            "section_presence": {**sp, **_weight_meta(_WEIGHTS["section_presence"])},
            "role_level":       {**rl, **_weight_meta(_WEIGHTS["role_level"])},
        }

        composite = sum(components[k]["score"] * _WEIGHTS[k] for k in _WEIGHTS)
        components["composite"] = round(composite, 1)

        baseline = (baseline_breakdown or {}).get("composite")
        if baseline is not None:
            components["baseline_composite"] = baseline
            components["delta"] = round(components["composite"] - baseline, 1)

        return components

    @staticmethod
    def flatten_section_text(tailored_content: Dict, section_key: str) -> str:
        """String fragments of one resume section ("experience"/"projects"/"skills")."""
        parts: List[str] = []
        if section_key == "experience":
            for exp in tailored_content.get("experiences") or []:
                parts.append(f"{exp.get('title', '')} at {exp.get('company', '')}")
                parts.extend(exp.get("bullets") or [])
        elif section_key == "projects":
            for proj in tailored_content.get("projects") or []:
                parts.append(proj.get("name", ""))
                parts.extend(proj.get("bullets") or [])
        elif section_key == "skills":
            # Prefer the JD-ranked/capped skill list (issue #54) so the scored
            # text matches what the formatter actually renders; fall back to the
            # legacy skills_emphasized list when no ranking was produced.
            ranked = tailored_content.get("skills_ranked")
            if ranked:
                names = [it.get("name", "") for it in ranked if it.get("name")]
                if names:
                    parts.append("Skills: " + ", ".join(names))
            else:
                skills = tailored_content.get("skills_emphasized") or []
                if skills:
                    parts.append("Skills: " + ", ".join(skills))
        return "\n".join(p for p in parts if p)

    @staticmethod
    def flatten_tailored_text(tailored_content: Dict) -> str:
        """Collect all string fragments from a tailored_resume_content dict."""
        sections = (
            ATSScoringEngine.flatten_section_text(tailored_content, key)
            for key in ("experience", "projects", "skills")
        )
        return "\n".join(s for s in sections if s)

    @staticmethod
    def _tailored_section_presence(tailored_content: Dict) -> Dict:
        """Section check for tailored output: both sections must be non-empty."""
        present: List[str] = []
        missing: List[str] = []
        for section in ("experiences", "projects"):
            (present if tailored_content.get(section) else missing).append(section)
        score = len(present) / 2 * 100
        return {"score": round(score, 1), "present": present, "missing": missing}

    # ── Sub-scorers ───────────────────────────────────────────────────────────

    @staticmethod
    def _extract_keywords(text: str) -> Set[str]:
        """Port of resume-matcher extractKeywords(). Stdlib re only."""
        keywords: Set[str] = set()
        for word in _SPLIT_PATTERN.split(text.lower()):
            if (
                len(word) >= _MIN_WORD_LEN
                and word not in _STOP_WORDS
                and not _PURE_NUMBER.match(word)
            ):
                keywords.add(word)
        return keywords

    @staticmethod
    def _keyword_coverage(resume_text: str, jd_text: str) -> Dict:
        """
        Port of resume-matcher jd_keywords_present().
        Fraction of JD keywords found via substring match in resume text.
        """
        if not jd_text.strip():
            return {"score": 0.0, "matched_keywords": [], "missing_keywords": [], "total": 0}

        jd_keywords = ATSScoringEngine._extract_keywords(jd_text)
        if not jd_keywords:
            return {"score": 100.0, "matched_keywords": [], "missing_keywords": [], "total": 0}

        haystack = resume_text.lower()
        matched = [kw for kw in jd_keywords if kw in haystack]
        missing = [kw for kw in jd_keywords if kw not in haystack]

        score = len(matched) / len(jd_keywords) * 100
        return {
            "score": round(score, 1),
            "matched_keywords": sorted(matched),
            "missing_keywords": sorted(missing),
            "total": len(jd_keywords),
        }

    @staticmethod
    def _section_presence(user_id: UUID, session: Session) -> Dict:
        """Check that required profile sections have data in the DB."""
        present: List[str] = []
        missing: List[str] = []

        has_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).first() is not None
        (present if has_skills else missing).append("skills")

        has_experience = session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).first() is not None
        (present if has_experience else missing).append("experience")

        has_projects = session.exec(
            select(Project).where(Project.user_id == user_id)
        ).first() is not None
        (present if has_projects else missing).append("projects")

        user = session.get(User, user_id)
        has_markdown = bool(user and user.resume_markdown and user.resume_markdown.strip())
        (present if has_markdown else missing).append("resume_uploaded")

        # Only required: skills + experience
        required = {"skills", "experience"}
        req_present = len([s for s in present if s in required])
        score = req_present / len(required) * 100

        return {"score": round(score, 1), "present": present, "missing": missing}

    @staticmethod
    def _role_level(resume_text: str, jd_text: str) -> Dict:
        """Detect seniority tier from both texts, penalise mismatches."""
        jd_level = _detect_level(jd_text)
        resume_level = _detect_level(resume_text)

        level_names = [name for name, _ in _LEVELS]
        jd_idx = level_names.index(jd_level)
        res_idx = level_names.index(resume_level)

        gap = abs(jd_idx - res_idx)
        score = max(0.0, 100.0 - gap * 25.0)

        return {
            "score": round(score, 1),
            "jd_level": jd_level,
            "resume_level": resume_level,
        }

    @staticmethod
    def _build_resume_text(user_id: UUID, session: Session) -> str:
        """
        Reconstruct resume text from DB records.
        Port of resume-matcher flatten_resume_text() — collects all string
        fragments from the structured profile data.
        """
        parts: List[str] = []

        user_skills = session.exec(
            select(UserSkill).where(UserSkill.user_id == user_id)
        ).all()
        skill_names: List[str] = []
        for us in user_skills:
            skill = session.exec(select(Skill).where(Skill.skill_id == us.skill_id)).first()
            if skill:
                skill_names.append(skill.name)
        if skill_names:
            parts.append("Skills: " + ", ".join(skill_names))

        experiences = session.exec(
            select(Experience).where(Experience.user_id == user_id)
        ).all()
        for exp in experiences:
            parts.append(f"{exp.title} at {exp.company}")
            if exp.description:
                parts.append(exp.description)
            for bullet in (exp.bullets or []):
                parts.append(bullet)

        projects = session.exec(
            select(Project).where(Project.user_id == user_id)
        ).all()
        for proj in projects:
            parts.append(proj.name)
            if proj.description:
                parts.append(proj.description)

        user = session.get(User, user_id)
        if user and user.resume_markdown:
            parts.append(user.resume_markdown)

        return "\n".join(parts)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_level(text: str) -> str:
    """Return the highest seniority tier whose keywords appear in text."""
    lower = text.lower()
    detected = "mid"  # default assumption
    for name, keywords in _LEVELS:
        if any(kw in lower for kw in keywords):
            detected = name
    return detected


def _weight_meta(weight: float) -> Dict:
    return {"weight": weight}
