import logging
import json
import re
from datetime import datetime
from typing import Dict, Any, List
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from llm import get_llm
from database.db import engine
from database.models import User, Skill, UserSkill, Education, Experience, Project, Achievement
from database.user_utils import get_or_create_default_user
from agents.skill_postprocessor import postprocess_skills, normalize_skill_name
from institution import canonicalize_institution

logger = logging.getLogger(__name__)

# Date strings the extractor emits when a real date is absent — coerced to None
# at save time so the knowledge graph never stores "Not specified" as a date
# (issue #72). Mirrors the tailor/formatter placeholder sets.
_PLACEHOLDER_DATE_TOKENS = {
    "", "not specified", "unknown", "unspecified", "n/a", "na", "none", "tbd", "-", "?",
}

# Sentinel title/company/name values the extractor emits when a real value is
# absent. Treated as wildcards during dedup so a placeholder-titled row folds
# into the real one instead of surviving as a duplicate (issue #72 follow-up).
_PLACEHOLDER_NAMES = {"", "unknown", "unknown position", "n/a", "na", "none", "?"}


def _clean_date(value):
    """Coerce a placeholder date string to None; pass real dates through."""
    v = str(value or "").strip()
    return None if v.lower() in _PLACEHOLDER_DATE_TOKENS else v


