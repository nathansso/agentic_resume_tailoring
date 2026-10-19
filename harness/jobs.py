"""Which job? (issue #288): a last-touched signal and a ranked job lookup.

When the user says "revise the resume for this role", the host must act on the job they mean.
ART keeps no hidden current-job pointer. It gives the host a ranked guess, and the host
confirms it with the user (docs/harness.md § 2.1):

- **`last_touched_at`** on each job: set by every host write to the job (`open_job`, an
  `execute_plan` commit, `patch_plan`, `checkout`, `render`; the contract calls `touch`) and
  by the user opening the job in `artie ui` (`POST /api/jobs/{id}/open`, same-origin guarded
  like the editor's other writes). An editor open also sets `last_opened_at`, so the prompt
  hook can tell the user switching jobs in the editor from a host write (`latest_open`).
- **`list_jobs`** ranks the user's jobs: first by how well the company and title match the
  user's words (case- and punctuation-insensitive tokens), then by `last_touched_at`, newest
  first, then by creation. Each job says why it ranked there ("matched "zendar" (company);
  touched 2 h ago").

Tools that take a `job_id` still require it; nothing here picks a job silently. Every
function takes `user_id` explicitly and scopes every query by it. Model-free by rule
(`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlmodel import Session, select

import database.db as _db
from database.clock import as_utc, parse_utc, utc_now
from database.models import JobDescription, PlanProgram

log = logging.getLogger(__name__)

# Words that say which kind of thing the user means, not which one ("the Zendar role", "my
# resume for Acme"), on top of the resolver's company stop words.
_ROLE_WORDS = frozenset("role job position posting opening resume cv application one this "
                        "that my me i for with to in on".split())
COMPANY_WEIGHT, TITLE_WEIGHT = 2.0, 1.0
_PREFIX_MIN = 4          # "engineer" matches "engineering"; "ml" matches only "ml"


def _uuid(value) -> Optional[UUID]:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (TypeError, ValueError):
        return None


# ── the signal ───────────────────────────────────────────────────────────────

def touch(user_id, job_id, *, opened: bool = False) -> bool:
    """Mark the job as touched now (and, with `opened`, as opened in `artie ui`). False for an
    unknown or foreign job. Never raises: the signal must not fail the write it follows."""
    uid, jid = _uuid(user_id), _uuid(job_id)
    if uid is None or jid is None:
        return False
    try:
        with Session(_db.engine) as session:
            job = session.get(JobDescription, jid)
            if job is None or job.user_id != uid:
                return False
            now = utc_now()
            job.last_touched_at = now
            if opened:
                job.last_opened_at = now
            session.add(job)
            session.commit()
            return True
    except Exception:  # noqa: BLE001 — a read-only store, a lock: the write it follows stands
        log.exception("which job: could not touch job %s", job_id)
        return False


def label(user_id, job_id) -> Optional[Dict[str, str]]:
    """`{job_id, title, company}` for one of the user's jobs, or None."""
    uid, jid = _uuid(user_id), _uuid(job_id)
    if uid is None or jid is None:
        return None
    with Session(_db.engine) as session:
        job = session.get(JobDescription, jid)
        if job is None or job.user_id != uid:
            return None
        return {"job_id": str(job.job_id), "title": job.title or "", "company": job.company or ""}


def program_job(user_id, program_id: str) -> Optional[str]:
    """The job a saved plan program belongs to, or None."""
    uid = _uuid(user_id)
    with Session(_db.engine) as session:
        row = session.get(PlanProgram, program_id)
        return str(row.job_id) if row is not None and row.user_id == uid else None


def latest_open(user_id) -> Optional[Tuple[str, datetime]]:
    """The job the user opened in `artie ui` most recently, and when; None if they never did."""
    uid = _uuid(user_id)
    with Session(_db.engine) as session:
        job = session.exec(select(JobDescription).where(
            JobDescription.user_id == uid, JobDescription.last_opened_at.is_not(None))
            .order_by(JobDescription.last_opened_at.desc(), JobDescription.job_id)).first()
        return (str(job.job_id), as_utc(job.last_opened_at)) if job is not None else None


