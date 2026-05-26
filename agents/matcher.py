import logging
from typing import Dict, List, Tuple
from uuid import UUID
from sqlmodel import Session, select
import numpy as np

from database.db import engine
from database.models import (
    User, Skill, UserSkill, JobDescription, JobSkill, UserJobResult,
    Experience, Project,
)
from knowledge_graph.builder import SkillGraphBuilder
from config import EMBEDDING_MODEL

logger = logging.getLogger(__name__)

# Lazy-loaded embedding model
_embedding_model = None


def get_embedding_model():
    """Lazy-load the sentence-transformer model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading embedding model: {EMBEDDING_MODEL}")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
    return _embedding_model


class SkillMatcherAgent:
    """
    Compares a user's skill profile against a job description's requirements.
    Uses direct matching, semantic similarity, and knowledge-graph-based
    indirect matching to produce an ATS-style score and detailed skill breakdown.
    """

    SEMANTIC_THRESHOLD = 0.65  # Cosine similarity threshold for semantic match

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
            user_skill_map = {}  # lowercase -> original name
            for us in user_skills:
                skill = session.exec(select(Skill).where(Skill.skill_id == us.skill_id)).first()
                if skill:
                    user_skill_map[skill.name.lower().strip()] = skill.name
            user_skill_names = set(user_skill_map.keys())

            # Pre-compute user skill embeddings for semantic matching
            user_skill_names_list = list(user_skill_map.values())
            user_embeddings = None
            model = None
            try:
                model = get_embedding_model()
                if user_skill_names_list:
                    user_embeddings = model.encode(user_skill_names_list, normalize_embeddings=True)
            except Exception as e:
                logger.warning(f"Semantic embedding failed, falling back to exact match: {e}")

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

                # Semantic match via embeddings
                semantic_match = self._check_semantic_match(
                    skill_name, user_skill_names_list, user_embeddings, model if user_embeddings is not None else None
                )
                if semantic_match:
                    matched_skills[skill_name] = {
                        "match_type": "semantic",
                        "matched_to": semantic_match[0],
                        "similarity": round(semantic_match[1], 3),
                        "required": js.required,
                        "weight": js.weight * 0.75,  # 75% credit for semantic
                    }
                    matched_weight += js.weight * 0.75
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

    def _check_semantic_match(self, job_skill_name: str, user_skill_names: List[str],
                               user_embeddings, model) -> Tuple[str, float]:
        """
        Check if a job skill semantically matches any user skill using embeddings.
        
        Returns (matched_skill_name, similarity_score) if above threshold, else empty tuple.
        """
        if model is None or user_embeddings is None or not user_skill_names:
            return ()

        try:
            job_embedding = model.encode([job_skill_name], normalize_embeddings=True)
            # Cosine similarity (already normalized, so dot product = cosine sim)
            similarities = np.dot(user_embeddings, job_embedding.T).flatten()
            best_idx = int(np.argmax(similarities))
            best_score = float(similarities[best_idx])

            if best_score >= self.SEMANTIC_THRESHOLD:
                matched_name = user_skill_names[best_idx]
                logger.debug(f"Semantic match: '{job_skill_name}' -> '{matched_name}' ({best_score:.3f})")
                return (matched_name, best_score)
        except Exception as e:
            logger.debug(f"Semantic match check failed: {e}")

        return ()