class ResumeParserAgent:
    def __init__(self):
        self.llm = get_llm(role="extract", temperature=0.0)
        self.user = get_or_create_default_user()

    def parse_and_save(self, ingestion_data: Dict[str, Any]):
        """
        Orchestrates the parsing of raw ingestion data into DB entities.
        """
        raw_text = ingestion_data.get("full_text", "")
        source_file = ingestion_data.get("source_file", "unknown")
        
        logger.info(f"Parsing resume content from {source_file}...")

        is_github = source_file.startswith("github:")
        linkedin_record = ingestion_data.get("linkedin_record")

        if linkedin_record:
            # Bright Data already returns structured entities — map them
            # directly instead of a lossy text → LLM → structure round trip.
            self._save_linkedin_structured(linkedin_record)
        else:
            # Skip experience extraction for non-resume sources (e.g. GitHub)
            if not is_github:
                # 1. Extract Experiences
                experiences = self._extract_experiences(raw_text)
                self._save_experiences(experiences, source_file)

                # 1b. Extract Education (issue #73 — previously hardcoded in the formatter)
                education = self._extract_education(raw_text)
                self._save_education(education, source_file)

                # 1c. Extract Achievements / honors / awards
                achievements = self._extract_achievements(raw_text)
                self._save_achievements(achievements, source_file)

            # 2. Extract Projects
            projects = self._extract_projects(raw_text)
            repo_metrics = ingestion_data.get("repo_metrics") or {}
            self._save_projects(projects, source_file, repo_metrics)

        # 3. Extract Skills — use specialized prompt for GitHub repos
        if is_github:
            skills = self._extract_repo_skills(raw_text)
        else:
            skills = self._extract_skills(raw_text)
        
        # Post-process: filter noise, normalize names, deduplicate
        skills = postprocess_skills(skills)
        self._save_skills(skills, source_file)

        # Self-heal (issue #72): merge pre-existing fuzzy-duplicate experience,
        # project, and education rows and coerce placeholder dates, so any junk
        # from earlier ingests is cleaned up on the next ingest without the user
        # having to re-ingest from scratch.
        with Session(engine) as session:
            self._heal_experiences(session, self.user.user_id)
            self._heal_projects(session, self.user.user_id)
            self._heal_education(session, self.user.user_id)
            self._heal_achievements(session, self.user.user_id)
            session.commit()

        logger.info("Parsing complete and saved to DB.")

    @staticmethod
    def _coerce_records(data: Any, str_key: str | None = None) -> List[Dict]:
        """Normalize LLM JSON output into a list of dicts.

        Models sometimes wrap the list in an object ({"skills": [...]}) or
        return bare strings instead of objects; both crash downstream
        .get() calls if passed through untouched.

        str_key: when set, bare-string items become {str_key: item};
        otherwise they are dropped.
        """
        if isinstance(data, dict):
            wrapped = next((v for v in data.values() if isinstance(v, list)), None)
            data = wrapped if wrapped is not None else [data]
        if not isinstance(data, list):
            return []
        records: List[Dict] = []
        for item in data:
            if isinstance(item, dict):
                records.append(item)
            elif isinstance(item, str) and str_key and item.strip():
                records.append({str_key: item.strip()})
        return records

    def _extract_experiences(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract work experiences from the text."),
            ("user", "Text:\n{text}\n\nReturn a JSON list of objects with keys: title, company, start_date (YYYY-MM), end_date (YYYY-MM or Present), description (summary), bullets (list of strings). "
             "If a bullet references a URL (e.g. an embedded demo or repo link), preserve it verbatim as markdown `[text](url)` inside the bullet string — never drop it.")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="title")
        except Exception as e:
            logger.error(f"Experience extraction failed: {e}")
            return []

    def _extract_education(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract education entries from the text."),
            ("user", "Text:\n{text}\n\nReturn a JSON list of objects with keys: institution, degree (full degree name including major/minor), location, start_date, end_date (graduation date, e.g. 'June 2025' or 'Expected June 2027'), gpa (string, or null if not stated). Only include entries explicitly present in the text — never invent one.")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="institution")
        except Exception as e:
            logger.error(f"Education extraction failed: {e}")
            return []

    def _extract_achievements(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract achievements, honors, and awards from the text."),
            ("user", "Text:\n{text}\n\nReturn a JSON list of objects with keys: title (the award/honor name), "
             "description (any supporting detail, or null), issuer (awarding organization or publication, or null), "
             "date (year or date awarded, or null). Only include entries explicitly present in the text — never invent one. "
             "Do not include work experience, education degrees, or projects here.")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="title")
        except Exception as e:
            logger.error(f"Achievement extraction failed: {e}")
            return []

    def _extract_projects(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract projects."),
            ("user", "Text:\n{text}\n\nReturn a JSON list with keys: name, description, start_date, end_date. "
             "If a source-code/repository URL is found, include 'repo_url'. "
             "If a separate live/demo URL is found (distinct from the repo link), include 'demo_url'. "
             "Preserve any other URL referenced in the description verbatim as markdown `[text](url)` — never drop it.")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="name")
        except Exception as e:
            logger.error(f"Project extraction failed: {e}")
            return []

    def _extract_skills(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract technical skills, tools, and languages."),
            ("user", "Text:\n{text}\n\nReturn a JSON list with keys: name, category (e.g. Language, Framework, Tool), proficiency (1-5 estimate based on context).")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="name")
        except Exception as e:
            logger.error(f"Skill extraction failed: {e}")
            return []

    def _extract_repo_skills(self, text: str) -> List[Dict]:
        """Specialized skill extraction for GitHub repo data.
        
        Analyzes README content, dependency files (requirements.txt, etc.),
        and project descriptions to extract specific libraries, frameworks,
        tools, and techniques actually used in code.
        """
        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an expert software engineer analyzing GitHub repositories. "
             "Your task is to extract EVERY specific technical skill, library, framework, "
             "tool, and technique used in these projects.\n\n"
             "Pay special attention to:\n"
             "- Libraries listed in requirements.txt, setup.py, pyproject.toml, package.json, etc.\n"
             "- Specific ML/AI libraries (e.g. xgboost, lightgbm, scikit-learn, pytorch, tensorflow)\n"
             "- Data processing tools (e.g. pandas, numpy, spark, dask)\n"
             "- Techniques described in READMEs (e.g. feature engineering, ensemble methods, NLP)\n"
             "- Infrastructure/DevOps tools (e.g. docker, kubernetes, AWS, GCP)\n"
             "- Databases and data stores mentioned\n"
             "- Programming languages used\n\n"
             "Extract individual libraries as separate skills, NOT grouped. "
             "For example, list 'xgboost', 'lightgbm', 'scikit-learn' separately, not 'ML libraries'."),
            ("user",
             "GitHub Repository Data:\n{text}\n\n"
             "Return a JSON list of objects. Each object must have:\n"
             "- name: the specific skill/library/tool name (e.g. 'XGBoost', 'Feature Engineering', 'pandas')\n"
             "- category: one of 'Language', 'Library', 'Framework', 'Tool', 'Technique', 'Database', 'Cloud'\n"
             "- proficiency: 1-5 estimate (3 if used in a project, 4 if used extensively)")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return self._coerce_records(chain.invoke({"text": text}), str_key="name")
        except Exception as e:
            logger.error(f"Repo skill extraction failed: {e}")
            return []

    def _save_experiences(self, data: List[Dict], source: str):
        with Session(engine) as session:
            existing_exps = list(session.exec(
                select(Experience).where(Experience.user_id == self.user.user_id)
            ).all())
            for item in data:
                title = str(item.get("title") or "").strip() or "Unknown"
                company = str(item.get("company") or "").strip() or "Unknown"
                start = _clean_date(item.get("start_date"))
                end = _clean_date(item.get("end_date"))
                bullets = item.get("bullets") or []
                desc = item.get("description")

                # Fuzzy dedup (issue #72): 'IDXExchange' merges with 'IDX Exchange'
                # instead of creating a second, sparser row. A placeholder title
                # on either side still merges on company alone, so an 'Unknown
                # Position' row folds into the real one. Enrich the existing row
                # with anything it is missing rather than dropping the new data.
                match = next(
                    (e for e in existing_exps
                     if self._experiences_match(e.title, e.company, title, company)),
                    None,
                )
                if match:
                    if self._is_placeholder_name(match.title) and not self._is_placeholder_name(title):
                        match.title = title
                        match.updated_at = datetime.utcnow()
                        session.add(match)
                    if self._merge_experience(match, start, end, desc, bullets):
                        session.add(match)
                    logger.debug(f"Merged experience into: {match.title} @ {match.company}")
                    continue

                exp = Experience(
                    user_id=self.user.user_id,
                    title=title,
                    company=company,
                    start_date=start,
                    end_date=end,
                    description=desc,
                    bullets=bullets,
                )
                session.add(exp)
                existing_exps.append(exp)
            session.commit()

    @staticmethod
    def _merge_experience(row, start, end, description, bullets) -> bool:
        """Fill a row's missing date/description/bullets from another source.
        Idempotent — re-ingesting the same data changes nothing. Returns whether
        the row was modified."""
        changed = False
        if start and not row.start_date:
            row.start_date = start; changed = True
        if end and not row.end_date:
            row.end_date = end; changed = True
        if description and not (row.description or "").strip():
            row.description = description; changed = True
        if bullets and not (row.bullets or []):
            row.bullets = bullets; changed = True
        if changed:
            row.updated_at = datetime.utcnow()
        return changed

    # ── Self-heal for existing rows (issue #72) ──────────────────────────────

    @classmethod
    def _exp_row_richness(cls, e) -> tuple:
        """Prefer a row with a real (non-placeholder) title, then more bullets,
        then real dates, then description. The title term ranks first so the
        real 'Data Science Intern' row always survives an 'Unknown Position'
        duplicate regardless of the other fields."""
        return (0 if cls._is_placeholder_name(e.title) else 1,
                len(e.bullets or []),
                bool(e.start_date) + bool(e.end_date),
                len((e.description or "").strip()))

    @classmethod
    def _heal_experiences(cls, session, user_id) -> int:
        """Coerce placeholder dates and merge fuzzy-duplicate experience rows for
        a user, keeping the richest of each group and deleting the rest. Returns
        the number of rows removed. Idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Experience).where(Experience.user_id == user_id)
            .order_by(Experience.created_at)
        ).all())
        kept: List = []
        removed = 0
        for e in rows:
            cs, ce = _clean_date(e.start_date), _clean_date(e.end_date)
            if cs != e.start_date or ce != e.end_date:
                e.start_date, e.end_date = cs, ce
                e.updated_at = datetime.utcnow()
                session.add(e)
            match = next(
                (k for k in kept
                 if cls._experiences_match(k.title, k.company, e.title, e.company)),
                None,
            )
            if match is None:
                kept.append(e)
                continue
            # Merge into the richer row; delete the poorer.
            if cls._exp_row_richness(e) > cls._exp_row_richness(match):
                cls._merge_experience(e, match.start_date, match.end_date,
                                      match.description, match.bullets)
                session.add(e)
                session.delete(match)
                kept[kept.index(match)] = e
            else:
                cls._merge_experience(match, e.start_date, e.end_date,
                                      e.description, e.bullets)
                session.add(match)
                session.delete(e)
            removed += 1
        return removed

    @classmethod
    def _heal_projects(cls, session, user_id) -> int:
        """Coerce placeholder dates and merge fuzzy-duplicate project rows for a
        user, keeping the richer of each pair. Returns rows removed."""
        rows = list(session.exec(
            select(Project).where(Project.user_id == user_id)
            .order_by(Project.created_at)
        ).all())

        def richness(p) -> tuple:
            return (len((p.description or "").strip()),
                    1 if (p.metrics or {}) else 0,
                    bool(p.repo_url) + bool(p.demo_url),
                    bool(p.start_date) + bool(p.end_date))

        kept: List = []
        removed = 0
        for p in rows:
            cs, ce = _clean_date(p.start_date), _clean_date(p.end_date)
            if cs != p.start_date or ce != p.end_date:
                p.start_date, p.end_date = cs, ce
                p.updated_at = datetime.utcnow()
                session.add(p)
            match = next(
                (k for k in kept
                 if cls._projects_match(k.name, k.repo_url, p.name, p.repo_url)),
                None,
            )
            if match is None:
                kept.append(p)
                continue
            richer, poorer = (p, match) if richness(p) > richness(match) else (match, p)
            # Backfill the survivor's blanks from the row being removed.
            for field in ("description", "repo_url", "demo_url", "start_date", "end_date"):
                if not getattr(richer, field, None) and getattr(poorer, field, None):
                    setattr(richer, field, getattr(poorer, field))
            if not (richer.metrics or {}) and (poorer.metrics or {}):
                richer.metrics = poorer.metrics
            richer.updated_at = datetime.utcnow()
            session.add(richer)
            session.delete(poorer)
            if richer is p:  # p won: replace match in kept
                kept[kept.index(match)] = p
            removed += 1
        return removed

    @classmethod
    def _heal_education(cls, session, user_id) -> int:
        """Merge fuzzy-duplicate education rows for a user, keeping the richest
        of each institution+degree group and backfilling the survivor's blanks.
        Distinct degrees at one school stay separate. Returns rows removed;
        idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Education).where(Education.user_id == user_id)
            .order_by(Education.created_at)
        ).all())

        def richness(e) -> tuple:
            return (bool(e.gpa),
                    bool(e.start_date) + bool(e.end_date),
                    bool(e.location),
                    len((e.degree or "").strip()))

        kept: List = []
        removed = 0
        for e in rows:
            match = next(
                (k for k in kept
                 if cls._education_match(k.institution, k.degree, e.institution, e.degree)),
                None,
            )
            if match is None:
                kept.append(e)
                continue
            richer, poorer = (e, match) if richness(e) > richness(match) else (match, e)
            for field in ("degree", "location", "start_date", "end_date", "gpa"):
                if not getattr(richer, field, None) and getattr(poorer, field, None):
                    setattr(richer, field, getattr(poorer, field))
            richer.updated_at = datetime.utcnow()
            session.add(richer)
            session.delete(poorer)
            if richer is e:  # e won: replace match in kept
                kept[kept.index(match)] = e
            removed += 1
        return removed

    def _save_education(self, data: List[Dict], source: str):
        with Session(engine) as session:
            existing = list(session.exec(
                select(Education).where(Education.user_id == self.user.user_id)
            ).all())
            for item in data:
                institution = str(item.get("institution") or "").strip()
                degree = str(item.get("degree") or "").strip()
                if not institution:
                    continue

                # Fuzzy dedup on institution + degree, so re-ingested rows with
                # trivially different strings merge instead of duplicating, while
                # distinct degrees at one school stay separate (issue #73 follow-up).
                match = next(
                    (e for e in existing
                     if self._education_match(e.institution, e.degree, institution, degree)),
                    None,
                )
                if match:
                    logger.debug(f"Merging duplicate education: {degree} at {institution}")
                    changed = False
                    if not match.degree and degree:
                        match.degree = degree; changed = True
                    gpa = item.get("gpa")
                    for field, val in (("location", item.get("location")),
                                       ("start_date", item.get("start_date")),
                                       ("end_date", item.get("end_date")),
                                       ("gpa", str(gpa).strip() if gpa else None)):
                        v = str(val or "").strip()
                        if v and not getattr(match, field, None):
                            setattr(match, field, v); changed = True
                    if changed:
                        match.updated_at = datetime.utcnow()
                        session.add(match)
                    continue

                gpa = item.get("gpa")
                row = Education(
                    user_id=self.user.user_id,
                    institution=institution,
                    degree=degree,
                    location=item.get("location"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    gpa=str(gpa).strip() if gpa else None,
                )
                session.add(row)
                existing.append(row)
            session.commit()

    def _save_achievements(self, data: List[Dict], source: str):
        with Session(engine) as session:
            existing = list(session.exec(
                select(Achievement).where(Achievement.user_id == self.user.user_id)
            ).all())
            for item in data:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                issuer = str(item.get("issuer") or "").strip() or None

                # Fuzzy dedup on title (+ issuer as enrichment), so a resume line
                # and its LinkedIn honors_and_awards entry fold into one row
                # instead of duplicating. Never drops content — merges blanks.
                match = next(
                    (a for a in existing
                     if self._achievements_match(a.title, a.issuer, title, issuer)),
                    None,
                )
                if match:
                    if self._merge_achievement(match, item):
                        session.add(match)
                    logger.debug(f"Merged achievement into: {match.title}")
                    continue

                row = Achievement(
                    user_id=self.user.user_id,
                    title=title,
                    description=str(item.get("description") or "").strip() or None,
                    issuer=issuer,
                    date=str(item.get("date") or "").strip() or None,
                )
                session.add(row)
                existing.append(row)
            session.commit()

    @staticmethod
    def _merge_achievement(row, item: Dict) -> bool:
        """Fill a row's missing description/issuer/date from another source.
        Idempotent — re-ingesting the same data changes nothing. Returns whether
        the row was modified."""
        changed = False
        for field in ("description", "issuer", "date"):
            val = str(item.get(field) or "").strip()
            if val and not getattr(row, field, None):
                setattr(row, field, val)
                changed = True
        if changed:
            row.updated_at = datetime.utcnow()
        return changed

    @classmethod
    def _heal_achievements(cls, session, user_id) -> int:
        """Merge fuzzy-duplicate achievement rows for a user, keeping the richest
        of each title group and backfilling the survivor's blanks. Returns rows
        removed; idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Achievement).where(Achievement.user_id == user_id)
            .order_by(Achievement.created_at)
        ).all())

        def richness(a) -> tuple:
            return (len((a.description or "").strip()),
                    bool(a.issuer),
                    bool(a.date))

        kept: List = []
        removed = 0
        for a in rows:
            match = next(
                (k for k in kept
                 if cls._achievements_match(k.title, k.issuer, a.title, a.issuer)),
                None,
            )
            if match is None:
                kept.append(a)
                continue
            richer, poorer = (a, match) if richness(a) > richness(match) else (match, a)
            for field in ("description", "issuer", "date"):
                if not getattr(richer, field, None) and getattr(poorer, field, None):
                    setattr(richer, field, getattr(poorer, field))
            richer.updated_at = datetime.utcnow()
            session.add(richer)
            session.delete(poorer)
            if richer is a:  # a won: replace match in kept
                kept[kept.index(match)] = a
            removed += 1
        return removed

    def _save_projects(self, data: List[Dict], source: str, repo_metrics: Dict[str, Dict] = None):
        # GitHub signals keyed by repo name (issue #46) — matched case-insensitively
        # against extracted project names so complexity scoring can use them later.
        metrics_by_name = {k.lower(): v for k, v in (repo_metrics or {}).items()}
        with Session(engine) as session:
            existing_projects = list(session.exec(
                select(Project).where(Project.user_id == self.user.user_id)
            ).all())
            for item in data:
                name = str(item.get("name") or "").strip() or "Unknown"
                metrics = metrics_by_name.get(name.lower(), {})

                # Fuzzy dedup (issue #72): '…Prediction(Stacked…)' merges with
                # '…Prediction (Stacked…)' instead of duplicating. A shared repo
                # URL merges a GitHub-ingested repo with its resume line even
                # when the names diverge.
                match = next(
                    (p for p in existing_projects
                     if self._projects_match(p.name, p.repo_url, name, item.get("repo_url"))),
                    None,
                )
                if match:
                    changed = False
                    if metrics:
                        match.metrics = metrics; changed = True  # refresh GitHub signals
                    for field in ("description", "repo_url", "demo_url"):
                        val = str(item.get(field) or "").strip()
                        if val and not getattr(match, field, None):
                            setattr(match, field, val); changed = True
                    for field in ("start_date", "end_date"):
                        val = _clean_date(item.get(field))
                        if val and not getattr(match, field, None):
                            setattr(match, field, val); changed = True
                    if changed:
                        session.add(match)
                    logger.debug(f"Merged project into: {match.name}")
                    continue

                proj = Project(
                    user_id=self.user.user_id,
                    name=name,
                    description=item.get("description"),
                    repo_url=item.get("repo_url"),
                    demo_url=item.get("demo_url"),
                    start_date=_clean_date(item.get("start_date")),
                    end_date=_clean_date(item.get("end_date")),
                    metrics=metrics,
                )
                session.add(proj)
                existing_projects.append(proj)
            session.commit()

    # ── Deterministic LinkedIn mapping (issue #68 follow-up) ─────────────────
    # Bright Data returns projects/experiences as structured records; save them
    # verbatim, merging into rows already ingested from other sources.

    @staticmethod
    def _norm_name(name: Any) -> str:
        name = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
        return re.sub(r"\s+", " ", name).strip()

    @classmethod
    def _names_match(cls, a: Any, b: Any) -> bool:
        """Equal after normalization, ignoring spacing ('IDXExchange' ==
        'IDX Exchange'), or containment for long names so
        'Recipe Review Analysis - Classification Model …' merges with
        'Recipe Review Analysis' instead of duplicating it."""
        na, nb = cls._norm_name(a), cls._norm_name(b)
        if not na or not nb:
            return False
        if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
            return True
        shorter, longer = sorted((na, nb), key=len)
        return len(shorter) >= 10 and shorter in longer

    @classmethod
    def _is_placeholder_name(cls, value: Any) -> bool:
        """True when a title/company/name is a sentinel like 'Unknown Position'
        or '?' that should act as a wildcard during dedup rather than a real,
        distinguishing value."""
        return cls._norm_name(value) in _PLACEHOLDER_NAMES

    @classmethod
    def _institutions_match(cls, a: Any, b: Any) -> bool:
        """Same institution/employer when their canonical keys agree — a ROR id
        when the name resolves, so 'UC San Diego' and 'University of California,
        San Diego' match (issue #95) — or, when ROR can't resolve them (most
        companies), when the raw names fuzzy-match as before."""
        ka, kb = canonicalize_institution(a), canonicalize_institution(b)
        if ka and kb and ka == kb:
            return True
        return cls._names_match(a, b)

    @classmethod
    def _experiences_match(cls, t1: Any, c1: Any, t2: Any, c2: Any) -> bool:
        """Two experiences are the same job when their companies match (canonical
        key, else fuzzy) and either their titles fuzzy-match or one side's title
        is a placeholder. Lets a sparse 'Unknown Position @ IDXExchange' row fold
        into the real 'Data Science Intern @ IDX Exchange' one, across any pair
        of sources."""
        if not cls._institutions_match(c1, c2):
            return False
        return (cls._names_match(t1, t2)
                or cls._is_placeholder_name(t1)
                or cls._is_placeholder_name(t2))

    @classmethod
    def _projects_match(cls, a_name: Any, a_repo: Any, b_name: Any, b_repo: Any) -> bool:
        """Same project when they share a repo URL — the strongest cross-source
        signal, matching a GitHub-ingested repo to its resume line even when the
        names diverge — or, failing that, when their names fuzzy-match."""
        ra = str(a_repo or "").strip().lower().rstrip("/")
        rb = str(b_repo or "").strip().lower().rstrip("/")
        if ra and rb and ra == rb:
            return True
        return cls._names_match(a_name, b_name)

    # Degree-level tokens, checked most-advanced first. Matching on level rather
    # than raw string lets an abbreviated 'BS, CS' merge with a spelled-out
    # 'B.S. Computer Science' while keeping an 'M.S.' distinct from a 'B.S.' at
    # the same school. Patterns run against the normalized (space-joined) degree,
    # so 'b\s*s' covers both 'bs' and 'b s'.
    _DEGREE_LEVEL_PATTERNS = [
        ("phd", re.compile(r"\b(ph\s*d|phd|doctor\w*|dphil)\b")),
        ("master", re.compile(r"\b(m\s*b\s*a|mba|master\w*|m\s*s|m\s*sc|m\s*a|m\s*eng)\b")),
        ("bachelor", re.compile(r"\b(bachelor\w*|b\s*s|b\s*sc|b\s*a|b\s*eng)\b")),
        ("associate", re.compile(r"\b(associate\w*|a\s*a\s*s)\b")),
    ]

    # Connective words that aren't part of a field of study, stripped before
    # comparing the field portions of two same-level degrees (issue #95).
    _DEGREE_STOPWORDS = re.compile(
        r"\b(in|of|the|and|minor|major|concentration|with|honors|degree)\b")

    @classmethod
    def _degree_level(cls, degree: Any) -> str:
        """Coarse degree level ('bachelor'/'master'/'phd'/'associate') extracted
        from a free-form degree string, or '' when none is recognized."""
        norm = cls._norm_name(degree)
        for level, pattern in cls._DEGREE_LEVEL_PATTERNS:
            if pattern.search(norm):
                return level
        return ""

    @classmethod
    def _degree_field_tokens(cls, degree: Any) -> List[str]:
        """The field-of-study tokens of a degree, with level markers ('B.S.',
        'M.S.') and connective words removed: 'B.S. Mathematics & Economics,
        Minor in Data Science' -> ['mathematics','economics','data','science']."""
        norm = cls._norm_name(degree)
        for _level, pattern in cls._DEGREE_LEVEL_PATTERNS:
            norm = pattern.sub(" ", norm)
        norm = cls._DEGREE_STOPWORDS.sub(" ", norm)
        return [t for t in norm.split() if t]

    @classmethod
    def _degrees_compatible(cls, deg_a: Any, deg_b: Any) -> bool:
        """Whether two same-level degrees name the same field. True when the
        degree/field strings fuzzy-match, or one field is an acronym of the other
        ('CS' vs 'Computer Science'). False when they name distinct fields, or one
        has no identifiable field so agreement can't be confirmed (so an MBA and
        an M.S. Data Science at one school stay separate). (issue #95)"""
        if cls._names_match(deg_a, deg_b):
            return True
        fa, fb = cls._degree_field_tokens(deg_a), cls._degree_field_tokens(deg_b)
        if not fa or not fb:
            return False
        if cls._names_match(" ".join(fa), " ".join(fb)):
            return True
        # Acronym: a single-token field vs the initials of a multi-token field.
        for short, long_toks in ((fa, fb), (fb, fa)):
            if len(short) == 1 and len(long_toks) > 1:
                if short[0] == "".join(t[0] for t in long_toks):
                    return True
        return False

    @classmethod
    def _education_match(cls, inst_a: Any, deg_a: Any, inst_b: Any, deg_b: Any) -> bool:
        """Same education entry when institutions canonicalize to the same key
        ('UC San Diego' == 'University of California, San Diego' via ROR) and the
        degrees are compatible. A blank/unknown degree on either side folds into
        the fuller one; two distinct degrees at one school stay separate — an M.S.
        and a B.S. by level, and two same-level majors (B.S. Math vs B.S. Physics)
        by field. (issue #95)"""
        if not cls._institutions_match(inst_a, inst_b):
            return False
        da, db = cls._norm_name(deg_a), cls._norm_name(deg_b)
        if not da or not db:
            return True  # a blank degree can't distinguish — merge and backfill
        la, lb = cls._degree_level(deg_a), cls._degree_level(deg_b)
        if la and lb and la != lb:
            return False  # different levels (B.S. vs M.S.) are distinct entries
        return cls._degrees_compatible(deg_a, deg_b)

    @classmethod
    def _achievements_match(cls, title_a: Any, issuer_a: Any, title_b: Any, issuer_b: Any) -> bool:
        """Same achievement when their titles fuzzy-match ('Deans List' ==
        "Dean's List"), or when one title is a substring of a longer title and
        the issuers agree — folding a resume line into its LinkedIn honors entry
        across sources. Issuer alone never matches; a distinct award keeps its
        own row."""
        if cls._names_match(title_a, title_b):
            return True
        ia, ib = cls._norm_name(issuer_a), cls._norm_name(issuer_b)
        if ia and ib and ia == ib:
            ta, tb = cls._norm_name(title_a), cls._norm_name(title_b)
            if ta and tb and (ta in tb or tb in ta):
                return True
        return False

    @staticmethod
    def _enrich(row, item: Dict, fields: List[str]) -> bool:
        """Fill missing scalar fields; append genuinely new description text.
        Idempotent: re-ingesting the same record changes nothing."""
        changed = False
        for field in fields:
            val = str(item.get(field) or "").strip()
            if val and not getattr(row, field, None):
                setattr(row, field, val)
                changed = True
        desc = str(item.get("description") or "").strip()
        if desc and row.description and desc not in row.description:
            row.description = f"{row.description}\n\n[LinkedIn] {desc}"
            changed = True
        if changed:
            row.updated_at = datetime.utcnow()
        return changed

    @classmethod
    def _flatten_linkedin_experiences(cls, experiences: List[Dict]) -> List[Dict]:
        """Expand Bright Data experience records into one dict per role (issue #96).

        Multiple roles at one employer arrive nested under a ``positions``
        sub-role array with the company on the parent record; the role's own
        title/dates/description live on each nested item. Traverse them so a
        multi-role employer yields one Experience per role instead of silently
        dropping all but the first. Single-role employers (no ``positions``)
        pass through unchanged; the parent company backfills a role that omits
        its own.
        """
        flat: List[Dict] = []
        for rec in experiences:
            if not isinstance(rec, dict):
                continue
            positions = rec.get("positions")
            if isinstance(positions, list) and positions:
                company = str(rec.get("company") or rec.get("company_name") or "").strip()
                for pos in positions:
                    if not isinstance(pos, dict):
                        continue
                    role = dict(pos)
                    if not str(role.get("company") or "").strip() and company:
                        role["company"] = company
                    flat.append(role)
            else:
                flat.append(rec)
        return flat

    @staticmethod
    def _linkedin_bullets(item: Dict) -> List[str]:
        """Bullets for a LinkedIn experience role (issue #96).

        Prefers an explicit ``bullets`` list; otherwise splits a multi-line
        ``description`` into bullet lines (stripping leading glyphs) so a role
        described as a bulleted blob isn't reduced to a content-empty stub that
        the tailor drops. A single-line description yields no bullets — it stays
        as the description rather than being shredded into one bullet.
        """
        raw = item.get("bullets")
        if isinstance(raw, list):
            out = [str(b).strip() for b in raw if str(b or "").strip()]
            if out:
                return out
        desc = str(item.get("description") or "")
        lines = [re.sub(r"^[\s•·\-\*]+", "", ln).strip() for ln in desc.splitlines()]
        lines = [ln for ln in lines if ln]
        return lines if len(lines) >= 2 else []

    def _save_linkedin_structured(self, record: Dict[str, Any]) -> None:
        projects = self._coerce_records(record.get("projects"), str_key="title")
        experiences = self._coerce_records(record.get("experience"), str_key="title")
        education = self._coerce_records(record.get("education"), str_key="title")

        with Session(engine) as session:
            existing_projects = list(session.exec(
                select(Project).where(Project.user_id == self.user.user_id)
            ).all())
            for item in projects:
                name = str(item.get("title") or item.get("name") or "").strip()
                if not name:
                    continue
                item["start_date"] = _clean_date(item.get("start_date"))
                item["end_date"] = _clean_date(item.get("end_date"))
                match = next(
                    (p for p in existing_projects
                     if self._projects_match(p.name, p.repo_url, name, item.get("repo_url"))),
                    None,
                )
                if match:
                    if self._enrich(match, item, ["description", "start_date", "end_date"]):
                        session.add(match)
                    logger.debug(f"Merged LinkedIn project into: {match.name}")
                    continue
                proj = Project(
                    user_id=self.user.user_id,
                    name=name,
                    description=item.get("description"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                )
                session.add(proj)
                existing_projects.append(proj)

            existing_exps = list(session.exec(
                select(Experience).where(Experience.user_id == self.user.user_id)
            ).all())
            # Flatten multi-role employers so each nested position becomes its
            # own row instead of being dropped (issue #96).
            for item in self._flatten_linkedin_experiences(experiences):
                title = str(item.get("title") or "").strip()
                company = str(item.get("company") or "").strip()
                if not title and not company:
                    continue
                item["start_date"] = _clean_date(item.get("start_date"))
                item["end_date"] = _clean_date(item.get("end_date"))
                bullets = self._linkedin_bullets(item)
                # Same company + same title merges; a missing/placeholder title
                # on either side still merges on company alone, so a sparse
                # LinkedIn entry enriches the resume-ingested row.
                match = next(
                    (e for e in existing_exps
                     if self._experiences_match(e.title, e.company, title, company)),
                    None,
                )
                if match:
                    touched = False
                    if self._is_placeholder_name(match.title) and title and not self._is_placeholder_name(title):
                        match.title = title
                        touched = True
                    if self._enrich(match, item, ["description", "start_date", "end_date"]):
                        touched = True
                    if bullets and not (match.bullets or []):
                        match.bullets = bullets
                        touched = True
                    if touched:
                        match.updated_at = datetime.utcnow()
                        session.add(match)
                    logger.debug(f"Merged LinkedIn experience into: {match.title} @ {match.company}")
                    continue
                exp = Experience(
                    user_id=self.user.user_id,
                    title=title or "Unknown Position",
                    company=company or "Unknown",
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    description=item.get("description"),
                    bullets=bullets,
                )
                session.add(exp)
                existing_exps.append(exp)

            # Education (issue #73): Bright Data returns title (school), degree,
            # field, and sometimes start_year/end_year. Merge on institution so a
            # resume-ingested row is not duplicated by a sparser LinkedIn one.
            existing_edu = list(session.exec(
                select(Education).where(Education.user_id == self.user.user_id)
            ).all())
            for item in education:
                institution = str(item.get("title") or item.get("institution") or "").strip()
                if not institution:
                    continue
                degree = ", ".join(
                    p for p in (
                        str(item.get("degree") or "").strip(),
                        str(item.get("field") or "").strip(),
                    ) if p
                )
                # Match on institution + degree so a second degree at the same
                # school (M.S. after B.S.) is not dropped as a duplicate.
                if any(self._education_match(e.institution, e.degree, institution, degree)
                       for e in existing_edu):
                    logger.debug(f"Skipping LinkedIn education already present: {degree} at {institution}")
                    continue
                edu = Education(
                    user_id=self.user.user_id,
                    institution=institution,
                    degree=degree,
                    start_date=str(item.get("start_year") or "").strip() or None,
                    end_date=str(item.get("end_year") or "").strip() or None,
                )
                session.add(edu)
                existing_edu.append(edu)

            # Achievements (honors_and_awards): Bright Data returns title,
            # publication (the issuer), date, and description. Merge on title so a
            # resume-ingested achievement is not duplicated by the LinkedIn one.
            achievements = self._coerce_records(
                record.get("honors_and_awards"), str_key="title")
            existing_ach = list(session.exec(
                select(Achievement).where(Achievement.user_id == self.user.user_id)
            ).all())
            for item in achievements:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                issuer = str(
                    item.get("issuer") or item.get("publication") or "").strip() or None
                match = next(
                    (a for a in existing_ach
                     if self._achievements_match(a.title, a.issuer, title, issuer)),
                    None,
                )
                if match:
                    merge_item = {
                        "description": item.get("description"),
                        "issuer": issuer,
                        "date": item.get("date"),
                    }
                    if self._merge_achievement(match, merge_item):
                        session.add(match)
                    logger.debug(f"Merged LinkedIn achievement into: {match.title}")
                    continue
                ach = Achievement(
                    user_id=self.user.user_id,
                    title=title,
                    description=str(item.get("description") or "").strip() or None,
                    issuer=issuer,
                    date=str(item.get("date") or "").strip() or None,
                )
                session.add(ach)
                existing_ach.append(ach)
            session.commit()

    def _save_skills(self, data: List[Dict], source: str):
        touched_skill_ids = set()
        with Session(engine) as session:
            for item in data:
                # Normalize name via alias map
                raw_name = normalize_skill_name(item.get("name", "").strip())
                if not raw_name: continue

                # Get or create the Skill node
                skill = session.exec(select(Skill).where(Skill.name == raw_name)).first()
                if not skill:
                    skill = Skill(name=raw_name, category=item.get("category"))
                    session.add(skill)
                    session.commit()
                    session.refresh(skill)
                touched_skill_ids.add(skill.skill_id)

                # Check if this exact user+skill+source edge already exists
                existing_link = session.exec(
                    select(UserSkill).where(
                        UserSkill.user_id == self.user.user_id,
                        UserSkill.skill_id == skill.skill_id,
                        UserSkill.evidence_source == source,
                    )
                ).first()

                if existing_link:
                    # Same source — update proficiency if higher
                    new_prof = item.get("proficiency", 1)
                    if new_prof > (existing_link.proficiency or 0):
                        existing_link.proficiency = new_prof
                        session.add(existing_link)
                    continue

                # New evidence source — create a new edge
                link = UserSkill(
                    user_id=self.user.user_id,
                    skill_id=skill.skill_id,
                    proficiency=item.get("proficiency", 1),
                    evidence_source=source
                )
                session.add(link)
            session.commit()

            # Recompute cached embeddings for skills touched by this ingest
            # (issue #54). Bounded to new/changed skills; degrades to a no-op if
            # the embedding model is unavailable.
            try:
                from agents.skill_embeddings import ensure_skill_embeddings
                ensure_skill_embeddings(session, touched_skill_ids)
            except Exception as exc:
                logger.warning("Skill embedding refresh skipped: %s", exc)
