"""
Cached skill / job-description embeddings (issue #54, Phase 2).

Persists sentence-transformer vectors on the Skill and JobDescription rows so
the matcher and the skill scorer share one cache instead of re-encoding on every
run. Embeddings are the canonical-name vector (Skill) or a required-skill
centroid (JobDescription), tagged with the model that produced them so a model
change invalidates cleanly.

All functions degrade gracefully: if the embedding model can't load, they no-op
(return 0 / None / {}) and never raise, so ingestion and scoring keep working
without semantic signal.
"""
import json
import logging
from typing import Dict, List, Optional, Sequence
from uuid import UUID

import numpy as np
from sqlmodel import Session, select

from config import EMBEDDING_MODEL
from database.models import JobDescription, JobSkill, Skill

logger = logging.getLogger(__name__)


def _encode(texts: List[str]) -> Optional[np.ndarray]:
    """Encode texts to normalized vectors, or None if the model is unavailable."""
    if not texts:
        return None
    try:
        from agents.matcher import get_embedding_model
        model = get_embedding_model()
        return model.encode(texts, normalize_embeddings=True)
    except Exception as e:  # model missing / offline / OOM — semantic signal optional
        logger.warning("Embedding model unavailable, skipping embeddings: %s", e)
        return None


def _serialize(vec: Sequence[float]) -> str:
    return json.dumps([round(float(x), 6) for x in vec])


def deserialize(blob: Optional[str]) -> Optional[np.ndarray]:
    """JSON blob → float vector, or None when absent/corrupt."""
    if not blob:
        return None
    try:
        arr = np.asarray(json.loads(blob), dtype=float)
        return arr if arr.size else None
    except (ValueError, TypeError):
        return None


def ensure_skill_embeddings(
    session: Session, skill_ids: Optional[Sequence[UUID]] = None
) -> int:
    """
    Compute and persist embeddings for skills that are missing one or were
    embedded with a different model. Idempotent and bounded to new/changed
    skills, so it is cheap to call after every ingest. Returns the count
    (re)embedded.
    """
    stmt = select(Skill)
    if skill_ids is not None:
        ids = list(skill_ids)
        if not ids:
            return 0
        stmt = stmt.where(Skill.skill_id.in_(ids))
    skills = session.exec(stmt).all()

    stale = [s for s in skills if not s.embedding or s.embedding_model != EMBEDDING_MODEL]
    if not stale:
        return 0

    vecs = _encode([s.name for s in stale])
    if vecs is None:
        return 0

    for skill, vec in zip(stale, vecs):
        skill.embedding = _serialize(vec)
        skill.embedding_model = EMBEDDING_MODEL
        session.add(skill)
    session.commit()
    logger.info("Embedded %d skill(s)", len(stale))
    return len(stale)


def load_skill_vectors(
    session: Session, skill_ids: Sequence[UUID]
) -> Dict[UUID, np.ndarray]:
    """
    Return {skill_id: vector} for the given skills, ensuring the cache is warm
    first. Skills whose embedding is still unavailable are omitted.
    """
    if not skill_ids:
        return {}
    ensure_skill_embeddings(session, skill_ids)
    rows = session.exec(select(Skill).where(Skill.skill_id.in_(list(skill_ids)))).all()
    out: Dict[UUID, np.ndarray] = {}
    for s in rows:
        vec = deserialize(s.embedding)
        if vec is not None:
            out[s.skill_id] = vec
    return out


def ensure_job_embedding(session: Session, job: JobDescription) -> Optional[np.ndarray]:
    """
    Return the JD's cached embedding centroid, computing it from the job's
    required-skill names (falling back to the description) when missing or stale.
    """
    if job.embedding and job.embedding_model == EMBEDDING_MODEL:
        return deserialize(job.embedding)

    # Prefer the required-skill phrases over the raw blob — a tighter signal.
    job_skills = session.exec(
        select(JobSkill).where(JobSkill.job_id == job.job_id)
    ).all()
    phrases: List[str] = []
    for js in job_skills:
        sk = session.exec(select(Skill).where(Skill.skill_id == js.skill_id)).first()
        if sk:
            phrases.append(sk.name)
    if not phrases:
        phrases = [job.description[:2000]] if job.description else []
    if not phrases:
        return None

    vecs = _encode(phrases)
    if vecs is None:
        return None

    centroid = np.asarray(vecs, dtype=float).mean(axis=0)
    norm = np.linalg.norm(centroid)
    if norm > 0:
        centroid = centroid / norm

    job.embedding = _serialize(centroid)
    job.embedding_model = EMBEDDING_MODEL
    session.add(job)
    session.commit()
    return centroid
