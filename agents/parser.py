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
            ("user", "Text:\n{text}\n\nReturn a JSON list of objects with keys: title, company, start_date (YYYY-MM), end_date (YYYY-MM or Present), description (summary), bullets (list of strings).")
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
            ("user", "Text:\n{text}\n\nReturn a JSON list with keys: name, description, start_date, end_date. If a URL is found, include 'repo_url'.")
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
            for item in data:
                title = item.get("title", "Unknown")
                company = item.get("company", "Unknown")

                # Dedup by title + company for this user
                existing = session.exec(
                    select(Experience).where(
                        Experience.user_id == self.user.user_id,
                        Experience.title == title,
                        Experience.company == company,
                    )
                ).first()
                if existing:
                    logger.debug(f"Skipping duplicate experience: {title} at {company}")
                    continue

                exp = Experience(
                    user_id=self.user.user_id,
                    title=title,
                    company=company,
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    description=item.get("description"),
                    bullets=item.get("bullets", [])
                )
                session.add(exp)
            session.commit()

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
            for item in data:
                name = item.get("name", "Unknown")
                metrics = metrics_by_name.get(name.lower(), {})

                # Dedup by project name for this user
                existing = session.exec(
                    select(Project).where(
                        Project.user_id == self.user.user_id,
                        Project.name == name,
                    )
                ).first()
                if existing:
                    if metrics:
                        # Re-ingest: refresh GitHub signals on the existing row
                        existing.metrics = metrics
                        session.add(existing)
                    logger.debug(f"Skipping duplicate project: {name}")
                    continue

                proj = Project(
                    user_id=self.user.user_id,
                    name=name,
                    description=item.get("description"),
                    repo_url=item.get("repo_url"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    metrics=metrics,
                )
                session.add(proj)
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
