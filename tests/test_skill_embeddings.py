"""Tests for cached skill/JD embeddings and the scorer's semantic component (#54 Phase 2)."""
import numpy as np
import pytest
from sqlmodel import Session, select

import agents.matcher as matcher_module
from config import EMBEDDING_MODEL
from database.models import JobDescription, JobSkill, Skill
from agents.skill_embeddings import (
    deserialize,
    ensure_job_embedding,
    ensure_skill_embeddings,
    load_skill_vectors,
)
from agents.skill_scorer import score_skills


# ── Fake embedding model (deterministic, no network) ───────────────────────────

class _FakeModel:
    """Encodes each text to a small deterministic vector keyed by its first char."""

    def encode(self, texts, normalize_embeddings=True):
        vecs = []
        for t in texts:
            seed = sum(ord(c) for c in t.lower()) or 1
            rng = np.random.default_rng(seed)
            vecs.append(rng.standard_normal(8))
        arr = np.asarray(vecs, dtype=float)
        if normalize_embeddings:
            norms = np.linalg.norm(arr, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            arr = arr / norms
        return arr


@pytest.fixture()
def fake_model(monkeypatch):
    monkeypatch.setattr(matcher_module, "get_embedding_model", lambda: _FakeModel())
    return _FakeModel()


def _add_skill(engine, name, category="Other"):
    with Session(engine) as s:
        skill = Skill(name=name, category=category)
        s.add(skill)
        s.commit()
        s.refresh(skill)
        return skill.skill_id


# ── ensure_skill_embeddings ────────────────────────────────────────────────────

def test_ensure_skill_embeddings_computes_and_caches(isolated_engine, fake_model):
    sid = _add_skill(isolated_engine, "Python")
    with Session(isolated_engine) as s:
        n = ensure_skill_embeddings(s, [sid])
        assert n == 1
        skill = s.get(Skill, sid)
        assert skill.embedding_model == EMBEDDING_MODEL
        assert deserialize(skill.embedding) is not None


def test_ensure_skill_embeddings_is_idempotent(isolated_engine, fake_model):
    sid = _add_skill(isolated_engine, "Rust")
    with Session(isolated_engine) as s:
        assert ensure_skill_embeddings(s, [sid]) == 1
        assert ensure_skill_embeddings(s, [sid]) == 0  # already embedded


def test_model_change_invalidates_cache(isolated_engine, fake_model):
    sid = _add_skill(isolated_engine, "Go")
    with Session(isolated_engine) as s:
        skill = s.get(Skill, sid)
        skill.embedding = "[0.0, 1.0]"
        skill.embedding_model = "some-old-model"
        s.add(skill)
        s.commit()
        assert ensure_skill_embeddings(s, [sid]) == 1  # recomputed
        assert s.get(Skill, sid).embedding_model == EMBEDDING_MODEL


def test_load_skill_vectors_returns_vectors(isolated_engine, fake_model):
    sid = _add_skill(isolated_engine, "Kubernetes")
    with Session(isolated_engine) as s:
        vecs = load_skill_vectors(s, [sid])
        assert sid in vecs
        assert isinstance(vecs[sid], np.ndarray)


def test_embeddings_noop_when_model_unavailable(isolated_engine, monkeypatch):
    def _raise():
        raise ImportError("no sentence-transformers")
    monkeypatch.setattr(matcher_module, "get_embedding_model", _raise)
    sid = _add_skill(isolated_engine, "Scala")
    with Session(isolated_engine) as s:
        assert ensure_skill_embeddings(s, [sid]) == 0  # graceful no-op
        assert s.get(Skill, sid).embedding is None


# ── ensure_job_embedding ───────────────────────────────────────────────────────

def test_ensure_job_embedding_caches_centroid(isolated_engine, fake_model):
    skill_id = _add_skill(isolated_engine, "TensorFlow")
    with Session(isolated_engine) as s:
        job = JobDescription(title="ML Eng", company="X", description="train models")
        s.add(job)
        s.commit()
        s.refresh(job)
        s.add(JobSkill(job_id=job.job_id, skill_id=skill_id))
        s.commit()

        vec = ensure_job_embedding(s, job)
        assert vec is not None
        cached = s.get(JobDescription, job.job_id)
        assert cached.embedding_model == EMBEDDING_MODEL
        # Second call returns the cached vector without recomputing.
        again = ensure_job_embedding(s, cached)
        assert np.allclose(again, vec)


# ── Scorer semantic component (pure, no DB/model) ──────────────────────────────

def test_semantic_component_boosts_aligned_skill():
    jd_vec = np.array([1.0, 0.0, 0.0])
    skill_vectors = {
        "Aligned": np.array([1.0, 0.0, 0.0]),     # cosine 1.0
        "Orthogonal": np.array([0.0, 1.0, 0.0]),  # cosine 0.0
    }
    skills = [
        {"name": "Aligned", "category": "Other", "proficiency": 3, "confidence": 0.5},
        {"name": "Orthogonal", "category": "Other", "proficiency": 3, "confidence": 0.5},
    ]
    # Neutral JD text so the semantic component is the differentiator.
    jd = "Aligned Orthogonal both mentioned equally here."
    ranked = score_skills(
        skills, jd, matched_skills={}, skill_vectors=skill_vectors, jd_vector=jd_vec
    )
    by_name = {s["name"]: s for s in ranked}
    assert "semantic" in by_name["Aligned"]["components"]
    assert by_name["Aligned"]["score"] > by_name["Orthogonal"]["score"]


def test_semantic_absent_without_vectors():
    skills = [{"name": "Python", "category": "Other", "proficiency": 3, "confidence": 0.5}]
    ranked = score_skills(skills, "Python role", matched_skills={})
    assert "semantic" not in ranked[0]["components"]
