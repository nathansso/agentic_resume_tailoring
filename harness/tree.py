"""The tailoring tree (issue #196): every committed change is a node; HEAD per job.

A node is a full snapshot of what the user saw — tailored content, score,
manual .tex, layout overrides — and its parent is the version it revised.
`UserJobResult` remains the materialized current state that every existing
reader uses: a commit records what was just written there, and a checkout
writes the node back into it, so web, chat and CLI keep working unchanged.

Three properties the rest of the harness relies on:

- **Commits are guarded by HEAD.** `commit_node(..., expected_parent=...)`
  raises `StaleParent` when HEAD moved since the caller read it, so a plan
  built on an old version cannot silently overwrite a newer one (#197), and an
  editor edit and a host run cannot race.
- **Sibling nodes share a parent, therefore a context.** `sibling_pairs` hands
  #174 its preference pairs with no annotation.
- **The change feed is persistent.** `TreeEvent.event_id` is a cursor; the
  writers and `art ui` (#204) are different processes.

Every function takes `user_id` explicitly and scopes every query by it.
Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, select

import database.db as _db
from harness import ART_VERSION
from agents.checks import exp_key, proj_key
from database.models import JobDescription, JobHead, TailorNode, TreeEvent, UserJobResult

log = logging.getLogger(__name__)

SOURCES = ("host", "pipeline", "editor", "revert", "backfill")
_UNSET = object()


class StaleParent(Exception):
    """The caller's parent is not HEAD any more."""

    def __init__(self, expected, head):
        super().__init__(f"expected parent {expected}, HEAD is {head}")
        self.expected, self.head = expected, head


class NotFound(Exception):
    pass


# ── helpers ──────────────────────────────────────────────────────────────────

def _uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def _json(value, default):
    import json
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return default
    return value if value is not None else default


def _head_row(session, user_id: UUID, job_id: UUID) -> Optional[JobHead]:
    row = session.get(JobHead, job_id)
    return row if row is not None and row.user_id == user_id else None


def _node(session, user_id: UUID, node_id) -> TailorNode:
    node = session.get(TailorNode, _uuid(node_id))
    if node is None or node.user_id != user_id:
        raise NotFound(f"no node {node_id}")
    return node


def _summary(n: TailorNode) -> Dict[str, Any]:
    return {"node_id": str(n.node_id), "parent_id": str(n.parent_id) if n.parent_id else None,
            "job_id": str(n.job_id), "seq": n.seq, "source": n.source, "note": n.note,
            "provenance": n.provenance or {}, "created_at": n.created_at.isoformat()}


def _full(n: TailorNode) -> Dict[str, Any]:
    return {**_summary(n), "content": _json(n.content, {}),
            "score_breakdown": _json(n.score_breakdown, {}), "program": n.program,
            "metrics": n.metrics or {}, "edited_tex": n.edited_tex,
            "layout_overrides": n.layout_overrides,
            "result_id": str(n.result_id) if n.result_id else None}


def _state(n: TailorNode):
    return (_json(n.content, {}), n.edited_tex, n.layout_overrides)


def _set_head(session, user_id: UUID, job_id: UUID, node_id: UUID) -> None:
    row = session.get(JobHead, job_id)
    if row is None:
        row = JobHead(job_id=job_id, user_id=user_id, node_id=node_id)
    else:
        row.node_id, row.updated_at = node_id, datetime.utcnow()
    session.add(row)


def _latest_result(session, user_id: UUID, job_id: UUID) -> Optional[UserJobResult]:
    rows = session.exec(select(UserJobResult).where(
        UserJobResult.user_id == user_id, UserJobResult.job_id == job_id)).all()
    return _db.latest_result(rows)


# ── writes ───────────────────────────────────────────────────────────────────

