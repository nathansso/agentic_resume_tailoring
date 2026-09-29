"""The Jev decision cache (#193): one row per (state, question, model).

The key is per question, not per request, so questions batched into one
request are cached individually and a later request that repeats some of them
sends only the new ones. It hashes the canonical state, the question (with its
version) and the *requested* model, so pinning a different model is a
different decision.

Following `harness/render_cache.py`: a failed write is logged and never
raised (the answer is still returned), and a failed read is a miss.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlmodel import Session, select

import database.db as db
from database.models import JevDecision
from harness.decisions.questions import Question, canonical

log = logging.getLogger(__name__)

_CHUNK = 500


def cache_key(state: Any, question: Question, requested_model: str) -> str:
    body = canonical({"state": state, "question": question.canonical(),
                      "model": requested_model})
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def get_many(keys: Iterable[str]) -> Dict[str, JevDecision]:
    """The cached rows among `keys`, detached from their session. A store that
    cannot be read is a miss for every key."""
    wanted = sorted(set(keys))
    found: Dict[str, JevDecision] = {}
    try:
        with Session(db.engine, expire_on_commit=False) as session:
            for start in range(0, len(wanted), _CHUNK):
                rows = session.exec(select(JevDecision).where(
                    JevDecision.cache_key.in_(wanted[start:start + _CHUNK]))).all()
                for row in rows:
                    session.expunge(row)
                    found[row.cache_key] = row
    except Exception as exc:
        log.warning("jev cache: could not read %d keys: %s", len(wanted), exc)
        return {}
    return found


def put_many(rows: Sequence[JevDecision]) -> bool:
    """Store `rows`; True when they were stored. Never raises."""
    if not rows:
        return True
    try:
        with Session(db.engine) as session:
            for row in rows:
                session.merge(row)
            session.commit()
        return True
    except Exception as exc:
        log.warning("jev cache: could not store %d decisions: %s", len(rows), exc)
        return False


def make_row(key: str, question: Question, answer: Dict[str, Any], *, requested_model: str,
             resolved_model: str, input_tokens: Optional[float] = None,
             output_tokens: Optional[float] = None) -> JevDecision:
    return JevDecision(
        cache_key=key, point=question.point, question_version=question.version,
        requested_model=requested_model, resolved_model=resolved_model,
        question=question.wire(), answer=dict(answer), input_tokens=input_tokens,
        output_tokens=output_tokens, created_at=datetime.utcnow())


def all_rows() -> List[JevDecision]:
    with Session(db.engine, expire_on_commit=False) as session:
        rows = session.exec(select(JevDecision).order_by(
            JevDecision.point, JevDecision.cache_key)).all()
        for row in rows:
            session.expunge(row)
    return list(rows)
