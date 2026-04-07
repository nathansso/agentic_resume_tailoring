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

logger = logging.getLogger(__name__)

class ResumeParserAgent:
    def __init__(self):
        self.llm = get_llm(temperature=0.0)
        self.user = get_or_create_default_user()

    def parse_and_save(self, ingestion_data: Dict[str, Any]):
        """
        Orchestrates the parsing of raw ingestion data into DB entities.
        """
        raw_text = ingestion_data.get("full_text", "")
        source_file = ingestion_data.get("source_file", "unknown")
        
        logger.info(f"Parsing resume content from {source_file}...")

        # 1. Extract Experiences
        experiences = self._extract_experiences(raw_text)
        self._save_experiences(experiences, source_file)

        # 2. Extract Projects
        projects = self._extract_projects(raw_text)
        self._save_projects(projects, source_file)

        # 3. Extract Skills
        skills = self._extract_skills(raw_text)
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

    def _save_experiences(self, data: List[Dict], source: str):
        with Session(engine) as session:
            for item in data:
                # Check for duplicate? (Simple check by company/title)
                # For now just insert
                exp = Experience(
                    user_id=self.user.user_id,
                    title=item.get("title", "Unknown"),
                    company=item.get("company", "Unknown"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    description=item.get("description"),
                    bullets=item.get("bullets", [])
                )
                session.add(exp)
            session.commit()

    def _save_projects(self, data: List[Dict], source: str):
        with Session(engine) as session:
            for item in data:
                proj = Project(
                    user_id=self.user.user_id,
                    name=item.get("name", "Unknown"),
                    description=item.get("description"),
                    repo_url=item.get("repo_url"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date")
                )
                session.add(proj)
            session.commit()

    def _save_skills(self, data: List[Dict], source: str):
        with Session(engine) as session:
            for item in data:
                # Normalize name
                raw_name = item.get("name", "").strip()
                if not raw_name: continue
                
                # Check if Skill exists or create
                skill = session.exec(select(Skill).where(Skill.name == raw_name)).first()
                if not skill:
                    skill = Skill(name=raw_name, category=item.get("category"))
                    session.add(skill)
                    session.commit()
                    session.refresh(skill)
                
                # Link to User
                # associated with specific evidence if you extracted that, but for now generic link
                link = UserSkill(
                    user_id=self.user.user_id,
                    skill_id=skill.skill_id,
                    proficiency=item.get("proficiency", 1),
                    evidence_source=source
                )
                session.add(link)
            session.commit()