def commit_node(user_id, job_id, *, content: Dict, source: str,
                score_breakdown: Optional[Dict] = None, program: Optional[Dict] = None,
                metrics: Optional[Dict] = None, provenance: Optional[Dict] = None,
                edited_tex: Optional[str] = None, layout_overrides: Optional[Dict] = None,
                result_id=None, note: Optional[str] = None, expected_parent=_UNSET,
                event: bool = True, session: Optional[Session] = None) -> Dict[str, Any]:
    """Append a node under HEAD and move HEAD to it.

    `expected_parent`: pass the node id the caller built on (or None for "no
    history yet"). If HEAD is anything else, `StaleParent` is raised and nothing
    is written. Omit it to commit on whatever HEAD is (pipeline hooks do).
    """
    if source not in SOURCES:
        raise ValueError(f"unknown source {source!r}")
    user_id, job_id = _uuid(user_id), _uuid(job_id)
    own = session is None
    session = session or Session(_db.engine)
    try:
        head = _head_row(session, user_id, job_id)
        head_id = head.node_id if head else None
        if expected_parent is not _UNSET:
            want = _uuid(expected_parent) if expected_parent is not None else None
            if want != head_id:
                raise StaleParent(want, head_id)
        highest = session.exec(select(func.max(TailorNode.seq)).where(
            TailorNode.job_id == job_id)).one()
        node = TailorNode(
            user_id=user_id, job_id=job_id, parent_id=head_id,
            seq=0 if highest is None else highest + 1, source=source,
            content=content or {}, score_breakdown=score_breakdown or {},
            program=program, metrics=metrics or {}, provenance=provenance or {},
            edited_tex=edited_tex, layout_overrides=layout_overrides,
            result_id=_uuid(result_id) if result_id else None, note=note)
        session.add(node)
        session.flush()
        _set_head(session, user_id, job_id, node.node_id)
        if event:
            session.add(TreeEvent(user_id=user_id, job_id=job_id, node_id=node.node_id,
                                  kind="commit"))
        if own:
            session.commit()
            session.refresh(node)
        return _full(node)
    finally:
        if own:
            session.close()


def checkout(user_id, job_id, node_id, *, materialize: bool = True,
             session: Optional[Session] = None) -> Dict[str, Any]:
    """Move HEAD to `node_id` and write that node back into the job's current result."""
    user_id, job_id = _uuid(user_id), _uuid(job_id)
    own = session is None
    session = session or Session(_db.engine)
    try:
        node = _node(session, user_id, node_id)
        if node.job_id != job_id:
            raise NotFound(f"node {node_id} is not in job {job_id}")
        _set_head(session, user_id, job_id, node.node_id)
        if materialize:
            result = _latest_result(session, user_id, job_id)
            if result is not None:
                result.tailored_resume_content = _json(node.content, {})
                result.tailored_score_breakdown = _json(node.score_breakdown, {})
                result.edited_tex = node.edited_tex
                result.edited_tex_updated_at = datetime.utcnow() if node.edited_tex else None
                result.layout_overrides = node.layout_overrides
                result.updated_at = datetime.utcnow()
                session.add(result)
        session.add(TreeEvent(user_id=user_id, job_id=job_id, node_id=node.node_id,
                              kind="checkout"))
        if own:
            session.commit()
            session.refresh(node)
        return _full(node)
    finally:
        if own:
            session.close()


