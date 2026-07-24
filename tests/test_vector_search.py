"""Dual-path vector-search seam tests (issue #142).

The suite runs on SQLite, so it exercises the numpy fallback; the pgvector `<=>`
branch is validated on staging Postgres, not here. These tests pin the two hard
backward-compat requirements: the numpy path reproduces the pre-#142
matcher/scorer math exactly, and SQLite never attempts the Postgres-only vector
column.
"""
import numpy as np
import pytest
from sqlalchemy import inspect
from sqlmodel import SQLModel, Session, create_engine

from database.vector_search import cosine_sim, search_similar, _is_postgres


def _norm(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def test_numpy_top1_matches_old_argmax():
    """search_similar top-1 == the matcher's previous np.dot + argmax."""
    rng = np.random.default_rng(42)
    user = np.vstack([_norm(rng.standard_normal(8)) for _ in range(6)])
    names = [f"skill_{i}" for i in range(6)]
    query = _norm(rng.standard_normal(8))

    # Old behavior
    sims = np.dot(user, query.T).flatten()
    old_idx = int(np.argmax(sims))
    old = (names[old_idx], float(sims[old_idx]))

    # Seam behavior
    top = search_similar(query, k=1, candidates=list(zip(names, user)))
    assert top[0][0] == old[0]
    assert top[0][1] == old[1]  # bit-identical dot product


def test_numpy_topk_is_ordered_descending():
    names = ["a", "b", "c"]
    vecs = [_norm([1, 0]), _norm([0.9, 0.1]), _norm([0, 1])]
    query = _norm([1, 0])
    top = search_similar(query, k=3, candidates=list(zip(names, vecs)))
    scores = [s for _, s in top]
    assert scores == sorted(scores, reverse=True)
    assert top[0][0] == "a"


def test_cosine_sim_matches_old_dot_for_normalized_vectors():
    """The scorer's _semantic_similarity used a raw dot on normalized vectors."""
    a, b = _norm([1, 2, 3, 4]), _norm([2, 1, 0, 1])
    # Equal to the old raw dot up to the norm division's float precision.
    assert cosine_sim(a, b) == pytest.approx(float(a @ b), abs=1e-12)


def test_cosine_sim_handles_missing_and_zero_vectors():
    assert cosine_sim(None, [1, 2]) == 0.0
    assert cosine_sim([0, 0, 0], [1, 2, 3]) == 0.0


def test_search_similar_empty_inputs():
    assert search_similar(None, candidates=[("a", [1, 2])]) == []
    assert search_similar([1, 2], candidates=[]) == []


def test_sqlite_model_query_never_touches_vector_column():
    """With a SQLite session + model_cls (no candidates), the pgvector path is
    skipped entirely — it returns [] rather than emitting SQL against a column
    that does not exist on SQLite."""
    from database.models import Skill
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        assert not _is_postgres(session)
        # Would raise (no such column: embedding_vec) if it ran the pg query.
        assert search_similar([0.1, 0.2, 0.3], model_cls=Skill, session=session) == []


def test_pg_vector_migration_is_noop_on_sqlite(monkeypatch):
    """The guarded migration adds no vector column to a SQLite database."""
    import database.db as db
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(db, "engine", engine)
    db._migrate_pg_vector_columns()  # guarded — must be a silent no-op
    cols = {c["name"] for c in inspect(engine).get_columns("skill")}
    assert "embedding_vec" not in cols
    assert "embedding" in cols  # the JSON source-of-truth column stays
