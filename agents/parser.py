import logging
import json
from datetime import datetime
from typing import Dict, Any, List
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from llm import get_llm
from database.db import engine
from database.models import User, Skill, UserSkill, Experience, Project
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

        # Skip experience extraction for non-resume sources (e.g. GitHub)
        is_github = source_file.startswith("github:")
        if not is_github:
            # 1. Extract Experiences
            experiences = self._extract_experiences(raw_text)
            self._save_experiences(experiences, source_file)

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

    def _extract_experiences(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract work experiences from the text."),
            ("user", "Text:\n{text}\n\nReturn a JSON list of objects with keys: title, company, start_date (YYYY-MM), end_date (YYYY-MM or Present), description (summary), bullets (list of strings).")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return chain.invoke({"text": text})
        except Exception as e:
            logger.error(f"Experience extraction failed: {e}")
            return []

    def _extract_projects(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume parser. Extract projects."),
            ("user", "Text:\n{text}\n\nReturn a JSON list with keys: name, description, start_date, end_date. If a URL is found, include 'repo_url'.")
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return chain.invoke({"text": text})
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
            return chain.invoke({"text": text})
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
            return chain.invoke({"text": text})
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

    def _save_skills(self, data: List[Dict], source: str):
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
