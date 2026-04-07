import logging
from typing import Dict, List, Tuple
from uuid import UUID
from sqlmodel import Session, select

from database.db import engine
from database.models import (
    User, Skill, UserSkill, JobDescription, JobSkill, UserJobResult,
    Experience, Project,
)
from knowledge_graph.builder import SkillGraphBuilder

logger = logging.getLogger(__name__)


class SkillMatcherAgent:
    """
    Compares a user's skill profile against a job description's requirements.
    Uses both direct matching and knowledge-graph-based indirect matching
    to produce an ATS-style score and detailed skill breakdown.
    """

    def __init__(self):
        self.graph_builder = SkillGraphBuilder()

    def match(self, user_id: UUID, job_id: UUID) -> UserJobResult:
        """
        Runs skill matching and saves a UserJobResult to the DB.
        """
        logger.info(f"Matching user {user_id} against job {job_id}...")

        with Session(engine) as session:
            # Load user skills
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == user_id)
            ).all()
            user_skill_ids = {us.skill_id for us in user_skills}

            # Load user skill names for matching
            user_skill_names = set()
            for us in user_skills:
                skill = session.exec(select(Skill).where(Skill.skill_id == us.skill_id)).first()
                if skill:
                    user_skill_names.add(skill.name.lower().strip())

            # Load job skills
            job_skills = session.exec(
                select(JobSkill).where(JobSkill.job_id == job_id)
            ).all()

            matched_skills = {}
            missing_skills = []
            total_weight = 0.0
            matched_weight = 0.0

            for js in job_skills:
                skill = session.exec(select(Skill).where(Skill.skill_id == js.skill_id)).first()
                if not skill:
                    continue

                skill_name = skill.name
                total_weight += js.weight

                # Direct match
                if skill.skill_id in user_skill_ids:
                    matched_skills[skill_name] = {
                        "match_type": "direct",
                        "required": js.required,
                        "weight": js.weight,
                    }
                    matched_weight += js.weight
                    continue

                # Fuzzy name match (case-insensitive)
                if skill_name.lower().strip() in user_skill_names:
                    matched_skills[skill_name] = {
                        "match_type": "name_match",
                        "required": js.required,
                        "weight": js.weight,
                    }
                    matched_weight += js.weight
                    continue

                # Indirect match via knowledge graph
                indirect = self._check_indirect_match(skill_name, user_skill_names)
                if indirect:
                    matched_skills[skill_name] = {
                        "match_type": "indirect",
                        "via": indirect,
                        "required": js.required,
                        "weight": js.weight * 0.5,  # Partial credit for indirect
                    }
                    matched_weight += js.weight * 0.5
                    continue

                missing_skills.append(skill_name)

            # Calculate score
            ats_score = (matched_weight / total_weight * 100) if total_weight > 0 else 0.0

            # Save result
            result = UserJobResult(
                user_id=user_id,
                job_id=job_id,
                ats_score=round(ats_score, 1),
                matched_skills=matched_skills,
                missing_skills=missing_skills,
            )
            session.add(result)
            session.commit()
            session.refresh(result)

            logger.info(f"Match complete — ATS Score: {result.ats_score}%, "
                        f"Matched: {len(matched_skills)}, Missing: {len(missing_skills)}")
            return result

    def _check_indirect_match(self, job_skill_name: str, user_skill_names: set) -> str:
        """
        Check if the user has related skills via the knowledge graph.
        E.g., user knows 'React' → project uses 'TypeScript' → job wants 'TypeScript'.
        Returns the connecting skill name if found, else empty string.
        """
        try:
            graph = self.graph_builder.build_graph()

            # Find projects that use this skill
            projects = self.graph_builder.get_projects_using_skill(job_skill_name)
            for project_name in projects:
                # Get all skills for that project
                project_skills = self.graph_builder.get_skills_for_project(project_name)
                for ps in project_skills:
                    if ps.lower().strip() in user_skill_names:
                        return f"{ps} (via {project_name})"
        except Exception as e:
            logger.debug(f"Indirect match check failed: {e}")

        return ""