# ── the ranking (pure) ───────────────────────────────────────────────────────

def tokens(text: Any) -> List[str]:
    """Case- and punctuation-insensitive words, in order, without repeats: "Zendar, Inc." and
    "zendar" share "zendar". The fold is the one ingestion matches skill names with."""
    from harness.anchors import _COMPANY_STOP
    from harness.ingest import _norm

    out: List[str] = []
    for t in _norm(str(text or "")).split():
        if t not in _COMPANY_STOP and t not in out:
            out.append(t)
    return out


def _hits(word: str, field: Sequence[str]) -> bool:
    if word in field or word == "".join(field):       # "openai" names "Open AI"
        return True
    return len(word) >= _PREFIX_MIN and any(
        len(t) >= _PREFIX_MIN and (t.startswith(word) or word.startswith(t)) for t in field)


def match(query_words: Sequence[str], company: str, title: str) -> Tuple[float, List[str]]:
    """(score, the reasons) for one job: each of the user's words that names the company counts
    COMPANY_WEIGHT, each that names the title TITLE_WEIGHT (a word that names both counts as
    the company)."""
    comp, ttl = tokens(company), tokens(title)
    score, matched = 0.0, []
    for w in query_words:
        if _hits(w, comp):
            score += COMPANY_WEIGHT
            matched.append(f'"{w}" (company)')
        elif _hits(w, ttl):
            score += TITLE_WEIGHT
            matched.append(f'"{w}" (title)')
    return score, matched


def ago(when: Optional[datetime], now: datetime) -> str:
    if when is None:
        return "never touched"
    seconds = max(0.0, (now - as_utc(when)).total_seconds())
    if seconds < 60:
        return "touched just now"
    if seconds < 3600:
        return f"touched {int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"touched {int(seconds // 3600)} h ago"
    return f"touched {int(seconds // 86400)} d ago"


def rank(jobs: Sequence[Dict[str, Any]], query: Optional[str],
         now: Optional[datetime] = None) -> Tuple[List[Dict[str, Any]], int]:
    """Order `jobs` (rows from `tree.list_jobs`) for `query`: match score, then
    `last_touched_at` (never touched last), then newest created. Returns (rows, how many
    matched). Each row gains `score` (None without a query), `matched_words` and `reason`."""
    now = now or utc_now()
    words = [w for w in tokens(query) if w not in _ROLE_WORDS] if query else []
    out = []
    for job in jobs:
        touched = parse_utc(job.get("last_touched_at"))
        score, matched = match(words, job.get("company") or "", job.get("title") or "") \
            if words else (0.0, [])
        recency = ago(touched, now)
        if not words:
            reason = recency
        elif matched:
            reason = f"matched {', '.join(matched)}; {recency}"
        else:
            reason = f"no match; {recency}"
        out.append({**job, "score": score if words else None, "matched_words": matched,
                    "reason": reason, "_touched": touched})
    out.sort(key=lambda r: r["job_id"])
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    out.sort(key=lambda r: r["_touched"].timestamp() if r["_touched"] else float("-inf"),
             reverse=True)
    out.sort(key=lambda r: r["score"] or 0.0, reverse=True)
    for r in out:
        del r["_touched"]
    return out, sum(1 for r in out if r["score"])


def list_jobs(user_id, status: Optional[str] = None, query: Optional[str] = None,
              limit: Optional[int] = None) -> Dict[str, Any]:
    """The contract's `list_jobs`: the user's jobs, ranked for `query` (see `rank`)."""
    from harness import tree

    rows, matched = rank(tree.list_jobs(user_id, status), query)
    return {"jobs": rows[:limit] if limit else rows, "query": query or None,
            "matched": matched if query else None}
