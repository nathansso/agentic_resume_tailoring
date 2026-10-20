import logging
import json
from typing import Dict, Any, List
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate

from llm import get_llm, get_extractor
from agents.extraction_schemas import (
    ExperienceList, EducationList, AchievementList, ProjectList, SkillList,
)
from database.clock import utc_now
from database.db import engine, next_seq
from database.models import User, Skill, UserSkill, Education, Experience, Project, Achievement, DeletedEntry
from database.user_utils import require_active_user
from agents.skill_postprocessor import postprocess_skills, normalize_skill_name
from agents import linkedin_mapping
# The save, merge, heal and match logic is model-free and lives in agents/kg_store.py
# so the harness can reuse it (#192); these names stay importable from here.
from agents.kg_store import (  # noqa: F401
    KGStoreMixin, _PLACEHOLDER_DATE_TOKENS, _PLACEHOLDER_NAMES, _clean_date,
)

logger = logging.getLogger(__name__)


class ResumeParserAgent(KGStoreMixin):
    def __init__(self):
        self.llm = get_llm(role="extract", temperature=0.0)
        # Fails closed: parsed resume data is written under this user, so an
        # unbound caller must error rather than pick someone else (issue #131).
        self.user = require_active_user()

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
            self._heal_skills(session, self.user.user_id)
            session.commit()

        logger.info("Parsing complete and saved to DB.")

    # The Bright Data mapping is model-free and shared with the harness's ingest_linkedin
    # (#355); these names stay importable from here.
    _coerce_records = staticmethod(linkedin_mapping.coerce_records)
    _flatten_linkedin_experiences = staticmethod(linkedin_mapping.flatten_experiences)
    _linkedin_bullets = staticmethod(linkedin_mapping.experience_bullets)

    def _extract_experiences(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract work experiences from the text."),
            ("user", "Text:\n{text}\n\nExtract each work experience with its title, company, "
             "start_date (YYYY-MM), end_date (YYYY-MM or Present), a short description, and its bullets. "
             "If a bullet references a URL (e.g. an embedded demo or repo link), preserve it verbatim as markdown `[text](url)` inside the bullet string — never drop it.")
        ])
        extractor = get_extractor(schema=ExperienceList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.experiences]
        except Exception as e:
            logger.error(f"Experience extraction failed: {e}")
            return []

    def _extract_education(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract education entries from the text."),
            ("user", "Text:\n{text}\n\nExtract each education entry: institution, degree (full degree name including major/minor), location, start_date, end_date (graduation date, e.g. 'June 2025' or 'Expected June 2027'), and gpa (or null if not stated). Only include entries explicitly present in the text — never invent one.")
        ])
        extractor = get_extractor(schema=EducationList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.education]
        except Exception as e:
            logger.error(f"Education extraction failed: {e}")
            return []

    def _extract_achievements(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract achievements, honors, and awards from the text."),
            ("user", "Text:\n{text}\n\nExtract each achievement: title (the award/honor name), "
             "description (any supporting detail, or null), issuer (awarding organization or publication, or null), "
             "date (year or date awarded, or null). Only include entries explicitly present in the text — never invent one. "
             "Do not include work experience, education degrees, or projects here.")
        ])
        extractor = get_extractor(schema=AchievementList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.achievements]
        except Exception as e:
            logger.error(f"Achievement extraction failed: {e}")
            return []

    def _extract_projects(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract projects."),
            ("user", "Text:\n{text}\n\nExtract each project: name, description, start_date, end_date. "
             "If a source-code/repository URL is found, set 'repo_url'. "
             "If a separate live/demo URL is found (distinct from the repo link), set 'demo_url'. "
             "Preserve any other URL referenced in the description verbatim as markdown `[text](url)` — never drop it.")
        ])
        extractor = get_extractor(schema=ProjectList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.projects]
        except Exception as e:
            logger.error(f"Project extraction failed: {e}")
            return []

    def _extract_skills(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract technical skills, tools, and languages."),
            ("user", "Text:\n{text}\n\nExtract each skill: name, category (e.g. Language, Framework, Tool), proficiency (1-5 estimate based on context).")
        ])
        extractor = get_extractor(schema=SkillList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.skills]
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
             "Extract each skill. Each must have:\n"
             "- name: the specific skill/library/tool name (e.g. 'XGBoost', 'Feature Engineering', 'pandas')\n"
             "- category: one of 'Language', 'Library', 'Framework', 'Tool', 'Technique', 'Database', 'Cloud'\n"
             "- proficiency: 1-5 estimate (3 if used in a project, 4 if used extensively)")
        ])
        extractor = get_extractor(schema=SkillList, llm=self.llm)
        try:
            result = extractor.invoke(prompt.format_messages(text=text))
            return [item.model_dump() for item in result.skills]
        except Exception as e:
            logger.error(f"Repo skill extraction failed: {e}")
            return []

    # ── Deterministic LinkedIn mapping (issue #68 follow-up) ─────────────────
    # Bright Data returns projects/experiences as structured records; the shared
    # mapping (agents/linkedin_mapping.py) turns them into entities, saved here
    # verbatim and merged into rows already ingested from other sources.

    def _save_linkedin_structured(self, record: Dict[str, Any]) -> None:
        mapped = linkedin_mapping.map_record(record)

        with Session(engine) as session:
            uid = self.user.user_id
            proj_tombs = self._load_tombstones(session, uid, "project")
            exp_tombs = self._load_tombstones(session, uid, "experience")
            edu_tombs = self._load_tombstones(session, uid, "education")
            existing_projects = list(session.exec(
                select(Project).where(Project.user_id == self.user.user_id)
            ).all())
            for item in mapped["projects"]:
                name = item["name"]
                item["start_date"] = _clean_date(item.get("start_date"))
                item["end_date"] = _clean_date(item.get("end_date"))
                match = next(
                    (p for p in existing_projects
                     if self._projects_match(p.name, p.repo_url, name, item.get("repo_url"))),
                    None,
                )
                if match:
                    if match.manually_edited:  # user-edited row is authoritative (issue #92)
                        continue
                    if self._enrich(match, item, ["description", "start_date", "end_date"]):
                        session.add(match)
                    logger.debug(f"Merged LinkedIn project into: {match.name}")
                    continue
                if self._project_tombstoned(proj_tombs, name, item.get("repo_url")):
                    logger.debug(f"Skipping tombstoned LinkedIn project: {name}")
                    continue
                proj = Project(
                    user_id=self.user.user_id,
                    name=name,
                    description=item.get("description"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    seq=next_seq(session, Project, self.user.user_id),
                )
                session.add(proj)
                existing_projects.append(proj)

            existing_exps = list(session.exec(
                select(Experience).where(Experience.user_id == self.user.user_id)
            ).all())
            # The mapping flattens multi-role employers so each nested position
            # becomes its own row instead of being dropped (issue #96).
            for item in mapped["experiences"]:
                title = item.get("title", "")
                company = item.get("company", "")
                item["start_date"] = _clean_date(item.get("start_date"))
                item["end_date"] = _clean_date(item.get("end_date"))
                bullets = item.get("bullets", [])
                # Same company + same title merges; a missing/placeholder title
                # on either side still merges on company alone, so a sparse
                # LinkedIn entry enriches the resume-ingested row.
                match = next(
                    (e for e in existing_exps
                     if self._experiences_match(e.title, e.company, title, company)),
                    None,
                )
                if match:
                    if match.manually_edited:  # user-edited row is authoritative (issue #92)
                        continue
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
                        match.updated_at = utc_now()
                        session.add(match)
                    logger.debug(f"Merged LinkedIn experience into: {match.title} @ {match.company}")
                    continue
                if self._experience_tombstoned(exp_tombs, title, company):
                    logger.debug(f"Skipping tombstoned LinkedIn experience: {title} @ {company}")
                    continue
                # Don't auto-add an essentially empty stub (issue #85) — e.g. a
                # company grouping with no role title, dates, or detail.
                if not self._experience_is_includable(
                    title, company, item.get("start_date"), item.get("end_date"),
                    item.get("description"), bullets,
                ):
                    logger.debug(f"Skipping content-empty LinkedIn experience: {title} @ {company}")
                    continue
                exp = Experience(
                    user_id=self.user.user_id,
                    title=title or "Unknown Position",
                    company=company or "Unknown",
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    description=item.get("description"),
                    bullets=bullets,
                    seq=next_seq(session, Experience, self.user.user_id),
                )
                session.add(exp)
                existing_exps.append(exp)

            # Education (issue #73): Bright Data returns title (school), degree,
            # field, and sometimes start_year/end_year. Merge on institution so a
            # resume-ingested row is not duplicated by a sparser LinkedIn one.
            existing_edu = list(session.exec(
                select(Education).where(Education.user_id == self.user.user_id)
            ).all())
            for item in mapped["education"]:
                institution = item["institution"]
                degree = item.get("degree", "")
                # Match on institution + degree so a second degree at the same
                # school (M.S. after B.S.) is not dropped as a duplicate.
                if any(self._education_match(e.institution, e.degree, institution, degree)
                       for e in existing_edu):
                    logger.debug(f"Skipping LinkedIn education already present: {degree} at {institution}")
                    continue
                if self._education_tombstoned(edu_tombs, institution, degree):
                    logger.debug(f"Skipping tombstoned LinkedIn education: {degree} at {institution}")
                    continue
                edu = Education(
                    user_id=self.user.user_id,
                    institution=institution,
                    degree=degree,
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    seq=next_seq(session, Education, self.user.user_id),
                )
                session.add(edu)
                existing_edu.append(edu)

            # Achievements (honors_and_awards): Bright Data returns title,
            # publication (the issuer), date, and description. Merge on title so a
            # resume-ingested achievement is not duplicated by the LinkedIn one.
            existing_ach = list(session.exec(
                select(Achievement).where(Achievement.user_id == self.user.user_id)
            ).all())
            for item in mapped["achievements"]:
                title = item["title"]
                issuer = item.get("issuer")
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
                    description=item.get("description"),
                    issuer=issuer,
                    date=item.get("date"),
                    seq=next_seq(session, Achievement, self.user.user_id),
                )
                session.add(ach)
                existing_ach.append(ach)
            session.commit()
