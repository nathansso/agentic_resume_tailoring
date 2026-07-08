import logging
import json
from typing import List
from sqlmodel import Session, select
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser

from llm import get_llm
from database.db import engine
from database.models import Project, ProjectBlurb

logger = logging.getLogger(__name__)

class ProjectEnhancerAgent:
    def __init__(self):
        self.llm = get_llm(role="chat", temperature=0.7)

    def enhance_all_projects(self):
        """
        Iterates through all projects in the DB and generates variations if they don't exist.
        """
        logger.info("Starting Project Enhancement...")
        with Session(engine) as session:
            projects = session.exec(select(Project)).all()
            
            for proj in projects:
                logger.info(f"Enhancing project: {proj.name}")
                self._generate_variations(proj, session)
        
        logger.info("Project Enhancement complete.")

    def _generate_variations(self, project: Project, session: Session):
        # Check if variations already exist
        existing = session.exec(select(ProjectBlurb).where(ProjectBlurb.project_id == project.project_id)).all()
        if existing:
            logger.info(f"Skipping {project.name}, already has {len(existing)} blurbs.")
            return

        # Generate variations
        styles = ["concise", "detailed", "metrics_heavy", "technical"]
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", "You are an expert resume writer. Rewrite the project description in the requested style. "
             "Preserve any markdown links `[text](url)` or URLs present in the original description verbatim — never strip or invent one."),
            ("user", "Project: {name}\nOriginal Description: {desc}\n\nStyle: {style}\n\nOutput ONLY the rewritten text.")
        ])
        
        chain = prompt | self.llm

        for style in styles:
            try:
                # Basic description fallback if empty
                desc = project.description or "No description provided."
                
                result = chain.invoke({"name": project.name, "desc": desc, "style": style})
                content = result.content.strip()

                blurb = ProjectBlurb(
                    project_id=project.project_id,
                    style=style,
                    content=content
                )
                session.add(blurb)
            except Exception as e:
                logger.error(f"Failed to generate {style} for {project.name}: {e}")
        
        session.commit()
