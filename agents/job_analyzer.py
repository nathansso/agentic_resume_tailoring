import logging
from typing import Dict, Any, List
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from llm import get_llm
from database.db import engine
from database.models import JobDescription, JobSkill, Skill

logger = logging.getLogger(__name__)


class JobAnalyzerAgent:
    """
    Analyzes a raw job description and extracts structured data:
    - Job metadata (title, company)
    - Required and preferred skills with weights
    """

    def __init__(self):
        self.llm = get_llm(role="extract", temperature=0.0)

    def analyze_and_save(self, job_data: Dict[str, Any]) -> JobDescription:
        """
        Takes raw job ingestion data, extracts structured info via LLM,
        and saves JobDescription + JobSkill records to the DB.
        """
        raw_text = job_data.get("raw_text", "")
        source = job_data.get("source", "unknown")

        logger.info(f"Analyzing job description from {source}...")

        # 1. Extract job metadata
        metadata = self._extract_metadata(raw_text)

        # 2. Extract required/preferred skills
        skills = self._extract_skills(raw_text)

        # 3. Save to DB
        job = self._save_job(metadata, skills, raw_text, source)

        logger.info(f"Job analysis complete: {metadata.get('title', 'Unknown')} at {metadata.get('company', 'Unknown')}")
        return job

    def _extract_metadata(self, text: str) -> Dict[str, str]:
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are a job description parser. Extract the job title and company name from the text. Respond with ONLY valid JSON, no extra text."),
            ("user", 'Text:\n{text}\n\nReturn JSON: {{"title": "...", "company": "..."}}')
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            return chain.invoke({"text": text})
        except Exception as e:
            logger.error(f"Metadata extraction failed: {e}")
            return {"title": "Unknown", "company": "Unknown"}

    def _extract_skills(self, text: str) -> List[Dict]:
        prompt = ChatPromptTemplate.from_messages([
            ("system",
             "You are an expert job description analyzer. Extract all technical skills, tools, "
             "languages, and frameworks mentioned in the job posting. Classify each as required "
             "or preferred. Assign a weight from 0.1 to 1.0 based on how prominently it appears. "
             "Respond with ONLY valid JSON, no extra text."),
            ("user",
             "Job Description:\n{text}\n\n"
             'Return a JSON list: [{{"name": "Python", "category": "Language", "required": true, "weight": 0.9}}, ...]')
        ])
        chain = prompt | self.llm | JsonOutputParser()
        try:
            result = chain.invoke({"text": text})
            if isinstance(result, list):
                return result
            return []
        except Exception as e:
            logger.error(f"Skill extraction failed: {e}")
            return []

    def _save_job(self, metadata: Dict, skills: List[Dict], raw_text: str, source: str) -> JobDescription:
        with Session(engine) as session:
            job = JobDescription(
                title=metadata.get("title", "Unknown"),
                company=metadata.get("company", "Unknown"),
                description=raw_text,
                source_url=source if source.startswith("http") else None,
            )
            session.add(job)
            session.commit()
            session.refresh(job)

            for item in skills:
                skill_name = item.get("name", "").strip()
                if not skill_name:
                    continue

                # Get or create the Skill record
                skill = session.exec(select(Skill).where(Skill.name == skill_name)).first()
                if not skill:
                    skill = Skill(name=skill_name, category=item.get("category"))
                    session.add(skill)
                    session.commit()
                    session.refresh(skill)

                job_skill = JobSkill(
                    job_id=job.job_id,
                    skill_id=skill.skill_id,
                    required=item.get("required", True),
                    weight=item.get("weight", 1.0),
                )
                session.add(job_skill)

            session.commit()
            session.refresh(job)
            return job
