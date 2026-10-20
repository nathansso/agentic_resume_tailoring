"""Staged sources: what `artie ingest` read, waiting for the agent (#398).

ART is model-free, so ingestion runs in two halves. `artie ingest` (`harness/ingest_cmd.py`)
builds each repository's dossier deterministically and **stages** it here; the agent then
lists what is staged (`list_staged_sources`), reads each dossier, writes records from it with
`upsert_items`, and marks it processed (`mark_source_processed`).

Staging is incremental by the last-seen commit: a source whose commit and builder version are
unchanged is left exactly as it is (its status, its timestamps, its text), so a second run with
no new commits changes nothing. A new commit rebuilds the dossier and stages it again.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional
from uuid import UUID

from sqlmodel import Session, select

import database.db as _db
from database.clock import utc_now
from database.models import StagedSource

STATUSES = ("staged", "processed")
KINDS = ("github_repo", "local_repo")


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "suggestions": []}}


def evidence_source(kind: str, key: str) -> str:
    """What records written from this source cite, as `upsert_items`' `source`: the same
    `github:<repo>` `list_github_repos` gives (#353), or `repo:<folder>` for a local one."""
    if kind == "github_repo":
        return f"github:{key.rsplit('/', 1)[-1]}"
    return f"repo:{key.replace(chr(92), '/').rstrip('/').rsplit('/', 1)[-1]}"


def current(user_id: UUID, kind: str, key: str) -> Optional[StagedSource]:
    with Session(_db.engine) as s:
        return s.exec(select(StagedSource).where(
            StagedSource.user_id == user_id, StagedSource.kind == kind,
            StagedSource.key == key)).first()


def is_current(user_id: UUID, kind: str, key: str, commit: str, builder: str) -> bool:
    row = current(user_id, kind, key)
    return bool(row and row.commit == commit and row.builder == builder)


def stage(user_id: UUID, kind: str, key: str, commit: str, builder: str,
          build: Callable[[], Any], title: str = "") -> Dict[str, Any]:
    """Stage `key`'s dossier at `commit`. Unchanged (same commit and builder): nothing is
    built or written. Otherwise `build()` returns an object with `text` and `stats`, and the
    row is written and set to `staged`. Returns `{action, source_id}`, action one of
    `unchanged`, `staged` (new) or `restaged` (rebuilt at a new commit)."""
    if kind not in KINDS:
        raise ValueError(f"unknown source kind {kind!r}")
    row = current(user_id, kind, key)
    if row is not None and row.commit == commit and row.builder == builder:
        return {"action": "unchanged", "source_id": str(row.source_id)}
    dossier = build()
    now = utc_now()
    with Session(_db.engine) as s:
        if row is None:
            row = StagedSource(user_id=user_id, kind=kind, key=key, created_at=now)
            action = "staged"
        else:
            row = s.get(StagedSource, row.source_id)
            action = "restaged"
        row.title = title or key
        row.commit, row.builder = commit, builder
        row.content = dossier.text
        row.stats = dict(dossier.stats)
        row.status, row.note, row.processed_at = "staged", None, None
        row.updated_at = now
        s.add(row)
        s.commit()
        return {"action": action, "source_id": str(row.source_id)}


def _summary(row: StagedSource) -> Dict[str, Any]:
    stats = row.stats or {}
    return {
        "source_id": str(row.source_id), "kind": row.kind, "key": row.key, "title": row.title,
        "commit": row.commit, "status": row.status,
        "evidence_source": evidence_source(row.kind, row.key),
        "chars": len(row.content or ""), "est_tokens": stats.get("est_tokens"),
        "user_commits": stats.get("user_commits"), "authors": stats.get("authors"),
        "updated_at": row.updated_at, "processed_at": row.processed_at, "note": row.note,
    }


def list_staged_sources(user_id: UUID, status: Optional[str] = "staged",
                        source_id: Optional[str] = None) -> Dict[str, Any]:
    """The tool body. Without `source_id`: summaries of the user's sources with `status`
    (`staged` by default; None for all), oldest-updated first. With it: that one source,
    with its dossier text in `content`."""
    if source_id:
        try:
            sid = UUID(str(source_id))
        except ValueError:
            return _error("not_found", f"No staged source {source_id!r}.")
        with Session(_db.engine) as s:
            row = s.get(StagedSource, sid)
            if row is None or row.user_id != user_id:
                return _error("not_found", f"No staged source {source_id!r}.")
            return {"sources": [_summary(row)], "content": row.content,
                    "counts": _counts(s, user_id)}
    if status is not None and status not in STATUSES:
        return _error("invalid_status", f"status is one of {', '.join(STATUSES)}, or null for all.")
    with Session(_db.engine) as s:
        q = select(StagedSource).where(StagedSource.user_id == user_id)
        if status is not None:
            q = q.where(StagedSource.status == status)
        rows = sorted(s.exec(q).all(), key=lambda r: (r.updated_at, r.kind, r.key))
        return {"sources": [_summary(r) for r in rows], "content": None,
                "counts": _counts(s, user_id)}


def _counts(s: Session, user_id: UUID) -> Dict[str, int]:
    rows = s.exec(select(StagedSource.status).where(StagedSource.user_id == user_id)).all()
    return {st: sum(1 for r in rows if r == st) for st in STATUSES}


def mark_source_processed(user_id: UUID, source_id: str, note: Optional[str] = None) -> Dict[str, Any]:
    """The tool body: mark one source processed, after its records are written. Idempotent;
    a source rebuilt at a new commit comes back as `staged`."""
    try:
        sid = UUID(str(source_id))
    except ValueError:
        return _error("not_found", f"No staged source {source_id!r}.")
    with Session(_db.engine) as s:
        row = s.get(StagedSource, sid)
        if row is None or row.user_id != user_id:
            return _error("not_found", f"No staged source {source_id!r}.")
        changed = row.status != "processed"
        if changed or (note and note != row.note):
            now = utc_now()
            row.status = "processed"
            row.processed_at = row.processed_at if not changed else now
            if note:
                row.note = note
            row.updated_at = now
            s.add(row)
            s.commit()
            s.refresh(row)
        return {"sources": [_summary(row)], "changed": changed, "counts": _counts(s, user_id)}


__all__: List[str] = ["stage", "current", "is_current", "list_staged_sources",
                      "mark_source_processed", "evidence_source", "STATUSES", "KINDS"]