def record_result(user_id, job_id, *, source: str, note: Optional[str] = None,
                  provenance: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
    """Snapshot the job's current `UserJobResult` as a node — the write-site hook.

    Skips the commit when the result is identical to HEAD (a no-op save), and
    never raises: the tree is history, and a history failure must not fail the
    tailoring run, edit or revert that just succeeded.
    """
    try:
        user_id, job_id = _uuid(user_id), _uuid(job_id)
        with Session(_db.engine) as session:
            result = _latest_result(session, user_id, job_id)
            if result is None:
                return None
            state = (_json(result.tailored_resume_content, {}), result.edited_tex,
                     result.layout_overrides)
            if not state[0]:
                return None
            head = _head_row(session, user_id, job_id)
            if head is not None:
                current = session.get(TailorNode, head.node_id)
                if current is not None and _state(current) == state:
                    return None
            score = _json(result.tailored_score_breakdown, {})
            result_id = result.result_id
        # Read session closed before writing: SQLite allows one writer.
        return commit_node(
            user_id, job_id, content=state[0], source=source, score_breakdown=score,
            edited_tex=state[1], layout_overrides=state[2], result_id=result_id,
            note=note, provenance={"host": "art", "art_version": ART_VERSION,
                                   **(provenance or {})})
    except Exception:  # noqa: BLE001 — history must never break the write it follows
        log.exception("tailoring tree: could not record %s for job %s", source, job_id)
        return None


def record_revert(user_id, job_id) -> Optional[Dict[str, Any]]:
    """After a chat revert: move HEAD to its parent when that *is* the restored
    version, otherwise record the revert as a node. Never raises."""
    try:
        user_id, job_id = _uuid(user_id), _uuid(job_id)
        target = None
        with Session(_db.engine) as session:
            head = _head_row(session, user_id, job_id)
            result = _latest_result(session, user_id, job_id)
            if head is not None and result is not None:
                current = session.get(TailorNode, head.node_id)
                parent = session.get(TailorNode, current.parent_id) if (
                    current is not None and current.parent_id) else None
                if parent is not None and _json(parent.content, {}) == _json(
                        result.tailored_resume_content, {}):
                    target = parent.node_id
        if target is not None:
            return checkout(user_id, job_id, target, materialize=False)
        return record_result(user_id, job_id, source="revert", note="chat revert")
    except Exception:  # noqa: BLE001
        log.exception("tailoring tree: could not record revert for job %s", job_id)
        return None


# ── reads ────────────────────────────────────────────────────────────────────

def get_node(user_id, node_id) -> Dict[str, Any]:
    with Session(_db.engine) as session:
        return _full(_node(session, _uuid(user_id), node_id))


def history(user_id, job_id) -> List[Dict[str, Any]]:
    user_id, job_id = _uuid(user_id), _uuid(job_id)
    with Session(_db.engine) as session:
        rows = session.exec(select(TailorNode).where(
            TailorNode.user_id == user_id, TailorNode.job_id == job_id)
            .order_by(TailorNode.seq)).all()
        return [_summary(n) for n in rows]


def events_since(user_id, cursor: int = 0, job_id=None) -> List[Dict[str, Any]]:
    """The change feed after `cursor`, oldest first. Resume with the last event_id."""
    user_id = _uuid(user_id)
    with Session(_db.engine) as session:
        q = select(TreeEvent).where(TreeEvent.user_id == user_id,
                                    TreeEvent.event_id > int(cursor or 0))
        if job_id is not None:
            q = q.where(TreeEvent.job_id == _uuid(job_id))
        return [{"event_id": e.event_id, "job_id": str(e.job_id), "node_id": str(e.node_id),
                 "kind": e.kind, "created_at": e.created_at.isoformat()}
                for e in session.exec(q.order_by(TreeEvent.event_id)).all()]


def get_head(user_id, job_id, since_event: Optional[int] = None) -> Dict[str, Any]:
    """HEAD, plus what changed after `since_event` — including editor edits a host
    has not seen yet — and the cursor to pass next time."""
    user_id, job_id = _uuid(user_id), _uuid(job_id)
    with Session(_db.engine) as session:
        head = _head_row(session, user_id, job_id)
        node = session.get(TailorNode, head.node_id) if head else None
    events = events_since(user_id, since_event or 0, job_id)
    editor = []
    if since_event is not None:
        ids = {e["node_id"] for e in events if e["kind"] == "commit"}
        editor = [n for n in history(user_id, job_id)
                  if n["node_id"] in ids and n["source"] == "editor"]
    last = max([e["event_id"] for e in events], default=since_event or 0)
    return {"job_id": str(job_id), "head": _full(node) if node else None,
            "events": events, "editor_edits": editor, "cursor": last}


def sibling_pairs(user_id, job_id) -> List[Dict[str, str]]:
    """Every pair of nodes sharing a parent — one preference label each (#174)."""
    groups: Dict[Optional[str], List[str]] = {}
    for n in history(user_id, job_id):
        if n["parent_id"] is not None:
            groups.setdefault(n["parent_id"], []).append(n["node_id"])
    return [{"parent_id": p, "a": kids[i], "b": kids[j]}
            for p, kids in sorted(groups.items())
            for i in range(len(kids)) for j in range(i + 1, len(kids))]


def _items(content: Dict) -> Dict[str, Dict[str, Any]]:
    out = {}
    for e in content.get("experiences") or []:
        out[exp_key(e)] = {"title": f"{e.get('title', '')} @ {e.get('company', '')}",
                           "bullets": list(e.get("bullets") or [])}
    for p in content.get("projects") or []:
        out[proj_key(p)] = {"title": p.get("name", ""),
                            "bullets": list(p.get("bullets") or [])}
    return out


def diff_nodes(user_id, a, b) -> Dict[str, Any]:
    """What changed from node `a` to node `b`, by item key and bullet."""
    user_id = _uuid(user_id)
    with Session(_db.engine) as session:
        na, nb = _node(session, user_id, a), _node(session, user_id, b)
        ca, cb = _json(na.content, {}), _json(nb.content, {})
        tex_changed = na.edited_tex != nb.edited_tex
        layout_changed = na.layout_overrides != nb.layout_overrides
    ia, ib = _items(ca), _items(cb)
    changes = []
    for key in sorted(set(ia) | set(ib)):
        if key not in ia:
            changes.append({"key": key, "change": "added", "title": ib[key]["title"],
                            "bullets_added": ib[key]["bullets"]})
        elif key not in ib:
            changes.append({"key": key, "change": "removed", "title": ia[key]["title"],
                            "bullets_removed": ia[key]["bullets"]})
        elif ia[key]["bullets"] != ib[key]["bullets"]:
            before, after = ia[key]["bullets"], ib[key]["bullets"]
            changes.append({"key": key, "change": "revised", "title": ib[key]["title"],
                            "bullets_removed": [x for x in before if x not in after],
                            "bullets_added": [x for x in after if x not in before],
                            "reordered": sorted(before) == sorted(after)})
    skills_a = list(ca.get("skills_ranked") or [])
    skills_b = list(cb.get("skills_ranked") or [])
    return {"from": str(na.node_id), "to": str(nb.node_id), "items": changes,
            "skills_added": [s for s in skills_b if s not in skills_a],
            "skills_removed": [s for s in skills_a if s not in skills_b],
            "tex_changed": tex_changed, "layout_changed": layout_changed}


def list_jobs(user_id) -> List[Dict[str, Any]]:
    """The user's jobs with their HEAD, newest first. Minimal: #192 extends it."""
    user_id = _uuid(user_id)
    with Session(_db.engine) as session:
        jobs = session.exec(select(JobDescription).where(
            JobDescription.user_id == user_id)).all()
        heads = {h.job_id: h.node_id for h in session.exec(
            select(JobHead).where(JobHead.user_id == user_id)).all()}
        rows = [{"job_id": str(j.job_id), "title": j.title, "company": j.company,
                 "status": j.status, "head": str(heads[j.job_id]) if j.job_id in heads else None,
                 "created_at": j.created_at.isoformat() if j.created_at else None}
                for j in jobs]
    return sorted(rows, key=lambda r: (r["created_at"] or "", r["job_id"]), reverse=True)


# ── migration ────────────────────────────────────────────────────────────────

def backfill(engine) -> int:
    """Chain pre-#196 results into nodes, once per job. Idempotent: jobs that
    already have a node are skipped. Returns the number of nodes written.

    Only what the old storage kept can be recovered: each result's current
    content and its one-level `tailored_resume_previous`. Web re-tailors
    rewrote one row in place, so older versions are gone. Backfill writes no
    events — the feed is for live changes.
    """
    written = 0
    with Session(engine) as session:
        have = set(session.exec(select(TailorNode.job_id).distinct()).all())
        rows = session.exec(select(UserJobResult)).all()
        by_job: Dict[UUID, List[UserJobResult]] = {}
        for r in rows:
            if r.job_id not in have:
                by_job.setdefault(r.job_id, []).append(r)
        for job_id, results in by_job.items():
            results.sort(key=lambda r: (r.seq if r.seq is not None else -1,
                                        r.created_at, str(r.result_id)))
            for r in results:
                prev = _json(r.tailored_resume_previous, {}) or {}
                snaps = []
                if isinstance(prev, dict) and prev.get("content"):
                    snaps.append((prev.get("content"), prev.get("score_breakdown") or {},
                                  None, None, "previous version (backfilled)"))
                current = _json(r.tailored_resume_content, {})
                if current and "error" not in current:
                    snaps.append((current, _json(r.tailored_score_breakdown, {}),
                                  r.edited_tex, r.layout_overrides, "backfilled"))
                for content, score, tex, layout, note in snaps:
                    commit_node(r.user_id, job_id, content=content, source="backfill",
                                score_breakdown=score, edited_tex=tex,
                                layout_overrides=layout, result_id=r.result_id, note=note,
                                event=False, session=session)
                    written += 1
        session.commit()
    return written
