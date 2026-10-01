"""The bullet library and track baselines (issue #229): the Jev-free core of #199.

Tailoring works in three layers: raw facts (the KG), approved phrasings (this
library) and rules (preferences). Starting from approved text makes a run
cheaper, more consistent and faithful by construction. Everything here is
deterministic; the two judgment calls (which variant fits a job, which baseline
a job starts from) ship on the fallbacks `docs/harness.md` § 4 names, and #199
swaps Jev in behind them.

- **Variants** (`BulletVariant`). One phrasing of one experience or project
  bullet. An *approved* one is the user's confirmed wording: it arrives that way
  when the user's own curated library is imported (`upsert_items`, kind
  `variant`), or through `approve_variant`. A *draft* is what `promote_bullet`
  makes from a committed bullet; nothing approves a draft but an explicit
  `approve_variant`, and only approved variants are ever offered to a plan.
- **Baselines** (`TrackBaseline`). `save_baseline` pins a tree node as the
  baseline for a track, one per (user, track). A job's role family comes from
  the host (`open_job` metadata), else a deterministic title keyword map, else
  `other`; the track named after it is the job's baseline, else none, and a job
  with no history starts as a copy of that node (`harness/executor.py`).
- **`suggest_actions`.** Per experience or project on a node: the approved
  variant with the best overlap with the job's weighted terms (above a floor) or
  `no_match`, and the valid actions with uniform propensities, marked
  `source: "fallback"` so #199 can report `jev`.

Every function takes `user_id` and scopes every query by it, returns data and
never raises for bad input. Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from uuid import UUID

from sqlmodel import Session, select

import database.db as _db
import services
from agents.ats_scorer import ATSScoringEngine
from agents.checks import exp_key, proj_key
from agents.extraction_schemas import RoleFamily
from agents.job_card import role_family_from_title
from agents.tailor_planner import OPS
from database.models import (
    BulletVariant, JobDescription, JobRoleFamily, TailorNode, TrackBaseline,
)
from harness import tree
from harness.acceptance import VARIANT_DRIFT_TOLERANCE, token_distance
from harness.tools import _records

log = logging.getLogger(__name__)

APPROVED, DRAFT = "approved", "draft"
ROLE_FAMILIES = tuple(f.value for f in RoleFamily)

# `suggest_actions` offers a variant only when its overlap with the job's weighted terms is
# at least this: the share of the bullet's content tokens that are job terms, each counted
# at its weight relative to the heaviest term, so with uniform weights it is exactly
# `relevance_density`. 0.10 is a bullet whose tokens barely touch the posting (one or two
# stray terms in a typical 12-20 token bullet); a real match is usually well above it.
# Provisional, like the guard tolerances: #199's Jev choice replaces the rule.
VARIANT_MATCH_FLOOR = 0.10

_ROUND = 4


def _error(code: str, message: str, suggestions: Sequence[str] = ()) -> Dict[str, Any]:
    return {"error": {"code": code, "message": message, "suggestions": list(suggestions)}}


def norm_text(text: str) -> str:
    return " ".join((text or "").split())


def normalize_track(track: str) -> str:
    """A track name as stored: lowercase, runs of spaces and hyphens as `_`
    (`Data Science` and `data-science` are `data_science`)."""
    return re.sub(r"[\s\-]+", "_", (track or "").strip().lower())


def _uuid(value) -> Optional[UUID]:
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


# ── variants ─────────────────────────────────────────────────────────────────

def variant_ref(row: BulletVariant, score: Optional[float] = None) -> Dict[str, Any]:
    out = {"variant_id": str(row.variant_id), "item_key": row.item_key, "text": row.text,
           "status": row.status, "tags": dict(row.tags or {}), "cites": list(row.cites or []),
           "line_count": row.line_count,
           "source_node_id": str(row.source_node_id) if row.source_node_id else None}
    if score is not None:
        out["score"] = score
    return out


def _order(rows: Iterable[BulletVariant]) -> List[BulletVariant]:
    return sorted(rows, key=lambda r: (r.created_at, r.text.lower(), str(r.variant_id)))


def list_variants(user_id: UUID, item_key: Optional[str] = None,
                  status: Optional[str] = APPROVED) -> List[Dict[str, Any]]:
    """The user's variants, oldest first (then by text): approved only by default."""
    with Session(_db.engine) as session:
        q = select(BulletVariant).where(BulletVariant.user_id == user_id)
        if item_key:
            q = q.where(BulletVariant.item_key == item_key.strip().lower())
        if status:
            q = q.where(BulletVariant.status == status)
        return [variant_ref(r) for r in _order(session.exec(q).all())]


