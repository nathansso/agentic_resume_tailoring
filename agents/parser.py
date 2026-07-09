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
from database.models import User, Skill, UserSkill, Education, Experience, Project
from database.user_utils import get_or_create_default_user
from agents.skill_postprocessor import postprocess_skills, normalize_skill_name

logger = logging.getLogger(__name__)

# Date strings the extractor emits when a real date is absent — coerced to None
# at save time so the knowledge graph never stores "Not specified" as a date
# (issue #72). Mirrors the tailor/formatter placeholder sets.
_PLACEHOLDER_DATE_TOKENS = {
    "", "not specified", "unknown", "unspecified", "n/a", "na", "none", "tbd", "-",
}


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

        # Self-heal (issue #72): merge pre-existing fuzzy-duplicate experience/
        # project rows and coerce placeholder dates, so any junk from earlier
        # ingests is cleaned up without the user having to re-ingest from scratch.
        with Session(engine) as session:
            self._heal_experiences(session, self.user.user_id)
            self._heal_projects(session, self.user.user_id)
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
                # instead of creating a second, sparser row. Enrich the existing
                # row with anything it is missing rather than dropping the new data.
                match = next(
                    (e for e in existing_exps
                     if self._names_match(e.title, title)
                     and self._names_match(e.company, company)),
                    None,
                )
                if match:
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

    @staticmethod
    def _exp_row_richness(e) -> tuple:
        """Prefer the row with more bullets, then real dates, then description."""
        return (len(e.bullets or []),
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
                 if cls._names_match(k.title, e.title)
                 and cls._names_match(k.company, e.company)),
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
            match = next((k for k in kept if cls._names_match(k.name, p.name)), None)
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

    def _save_education(self, data: List[Dict], source: str):
        with Session(engine) as session:
            for item in data:
                institution = str(item.get("institution") or "").strip()
                degree = str(item.get("degree") or "").strip()
                if not institution:
                    continue

                # Dedup by institution + degree for this user
                existing = session.exec(
                    select(Education).where(
                        Education.user_id == self.user.user_id,
                        Education.institution == institution,
                        Education.degree == degree,
                    )
                ).first()
                if existing:
                    logger.debug(f"Skipping duplicate education: {degree} at {institution}")
                    continue

                gpa = item.get("gpa")
                session.add(Education(
                    user_id=self.user.user_id,
                    institution=institution,
                    degree=degree,
                    location=item.get("location"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    gpa=str(gpa).strip() if gpa else None,
                ))
            session.commit()

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
                # '…Prediction (Stacked…)' instead of duplicating.
                match = next(
                    (p for p in existing_projects if self._names_match(p.name, name)),
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
                    (p for p in existing_projects if self._names_match(p.name, name)),
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
            placeholders = {"", "unknown", "unknown position"}
            for item in experiences:
                title = str(item.get("title") or "").strip()
                company = str(item.get("company") or "").strip()
                if not title and not company:
                    continue
                item["start_date"] = _clean_date(item.get("start_date"))
                item["end_date"] = _clean_date(item.get("end_date"))
                # Same company + same title merges; a missing/placeholder title
                # on either side still merges on company alone, so a sparse
                # LinkedIn entry enriches the resume-ingested row.
                match = next(
                    (e for e in existing_exps
                     if company and self._names_match(e.company, company)
                     and (self._names_match(e.title, title)
                          or self._norm_name(title) in placeholders
                          or self._norm_name(e.title) in placeholders)),
                    None,
                )
                if match:
                    if self._norm_name(match.title) in placeholders and title:
                        match.title = title
                        match.updated_at = datetime.utcnow()
                        session.add(match)
                    if self._enrich(match, item, ["description", "start_date", "end_date"]):
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
                if any(self._names_match(e.institution, institution) for e in existing_edu):
                    logger.debug(f"Skipping LinkedIn education already present: {institution}")
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
