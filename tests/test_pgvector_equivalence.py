"""pgvector search-path equivalence (issues #142, #149).

`database/vector_search.py::_search_pgvector` has never run in a test. #142
shipped the dual-path seam and validated only the numpy side, noting the `<=>`
branch was "validated on staging Postgres, not here"; #137 then built a consumer
against a path that returns `[]` on SQLite while its tests stayed green. These
tests are the first execution of the Postgres branch under the suite.

Equivalence is asserted on **normalized** vectors. That is not a convenience:
`<=>` is cosine distance while `_search_numpy` takes a raw dot product, and the
two agree only when the inputs are unit vectors. The system's real vectors are
all-MiniLM produced with `normalize_embeddings=True`, so this is the true
operating condition — but an unnormalized fixture would fail here for a reason
that is not a bug, hence `_norm` on everything.

Vectors are written with raw SQL. `embedding_vec` has no production writer yet
(that is #60); this needs vectors *in the column*, not the pipeline that puts
them there, so the two concerns stay separate.
"""
import numpy as np
import pytest
from sqlalchemy import text
from sqlmodel import Session

from conftest import requires_postgres
from database.vector_search import _search_numpy, search_similar, vector_literal

DIM = 384  # must match the vector(384) column in _migrate_pg_vector_columns


def _norm(v):
    v = np.asarray(v, dtype=float)
    return v / np.linalg.norm(v)


def _seed_vectors(engine, n=12, seed=7):
    """Insert n skills with normalized embedding_vec values. Returns [(id, vec)]."""
    import database.db as db
    from database.models import Skill

    db._migrate_pg_vector_columns()  # adds embedding_vec; dialect-guarded

    rng = np.random.default_rng(seed)
    rows = []
    with Session(engine) as session:
        for i in range(n):
            skill = Skill(name=f"skill_{i}", category="language")
            session.add(skill)
            session.commit()
            session.refresh(skill)
            vec = _norm(rng.standard_normal(DIM))
            session.execute(
                text("UPDATE skill SET embedding_vec = :v WHERE skill_id = :i"),
                {"v": vector_literal(vec), "i": str(skill.skill_id)},
            )
            session.commit()
            rows.append((skill.skill_id, vec))
    return rows


@requires_postgres
def test_pgvector_topk_matches_numpy_ordering(isolated_engine):
    """The `<=>` branch returns the same top-k, in the same order, as numpy."""
    from database.models import Skill

    rows = _seed_vectors(isolated_engine)
    query = _norm(np.random.default_rng(99).standard_normal(DIM))

    with Session(isolated_engine) as session:
        pg = search_similar(query, k=5, session=session, model_cls=Skill)

    numpy_side = _search_numpy(query, rows, k=5)

    assert [k for k, _ in pg] == [k for k, _ in numpy_side]


@requires_postgres
def test_pgvector_scores_match_numpy_scores(isolated_engine):
    """Similarities agree numerically, not just in rank order.

    `1 - cosine_distance` and the dot product coincide for unit vectors; a
    mismatch here means the seam's score is not the quantity callers think it is.
    """
    from database.models import Skill

    rows = _seed_vectors(isolated_engine)
    query = _norm(np.random.default_rng(101).standard_normal(DIM))

    with Session(isolated_engine) as session:
        pg = search_similar(query, k=5, session=session, model_cls=Skill)

    numpy_scores = dict(_search_numpy(query, rows, k=len(rows)))

    for key, score in pg:
        assert score == pytest.approx(numpy_scores[key], rel=1e-5, abs=1e-6)


@requires_postgres
def test_pgvector_path_is_actually_taken(isolated_engine):
    """Guard against the test passing because it silently fell back to numpy.

    `search_similar` returns `[]` on the numpy path when given a model_cls and
    no candidates, so a non-empty result is proof the pgvector branch ran.
    """
    from database.models import Skill

    _seed_vectors(isolated_engine, n=3)
    query = _norm(np.random.default_rng(5).standard_normal(DIM))

    with Session(isolated_engine) as session:
        result = search_similar(query, k=3, session=session, model_cls=Skill)

    assert len(result) == 3


@requires_postgres
def test_pgvector_skips_null_vectors(isolated_engine):
    """Rows with no embedding_vec are excluded, not scored as zero.

    Relevant to #60: until the write path lands, most rows have NULL here, and
    a NULL must never be silently treated as the origin vector.
    """
    from database.models import Skill

    rows = _seed_vectors(isolated_engine, n=4)
    with Session(isolated_engine) as session:
        session.add(Skill(name="no_vector", category="language"))
        session.commit()

        result = search_similar(
            _norm(np.random.default_rng(3).standard_normal(DIM)),
            k=10, session=session, model_cls=Skill,
        )

    assert len(result) == len(rows)
