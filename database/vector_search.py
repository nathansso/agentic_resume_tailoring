"""Dual-path embedding similarity (issue #142).

A single seam for vector similarity so the P1 consumers (#137 JobCard top-N,
#121 requirement coverage) and the existing matcher / skill-scorer share one
implementation:

- **SQLite / in-memory** — numpy dot-product over candidate vectors. This
  reproduces the pre-#142 matcher (``np.dot`` + ``argmax``) and scorer
  (``skill_vec @ jd_vec``) math exactly for the all-MiniLM vectors, which are
  produced with ``normalize_embeddings=True``.
- **PostgreSQL** — pgvector ``<=>`` ANN over a stored ``vector(384)`` column.

The JSON ``embedding`` TEXT column stays the portable source of truth and the
only path SQLite ever uses; the pgvector column is a Postgres-only accelerator
added by the guarded migration in ``database/db.py``. On SQLite ``search_similar``
never emits vector SQL — with a ``model_cls`` and no candidates it simply returns
``[]``.
"""
import logging
from typing import Any, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)


def _as_vec(v: Any) -> Optional[np.ndarray]:
    """Coerce to a float ndarray, or None when absent/empty."""
    if v is None:
        return None
    arr = np.asarray(v, dtype=float)
    return arr if arr.size else None


def cosine_sim(a: Any, b: Any) -> float:
    """Cosine similarity of two vectors.

    all-MiniLM vectors arrive pre-normalized, so for the real inputs this equals
    their dot product (matching the old ``skill_vec @ jd_vec``); we divide by the
    norms defensively for the general case. Returns 0.0 on a missing/zero vector.
    """
    va, vb = _as_vec(a), _as_vec(b)
    if va is None or vb is None:
        return 0.0
    na, nb = float(np.linalg.norm(va)), float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _is_postgres(session) -> bool:
    if session is None:
        return False
    try:
        return session.get_bind().dialect.name == "postgresql"
    except Exception:  # pragma: no cover - defensive
        return False


def vector_literal(vec: Sequence[float]) -> str:
    """Render a vector as a pgvector text literal: ``[0.1,0.2,...]``."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def _search_numpy(
    query_vec: Any, candidates: Sequence[Tuple[Any, Any]], k: int
) -> List[Tuple[Any, float]]:
    """Top-k by dot product over in-memory (key, vector) candidates.

    For normalized vectors the top-1 result is identical to the matcher's old
    ``argmax`` of ``np.dot(user_embeddings, job_embedding)``.
    """
    q = _as_vec(query_vec)
    if q is None or not candidates:
        return []
    keys: List[Any] = []
    vecs: List[np.ndarray] = []
    for key, vec in candidates:
        av = _as_vec(vec)
        if av is None:
            continue
        keys.append(key)
        vecs.append(av)
    if not vecs:
        return []
    scores = np.vstack(vecs).dot(q)  # (n,)
    order = np.argsort(scores)[::-1][: max(1, k)]
    return [(keys[i], float(scores[i])) for i in order]


def _search_pgvector(
    session, query_vec: Any, k: int, model_cls, vector_column: str
) -> List[Tuple[Any, float]]:
    """Top-k via pgvector ``<=>`` (cosine distance) over a stored column.

    Returns (primary-key, similarity) with similarity = 1 - distance, ordered by
    ascending distance. Postgres-only; not exercised by the SQLite test suite.
    """
    q = _as_vec(query_vec)
    if q is None or model_cls is None:
        return []
    from sqlalchemy import text

    table = model_cls.__tablename__
    pk = list(model_cls.__table__.primary_key.columns.keys())[0]
    sql = text(
        f'SELECT "{pk}", {vector_column} <=> :qvec AS distance '
        f'FROM "{table}" WHERE {vector_column} IS NOT NULL '
        f"ORDER BY distance ASC LIMIT :k"
    )
    rows = session.execute(
        sql, {"qvec": vector_literal(q), "k": max(1, k)}
    ).fetchall()
    return [(r[0], 1.0 - float(r[1])) for r in rows]


def search_similar(
    query_vec: Any,
    k: int = 1,
    *,
    candidates: Optional[Sequence[Tuple[Any, Any]]] = None,
    session=None,
    model_cls=None,
    vector_column: str = "embedding_vec",
) -> List[Tuple[Any, float]]:
    """Return the ``k`` most-similar items to ``query_vec`` as ``[(key, score)]``,
    score descending.

    Path selection:

    - ``candidates`` given, or ``session`` is not Postgres → in-memory numpy
      dot-product. This is what the matcher and scorer route through (they hold
      vectors in memory), and it reproduces their previous behavior exactly.
    - Postgres session + ``model_cls`` + no ``candidates`` → pgvector ``<=>`` ANN
      over ``vector_column``. This is the accelerated path for the #137/#121
      consumers.

    On SQLite a ``model_cls`` query has no vector column to hit, so it falls to
    the numpy path with no candidates and returns ``[]`` — never touching a
    vector column.
    """
    if candidates is not None or not _is_postgres(session):
        return _search_numpy(query_vec, candidates or [], k)
    return _search_pgvector(session, query_vec, k, model_cls, vector_column)