def variants_by_id(user_id: UUID) -> Dict[str, Dict[str, Any]]:
    """Every variant of this user, any status, by id: what the executor checks a
    `from_variant` against. Another user's id is simply absent."""
    return {v["variant_id"]: v for v in list_variants(user_id, status=None)}


def approved_by_item(user_id: UUID) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for v in list_variants(user_id):
        out.setdefault(v["item_key"], []).append(v)
    return out


def measure_line_count(text: str) -> Optional[int]:
    """Rendered lines of one bullet from the block render cache (#200), or None when
    no LaTeX engine is available. Never raises: a variant is stored without it."""
    from agents.formatter import _find_latex_engine
    from harness import executor, render_cache

    if executor.MEASURER is None and _find_latex_engine() is None:
        return None
    try:
        return int(render_cache.bullet_lines(
            [text], measurer=executor.MEASURER or render_cache.measure_lines)[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("variant line count failed: %s", exc)
        return None


def _kg_keys(user_id: UUID) -> Tuple[set, set]:
    """`(experience and project keys, every key)` in the user's KG."""
    records = _records(user_id)
    return ({r["key"] for r in records if r["kind"] in ("experience", "project")},
            {r["key"] for r in records})


def _cite_problem(cite: str, keys: set) -> Optional[str]:
    c = (cite or "").strip().lower()
    base, sep, idx = c.rpartition("#b")
    base = base if sep and idx.isdigit() else c
    return None if base in keys else f"cite {cite!r} names no item in the knowledge graph"


def import_variants(user_id: UUID, items: Sequence[Tuple[int, Dict]]) -> Dict[int, Dict]:
    """Store curated variants (`upsert_items`, kind `variant`): they arrive `approved`.

    One result per `(index, data)`. `item_key` must name an existing experience or
    project; cites default to the item itself; a duplicate (same item, same words)
    is `unchanged`, and a duplicate that is still a draft becomes approved
    (`merged`), because importing is the user's own confirmation.
    """
    out: Dict[int, Dict] = {}
    if not items:
        return out
    item_keys, all_keys = _kg_keys(user_id)
    with Session(_db.engine) as session:
        rows = session.exec(select(BulletVariant).where(BulletVariant.user_id == user_id)).all()
        known: Dict[Tuple[str, str], BulletVariant] = {
            (r.item_key, norm_text(r.text).lower()): r for r in rows}
        for idx, data in items:
            key = (data.get("item_key") or "").strip().lower()
            text = norm_text(data.get("text") or "")
            if key not in item_keys:
                out[idx] = {"status": "invalid", "key": None, "message":
                            f"No experience or project with key {data.get('item_key')!r}; see list_items."}
                continue
            if not text:
                out[idx] = {"status": "invalid", "key": None, "message": "text is required."}
                continue
            cites = [c.strip().lower() for c in data.get("cites") or [] if c and c.strip()] or [key]
            bad = next((p for p in (_cite_problem(c, all_keys) for c in cites) if p), None)
            if bad:
                out[idx] = {"status": "invalid", "key": None, "message": bad}
                continue
            tags = dict(data.get("tags") or {})
            if tags.get("track"):
                tags["track"] = normalize_track(tags["track"])
            dup = known.get((key, text.lower()))
            if dup is not None:
                changed = []
                if dup.status != APPROVED:
                    dup.status = APPROVED
                    changed.append("status")
                merged_tags = {**(dup.tags or {}), **tags}
                if merged_tags != (dup.tags or {}):
                    dup.tags = merged_tags
                    changed.append("tags")
                merged_cites = list(dup.cites or []) + [c for c in cites if c not in (dup.cites or [])]
                if data.get("cites") and merged_cites != list(dup.cites or []):
                    dup.cites = merged_cites
                    changed.append("cites")
                if changed:
                    dup.updated_at = datetime.utcnow()
                    session.add(dup)
                out[idx] = {"status": "merged" if changed else "unchanged",
                            "key": f"variant:{dup.variant_id}",
                            "message": f"Updated {', '.join(changed)}." if changed else None}
                continue
            row = BulletVariant(user_id=user_id, item_key=key, text=text, tags=tags,
                                status=APPROVED, cites=cites, line_count=measure_line_count(text))
            session.add(row)
            known[(key, text.lower())] = row
            out[idx] = {"status": "created", "key": f"variant:{row.variant_id}", "message": None}
        session.commit()
    return out


def promote_bullet(user_id: UUID, node_id: str, bullet: str, item_key: Optional[str] = None,
                   track: Optional[str] = None) -> Dict[str, Any]:
    """A **draft** variant from a bullet on a committed node of the user's own job.

    Never approves: `approve_variant` is the only way a draft becomes usable. A bullet
    that is already a variant of that item (any status) returns that variant
    (`created: false`) rather than a second copy. `track` defaults to the job's role family.
    """
    text = norm_text(bullet)
    if not text:
        return _error("invalid_arguments", "bullet is required.")
    uid_node = _uuid(node_id)
    if uid_node is None:
        return _error("not_found", f"No node {node_id!r}.")
    with Session(_db.engine) as session:
        node = session.get(TailorNode, uid_node)
        if node is None or node.user_id != user_id:
            return _error("not_found", f"No node {node_id!r}.")
        content = tree._json(node.content, {})
        job_id = node.job_id
        job = session.get(JobDescription, job_id)
        job_title = job.title if job else ""
    wanted = (item_key or "").strip().lower()
    found: Dict[str, Tuple[str, Dict]] = {}
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            k = key_fn(item)
            for b in item.get("bullets") or []:
                if norm_text(b) == text and (not wanted or k == wanted):
                    found[k] = (b, item)
    if not found:
        return _error("bullet_not_found",
                      f"That bullet is not on node {node_id}" + (f" under {item_key}" if item_key else "") + ".")
    if len(found) > 1:
        return _error("ambiguous_bullet", "The bullet is under more than one item; pass item_key.",
                      sorted(found))
    (key, (original, item)), = found.items()
    item_keys, _ = _kg_keys(user_id)
    if key not in item_keys:
        return _error("unknown_key", f"{key} is no longer in the knowledge graph.")
    cites = (item.get("cites") or {}).get(original) if isinstance(item.get("cites"), dict) else None
    cites = [c for c in cites or [] if c] or [key]
    tags: Dict[str, Any] = {"job_id": str(job_id)}
    chosen = normalize_track(track) if track else role_family_of(user_id, job_id, job_title)[0]
    if chosen and (track or chosen != RoleFamily.OTHER.value):
        tags["track"] = chosen
    with Session(_db.engine) as session:
        for r in session.exec(select(BulletVariant).where(
                BulletVariant.user_id == user_id, BulletVariant.item_key == key)).all():
            if norm_text(r.text).lower() == text.lower():
                return {"variant": variant_ref(r), "created": False}
        row = BulletVariant(user_id=user_id, item_key=key, text=text, tags=tags, status=DRAFT,
                            cites=cites, line_count=measure_line_count(text),
                            source_node_id=uid_node)
        session.add(row)
        session.commit()
        session.refresh(row)
        return {"variant": variant_ref(row), "created": True}


def approve_variant(user_id: UUID, variant_id: str) -> Dict[str, Any]:
    """Confirm a draft: the only path from draft to approved. Owner only; approving an
    approved variant is a no-op that says so."""
    vid = _uuid(variant_id)
    with Session(_db.engine) as session:
        row = session.get(BulletVariant, vid) if vid else None
        if row is None or row.user_id != user_id:
            return _error("not_found", f"No variant {variant_id!r}.")
        was = row.status
        if was != APPROVED:
            row.status = APPROVED
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return {"variant": variant_ref(row), "changed": was != APPROVED}


# ── role family and baselines ────────────────────────────────────────────────

def role_family_of(user_id: UUID, job_id: UUID, title: Optional[str] = None) -> Tuple[str, str]:
    """`(family, source)` for a job: what `open_job` recorded, else the title map."""
    with Session(_db.engine) as session:
        row = session.get(JobRoleFamily, job_id)
        if row is not None and row.user_id == user_id:
            return row.role_family, row.source
        if title is None:
            job = session.get(JobDescription, job_id)
            title = job.title if job is not None and job.user_id == user_id else ""
    family = role_family_from_title(title or "")
    return family, ("title" if family != RoleFamily.OTHER.value else "default")


def record_role_family(user_id: UUID, job_id: UUID, title: str,
                       supplied: Optional[str] = None) -> Tuple[str, str]:
    """Store a job's role family and return `(family, source)`.

    A family the host supplied wins and is kept. Without one, an earlier host answer
    stays; otherwise the deterministic title map decides (`other` when nothing matches).
    """
    with Session(_db.engine) as session:
        row = session.get(JobRoleFamily, job_id)
        if supplied:
            family, source = supplied, "host"
        elif row is not None and row.source == "host":
            return row.role_family, row.source
        else:
            family = role_family_from_title(title or "")
            source = "title" if family != RoleFamily.OTHER.value else "default"
        if row is None:
            row = JobRoleFamily(job_id=job_id, user_id=user_id, role_family=family, source=source)
        elif (row.role_family, row.source) != (family, source):
            row.role_family, row.source, row.updated_at = family, source, datetime.utcnow()
        session.add(row)
        session.commit()
        return family, source


def save_baseline(user_id: UUID, node_id: str, track: str) -> Dict[str, Any]:
    """Pin a node of the user's own as the baseline for `track`, replacing that
    track's earlier baseline."""
    name = normalize_track(track)
    if not name:
        return _error("invalid_arguments", "track is required.")
    nid = _uuid(node_id)
    with Session(_db.engine) as session:
        node = session.get(TailorNode, nid) if nid else None
        if node is None or node.user_id != user_id:
            return _error("not_found", f"No node {node_id!r}.")
        row = session.get(TrackBaseline, (user_id, name))
        replaced = str(row.node_id) if row is not None and row.node_id != node.node_id else None
        if row is None:
            row = TrackBaseline(user_id=user_id, track=name, node_id=node.node_id, job_id=node.job_id)
        else:
            row.node_id, row.job_id, row.saved_at = node.node_id, node.job_id, datetime.utcnow()
        session.add(row)
        session.commit()
        return {"track": name, "node_id": str(node.node_id), "job_id": str(node.job_id),
                "replaced": replaced}


def choose_baseline(user_id: UUID, job_id: UUID) -> Optional[Dict[str, str]]:
    """The baseline a job branches from: the track named after its role family, else
    None. `{track, node_id, role_family, source}`; `source` is how the family was found."""
    family, source = role_family_of(user_id, job_id)
    with Session(_db.engine) as session:
        row = session.get(TrackBaseline, (user_id, normalize_track(family)))
        if row is None:
            return None
        node = session.get(TailorNode, row.node_id)
        if node is None or node.user_id != user_id:
            return None
        return {"track": row.track, "node_id": str(row.node_id), "role_family": family,
                "source": source}


# ── matching a variant to a job ──────────────────────────────────────────────

def job_terms(weights: Optional[Dict[str, float]], jd_text: str) -> Dict[str, float]:
    """The job's weighted terms for matching: the stored keyword weights, else (no
    profile, or every term weighing zero) the posting's keywords at weight 1, the same
    uniform fallback `keyword_coverage` uses."""
    terms = {t: float(w) for t, w in (weights or {}).items() if w and w > 0}
    if terms:
        return terms
    return {t: 1.0 for t in ATSScoringEngine._extract_keywords(jd_text or "")}


def variant_score(text: str, terms: Dict[str, float]) -> float:
    """Overlap of `text` with the job's weighted terms: the share of its content tokens
    (`relevance_density`'s tokenization) that are job terms, each at its weight over
    the heaviest term's. Uniform weights give exactly `relevance_density`."""
    tokens = ATSScoringEngine._extract_keywords(text or "")
    top = max(terms.values(), default=0.0)
    if not tokens or top <= 0:
        return 0.0
    return round(sum(min(terms.get(t, 0.0) / top, 1.0) for t in tokens) / len(tokens), _ROUND)


def best_variant(variants: Sequence[Dict], terms: Dict[str, float]) -> Tuple[Optional[Dict], float]:
    """`(variant with its score, best score)`: the highest overlap at or above the floor,
    ties to the shorter text then the id so the pick never depends on storage order.
    `(None, best)` when nothing reaches the floor."""
    scored = sorted(((variant_score(v["text"], terms), v) for v in variants),
                    key=lambda sv: (-sv[0], len(sv[1]["text"]), sv[1]["text"], sv[1]["variant_id"]))
    if not scored:
        return None, 0.0
    best, v = scored[0]
    return ({**v, "score": best} if best >= VARIANT_MATCH_FLOOR and best > 0 else None), best


def valid_ops(key: str, content: Dict, kg_keys_by_kind: Dict[str, Sequence[str]],
              hard_suppress: Iterable[str], hard_emphasize: Iterable[str]) -> List[str]:
    """The ops the executor would not refuse for this item on this page: keep and revise
    unless a hard preference suppresses it, delete unless one emphasizes it or it is its
    section's last item, replace (projects only) unless one emphasizes it or no other
    project is free to swap in."""
    suppressed, emphasized = set(hard_suppress), set(hard_emphasize)
    section = "experiences" if key.startswith("exp:") else "projects"
    items = content.get(section) or []
    on_page = {exp_key(i) if section == "experiences" else proj_key(i) for i in items}
    ops = []
    for op in OPS:
        if op in ("keep", "revise") and key in suppressed:
            continue
        if op == "delete" and (key in emphasized or len(items) <= 1):
            continue
        if op == "replace":
            free = [k for k in kg_keys_by_kind.get("project", ())
                    if k not in on_page and k not in suppressed]
            if not key.startswith("proj:") or key in emphasized or not free:
                continue
        ops.append(op)
    return ops


def suggest_actions(user_id: UUID, job_id: str, node_id: Optional[str] = None) -> Dict[str, Any]:
    """Per experience and project on a node (HEAD by default): the chosen approved
    variant or `no_match`, and the valid actions with uniform propensities.

    The fallback path (`source: "fallback"`): retrieval overlap picks the variant, and
    every valid action is equally likely. Deterministic ordering throughout: items in
    page order, actions in `OPS` order. A job with no history is judged on the version
    its first plan would start from (a baseline copy, else the whole KG).
    """
    from harness import executor

    jid = _uuid(job_id)
    with Session(_db.engine) as session:
        job = session.get(JobDescription, jid) if jid else None
        if job is None or job.user_id != user_id:
            return _error("not_found", f"No job {job_id!r}.")
        session.expunge(job)
    if node_id:
        try:
            node = tree.get_node(user_id, node_id)
        except (tree.NotFound, ValueError) as exc:
            return _error("not_found", str(exc))
        if node["job_id"] != str(jid):
            return _error("not_found", f"Node {node_id} is not in job {job_id}.")
        content, at = node["content"], node["node_id"]
    else:
        head = tree.get_head(user_id, jid)["head"]
        if head is not None:
            content, at = head["content"], head["node_id"]
        else:
            kg = executor._KG(user_id)
            content, _ = executor.start_content(user_id, job, kg,
                                                executor.job_requirements(user_id, jid))
            at = None
    kg_by_kind: Dict[str, List[str]] = {}
    for r in _records(user_id, ["project"]):
        kg_by_kind.setdefault("project", []).append(r["key"])
    suppress, emphasize, _ = executor._hard_preferences(user_id, jid)
    terms = job_terms(services.resolve_keyword_weights(jid, user_id, job.description or "",
                                                       persist=False), job.description or "")
    library = approved_by_item(user_id)
    items = []
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            key = key_fn(item)
            chosen, best = best_variant(library.get(key, []), terms)
            ops = valid_ops(key, content, kg_by_kind, suppress, emphasize)
            items.append({
                "item_key": key,
                "title": (f"{item.get('title', '')} @ {item.get('company', '')}"
                          if section == "experiences" else item.get("name", "")),
                "match": "variant" if chosen else "no_match",
                "variant": chosen, "best_score": best,
                "approved_variants": len(library.get(key, [])),
                "actions": [{"op": op, "propensity": 1.0 / len(ops)} for op in ops],
                "source": "fallback"})
    return {"job_id": str(jid), "node_id": at, "floor": VARIANT_MATCH_FLOOR, "items": items}


# ── reporting ────────────────────────────────────────────────────────────────

def library_reuse(content: Dict, approved: Dict[str, Sequence[Dict]],
                  tolerance: float = VARIANT_DRIFT_TOLERANCE) -> Dict[str, Any]:
    """How much of a page is library text: the share of experience and project bullets
    that are verbatim, or within `tolerance` token edit distance of, an approved variant
    of their own item. Report-only: never a target, a guard or a gate (#229)."""
    total = verbatim = near = 0
    for section, key_fn in (("experiences", exp_key), ("projects", proj_key)):
        for item in content.get(section) or []:
            variants = [norm_text(v["text"]) for v in approved.get(key_fn(item), [])]
            for b in item.get("bullets") or []:
                if not (b or "").strip():
                    continue
                total += 1
                best = min((token_distance(v, b) for v in variants), default=None)
                if best is None:
                    continue
                if norm_text(b) in variants:
                    verbatim += 1
                elif best <= tolerance:
                    near += 1
    return {"bullets": total, "verbatim": verbatim, "edited": near,
            "share": round((verbatim + near) / total, _ROUND) if total else None}
