"""The plan executor (issue #197): run a host's whole plan as one program.

`execute_plan` takes a `harness.program.Program` and does, in order
(docs/harness.md § 7):

1. **Base.** The parent node's content, which must be HEAD (`stale_parent`
   otherwise). A job with no history starts from `kg_default_content`: every
   experience and project with its source bullets, skills ranked against the
   posting, achievements and education verbatim — what a baseline will be once
   #199 ships track baselines.
2. **Arbitration.** A node that names a key the knowledge graph does not hold,
   cites something that does not resolve, or crosses a hard (strength-5)
   preference is *refused*: it never executes, and its reason is reported.
3. **Nodes in section order** (experiences, then projects, in page order), each
   under the per-metric acceptance rule (`harness/acceptance.py`). A node that
   fails is reverted and the rest continue.
4. **Finalize.** Hard-preference compliance, non-empty sections, the skills
   cap and floor, the per-bullet line gate and the page line budget (#200). Any
   violation means nothing commits; the host gets the violations and cut hints.
5. **Commit.** A `source=host` tree node carrying the program, the metric
   vectors and provenance, materialized into the job's current result so the
   web app and `art ui` show it.

Deterministic: the same program over the same store and render cache returns
the same result, byte for byte, apart from the new node's id. Every program is
saved (`PlanProgram`) so `patch_plan` can amend it by JSON pointer.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import copy
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from uuid import UUID

from pydantic import ValidationError
from sqlmodel import Session, select

import database.db as _db
import services
from agents.arbitration import HARD_STRENGTH
from agents.ats_scorer import ATSScoringEngine
from agents.checks import bullet_line_violations, exp_key, expected_sections, proj_key
from agents.preferences import preferences_in_scope
from agents.skill_scorer import rank_and_select_skills
from database.models import JobDescription, PlanProgram, UserJobResult
from harness import ART_VERSION, tree
from harness.acceptance import Context, accept, metric_vector, preference_violations
from harness.program import Program, apply_patch, program_id
from harness.tools import _records, skill_key

log = logging.getLogger(__name__)

# The bullet line measurer. None means `harness.render_cache.measure_lines`
# when a LaTeX engine is installed, and "unmeasured" when none is. Tests
# inject a fake here so CI needs no TeX.
MEASURER: Optional[Callable[[Sequence[str]], List[int]]] = None


class PlanError(Exception):
    def __init__(self, code: str, message: str, suggestions: Sequence[str] = ()):
        super().__init__(message)
        self.code, self.message, self.suggestions = code, message, list(suggestions)

    def as_dict(self) -> Dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message,
                          "suggestions": self.suggestions}}


# ── the knowledge graph, as the executor needs it ────────────────────────────

class _KG:
    """Keys, source bullets and defaults for one user, read once per run."""

    def __init__(self, user_id: UUID):
        self.records = _records(user_id)
        self.by_key = {r["key"]: r for r in self.records}
        self.source_bullets: Dict[str, List[str]] = {}
        for r in self.records:
            if r["kind"] == "experience":
                self.source_bullets[r["key"]] = [b for b in r["record"].get("bullets") or [] if b]
            elif r["kind"] == "project":
                blurbs = [b["content"] for b in r["record"].get("blurbs") or [] if b.get("content")]
                desc = (r["record"].get("description") or "").strip()
                self.source_bullets[r["key"]] = blurbs or ([desc] if desc else [])

    def of(self, kind: str) -> List[Dict]:
        return [r for r in self.records if r["kind"] == kind]

    def item(self, key: str) -> Dict:
        """A page item for a KG experience/project, with its source bullets."""
        rec = self.by_key[key]["record"]
        if key.startswith("exp:"):
            return {"title": rec["title"], "company": rec["company"],
                    "start_date": "" if rec.get("start") == "?" else rec.get("start") or "",
                    "end_date": "" if rec.get("end") == "?" else rec.get("end") or "",
                    "bullets": list(self.source_bullets.get(key, []))}
        return {"name": rec["name"], "bullets": list(self.source_bullets.get(key, []))}

    def resolves(self, cite: str) -> bool:
        cite = (cite or "").strip().lower()
        if cite in self.by_key:
            return True
        key, sep, idx = cite.rpartition("#b")
        return bool(sep) and idx.isdigit() and int(idx) < len(self.source_bullets.get(key, []))

    def skill(self, name: str) -> Optional[Dict]:
        r = self.by_key.get(skill_key({"name": name}))
        return r["record"] if r else None


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def kg_default_content(user_id: UUID, jd_text: str, kg: Optional[_KG] = None) -> Dict:
    """The version a job with no history starts from: the whole KG, untailored
    except for skills ranked against the posting."""
    kg = kg or _KG(user_id)
    skills = [{"name": s["record"]["name"], "category": s["record"].get("category"),
               "proficiency": _int(s["record"].get("proficiency"))} for s in kg.of("skill")]
    ranked = rank_and_select_skills(skills, jd_text or "") if skills else None
    if not ranked:
        ranked = [{"name": s["name"], "category": s["category"], "score": 0.0}
                  for s in sorted(skills, key=lambda s: s["name"].lower())]
    content = {
        "experiences": [kg.item(r["key"]) for r in kg.of("experience")],
        "projects": [kg.item(r["key"]) for r in kg.of("project")],
        "skills_ranked": ranked,
    }
    achievements = [{k: a["record"].get(k) or "" for k in ("title", "description", "issuer", "date")}
                    for a in kg.of("achievement")]
    if achievements:
        content["achievements"] = achievements
    education = [{"institution": e["record"]["institution"],
                  "degree": None if e["record"].get("degree") == "—" else e["record"].get("degree"),
                  "start_date": e["record"].get("start") or "",
                  "end_date": e["record"].get("end") or ""} for e in kg.of("education")]
    if education:
        content["education"] = education
    return content


# ── context ──────────────────────────────────────────────────────────────────

def _line_counter() -> Optional[Callable[[Dict], Optional[Dict[str, int]]]]:
    from agents.formatter import _find_latex_engine
    from harness import render_cache

    if MEASURER is None and _find_latex_engine() is None:
        return None
    measurer = MEASURER or render_cache.measure_lines

    def count(content: Dict) -> Optional[Dict[str, int]]:
        try:
            return render_cache.content_bullet_lines(content, measurer=measurer)
        except Exception as exc:  # a failed measurement never fails the plan
            log.warning("line measurement failed: %s", exc)
            return None
    return count


def _page_budget(content: Dict, budget: float) -> Optional[Dict]:
    from agents.formatter import _find_latex_engine
    from harness import render_cache

    if MEASURER is None and _find_latex_engine() is None:
        return None
    try:
        return render_cache.page_budget(content, budget=budget,
                                        measurer=MEASURER or render_cache.measure_lines)
    except Exception as exc:
        log.warning("page budget failed: %s", exc)
        return None


def _hard_preferences(user_id: UUID, job_id: UUID) -> Tuple[Dict[str, Dict], Dict[str, Dict], Dict[str, Dict]]:
    """Strength-5 preferences in scope: suppressed items, emphasized items,
    suppressed skills — each keyed to the preference that set it."""
    prefs = preferences_in_scope(services.load_preferences(user_id), job_id=str(job_id))
    suppress, emphasize, skills = {}, {}, {}
    for p in sorted(prefs, key=lambda p: str(p.get("preference_id"))):
        if (p.get("strength") or 0) < HARD_STRENGTH:
            continue
        key = (p.get("target_key") or "").strip().lower()
        term = (p.get("target_term") or "").strip().lower()
        if p.get("polarity") == "suppress":
            if key.startswith(("exp:", "proj:")):
                suppress[key] = p
            elif key.startswith("skill:"):
                skills[key.split(":", 1)[1]] = p
            elif term:
                skills[term] = p
        elif p.get("polarity") == "emphasize" and key.startswith(("exp:", "proj:")):
            emphasize[key] = p
    return suppress, emphasize, skills


def _context(user_id: UUID, job: JobDescription, kg: _KG, max_bullet_lines: int) -> Tuple[Context, Dict]:
    suppress, emphasize, skills = _hard_preferences(user_id, job.job_id)
    with Session(_db.engine) as session:
        rows = session.exec(select(UserJobResult).where(
            UserJobResult.user_id == user_id, UserJobResult.job_id == job.job_id)).all()
        latest = _db.latest_result(rows)
        matched = dict(latest.matched_skills or {}) if latest else {}
    weights = services.resolve_keyword_weights(job.job_id, user_id, job.description or "",
                                               persist=False)
    ctx = Context(
        jd_text=job.description or "",
        keyword_weights=weights or None,
        source_experiences=[{"title": r["record"]["title"], "company": r["record"]["company"],
                             "bullets": kg.source_bullets.get(r["key"], [])}
                            for r in kg.of("experience")],
        hard_suppress=set(suppress),
        # An emphasize naming something the KG lacks is refused by arbitration
        # (#129), so it cannot demand an item that does not exist.
        hard_emphasize={k for k in emphasize if k in kg.by_key},
        suppressed_skills=set(skills),
        matched_skills=matched,
        line_counter=_line_counter(),
        max_bullet_lines=max_bullet_lines,
    )
    return ctx, {"suppress": suppress, "emphasize": emphasize}


# ── nodes ────────────────────────────────────────────────────────────────────

def _key_of(section: str, item: Dict) -> str:
    return exp_key(item) if section == "experiences" else proj_key(item)


def _locate(content: Dict, key: str) -> Optional[Tuple[str, int]]:
    for section in ("experiences", "projects"):
        for i, item in enumerate(content.get(section) or []):
            if _key_of(section, item) == key:
                return section, i
    return None


def _refusal(node: Dict, content: Dict, kg: _KG, prefs: Dict, pref_ids: set) -> Optional[str]:
    """Why arbitration refuses this node, or None. Never executes anything."""
    key = node["item_key"].strip().lower()
    op = node["op"]
    if key not in kg.by_key:
        return f"unknown_key: {node['item_key']} is not in the knowledge graph"
    loc = _locate(content, key)
    if loc is None:
        return f"not_on_page: {node['item_key']} is not in the parent version"
    if op in ("keep", "revise") and key in prefs["suppress"]:
        return f"hard_preference: {prefs['suppress'][key].get('text')!r} suppresses {key}"
    if op in ("delete", "replace") and key in prefs["emphasize"]:
        return f"hard_preference: {prefs['emphasize'][key].get('text')!r} emphasizes {key}"
    if op == "replace":
        rep = node["replacement_key"].strip().lower()
        if rep not in kg.by_key:
            return f"unknown_key: {node['replacement_key']} is not in the knowledge graph"
        if _locate(content, rep) is not None:
            return f"already_on_page: {node['replacement_key']}"
        if rep in prefs["suppress"]:
            return f"hard_preference: {prefs['suppress'][rep].get('text')!r} suppresses {rep}"
    if op == "delete":
        section, _ = loc
        if len(content.get(section) or []) <= 1:
            return f"would_empty_section: {section}"
    for i, bullet in enumerate(node.get("bullets") or []):
        if not bullet["cites"]:
            return f"uncited_bullet: bullet {i} cites nothing"
        bad = [c for c in bullet["cites"] if not kg.resolves(c)]
        if bad:
            return f"unresolved_cite: bullet {i} cites {', '.join(bad)}"
    because = node.get("because") or ""
    if because.startswith("pref:") and because[5:].strip().lower() not in pref_ids:
        return f"unknown_preference: {because}"
    return None


def _apply(node: Dict, content: Dict, kg: _KG) -> Dict:
    out = copy.deepcopy(content)
    section, i = _locate(out, node["item_key"].strip().lower())
    items = out[section]
    if node["op"] == "delete":
        items.pop(i)
    elif node["op"] == "revise":
        items[i]["bullets"] = [b["text"] for b in node["bullets"]]
    elif node["op"] == "replace":
        new = kg.item(node["replacement_key"].strip().lower())
        if node.get("bullets"):
            new["bullets"] = [b["text"] for b in node["bullets"]]
        items[i] = new
    return out


def _section_rank(content: Dict, key: str) -> Tuple[int, int]:
    loc = _locate(content, key)
    if loc is None:
        return (2, 0)
    return (0 if loc[0] == "experiences" else 1, loc[1])


# ── finalize ─────────────────────────────────────────────────────────────────

def _finalize(content: Dict, program: Dict, ctx: Context, kg: _KG,
              lines: Optional[Dict[str, int]]) -> Tuple[List[Dict], List[Dict], Optional[Dict]]:
    fin = program["finalize"]
    violations: List[Dict] = []
    for v in preference_violations(content, ctx):
        violations.append({"check": "preferences", "detail": v,
                           "hint": "delete it" if v.startswith("suppressed:") else "restore it"})
    for section, kind in (("experiences", "experience"), ("projects", "project")):
        if kg.of(kind) and not content.get(section):
            violations.append({"check": "non_empty_sections", "detail": section,
                               "hint": f"keep at least one {kind}"})
    n_skills = len(content.get("skills_ranked") or [])
    floor = min(fin["min_skills"], len(kg.of("skill")))
    if n_skills > fin["max_skills"]:
        violations.append({"check": "skills_cap", "detail": f"{n_skills} > {fin['max_skills']}",
                           "hint": f"cut {n_skills - fin['max_skills']} skills"})
    if n_skills < floor:
        violations.append({"check": "skills_floor", "detail": f"{n_skills} < {floor}",
                           "hint": f"add {floor - n_skills} skills"})
    order = content.get("_section_order")
    if order is not None and sorted(order) != sorted(expected_sections(content)):
        violations.append({"check": "section_order", "detail": order,
                           "hint": f"use a permutation of {expected_sections(content)}"})
    if lines is not None:
        for v in bullet_line_violations(lines, fin["max_bullet_lines"]):
            violations.append({"check": "bullet_lines", "detail": v["bullet"],
                               "hint": f"shorten to {v['max']} lines (renders {v['lines']})"})
    budget = _page_budget(content, fin["line_budget"])
    cut_hints: List[Dict] = []
    if budget is not None and budget["over_by"] > 0:
        cut_hints = budget["cut_hints"]
        violations.append({"check": "line_budget",
                           "detail": f"{budget['lines_used']} lines > {budget['budget']}",
                           "hint": f"free {budget['over_by']} lines; see cut_hints"})
    return violations, cut_hints, budget


# ── entry points ─────────────────────────────────────────────────────────────

def _save_program(user_id: UUID, job_id: UUID, pid: str, program: Dict) -> None:
    with Session(_db.engine) as session:
        if session.get(PlanProgram, pid) is None:
            session.add(PlanProgram(program_id=pid, user_id=user_id, job_id=job_id,
                                    program=program))
            session.commit()


def execute_plan(user_id, program: Dict, *, dry_run: bool = False) -> Dict[str, Any]:
    """Run one program. Returns the result dict, or `{"error": ...}`."""
    user_id = tree._uuid(user_id)
    try:
        prog = Program.model_validate(program).model_dump(mode="json")
    except ValidationError as exc:
        return PlanError("invalid_program", str(exc)).as_dict()
    try:
        return _execute(user_id, prog, dry_run=dry_run)
    except PlanError as exc:
        return exc.as_dict()


def _execute(user_id: UUID, prog: Dict, *, dry_run: bool) -> Dict[str, Any]:
    try:
        job_id = UUID(prog["job_id"])
    except ValueError:
        raise PlanError("not_found", f"No job {prog['job_id']!r}.")
    with Session(_db.engine) as session:
        job = session.get(JobDescription, job_id)
        if job is None or job.user_id != user_id:
            raise PlanError("not_found", f"No job {prog['job_id']!r}.")
        session.expunge(job)
    pid = program_id(prog)
    _save_program(user_id, job_id, pid, prog)

    head = tree.get_head(user_id, job_id)["head"]
    head_id = head["node_id"] if head else None
    parent = (prog["parent"] or None)
    if parent != head_id:
        raise PlanError("stale_parent",
                        f"The plan builds on {parent}, but HEAD is {head_id}. Rebase with "
                        "patch_plan (replace /parent) after reading get_head.",
                        [head_id] if head_id else [])

    kg = _KG(user_id)
    ctx, prefs = _context(user_id, job, kg, prog["finalize"]["max_bullet_lines"])
    pref_ids = {str(p.get("preference_id")).lower()
                for p in services.load_preferences(user_id)}
    base = head["content"] if head else kg_default_content(user_id, job.description or "", kg)
    working = copy.deepcopy(base)
    base_vector = metric_vector(working, ctx)
    current = base_vector

    results = []
    order = sorted(prog["nodes"], key=lambda n: (_section_rank(base, n["item_key"].strip().lower()),
                                                 prog["nodes"].index(n)))
    for node in order:
        row = {"id": node["id"], "op": node["op"], "item_key": node["item_key"]}
        reason = _refusal(node, working, kg, prefs, pref_ids)
        if reason:
            results.append({**row, "status": "refused", "reason": reason})
            continue
        if node["op"] == "keep":
            results.append({**row, "status": "kept", "reason": None})
            continue
        candidate = _apply(node, working, kg)
        vector = metric_vector(candidate, ctx)
        verdict = accept(current, vector, improves=node["accept"]["improves"],
                         tolerances=node["accept"]["tolerances"],
                         requested=node["op"] == "delete" and bool(node.get("because")))
        key = node["item_key"].strip().lower()
        if (not verdict["accepted"] and node["op"] == "delete" and key in prefs["suppress"]
                and not verdict["gate_violations"]):
            # Compliance with a hard preference is itself a hard gate, which
            # outranks guards: finalize would refuse the page without this
            # delete, so a guard must not be able to veto it.
            verdict = {**verdict, "accepted": True,
                       "reason": f"hard_preference_override: {verdict['reason']}"}
        row.update(status="accepted" if verdict["accepted"] else "reverted",
                   reason=verdict["reason"], improved=verdict["improved"],
                   deltas=verdict["deltas"])
        results.append(row)
        if verdict["accepted"]:
            working, current = candidate, vector

    skill_notes = _apply_skills(working, prog.get("skills"), kg, ctx)
    if prog.get("section_order") is not None:
        working["_section_order"] = list(prog["section_order"])
    final_vector = metric_vector(working, ctx)
    lines = ctx.line_counter(working) if ctx.line_counter else None
    violations, cut_hints, budget = _finalize(working, prog, ctx, kg, lines)

    out = {
        "program_id": pid, "parent": parent, "dry_run": dry_run,
        "committed": False, "node_id": None,
        "nodes": results, "skills": skill_notes,
        "metrics": {"base": base_vector, "final": final_vector},
        "line_budget": budget or {"status": "unmeasured"},
        "violations": violations, "cut_hints": cut_hints,
    }
    if violations or dry_run:
        return out

    breakdown = ATSScoringEngine.score_tailored(
        working, ctx.jd_text, ctx.matched_skills, keyword_weights=ctx.keyword_weights)
    host = prog.get("host") or {}
    try:
        node = tree.commit_node(
            user_id, job_id, content=working, source="host", score_breakdown=breakdown,
            program=prog, metrics={"base": base_vector, "final": final_vector,
                                   "line_budget": budget, "nodes": results},
            provenance={"host": host.get("name"), "host_version": host.get("version"),
                        "model": host.get("model"), "art_version": ART_VERSION,
                        "program_id": pid},
            layout_overrides=(head or {}).get("layout_overrides"),
            note=f"plan {pid}", expected_parent=parent, materialize=True)
    except tree.StaleParent as exc:
        raise PlanError("stale_parent", str(exc), [str(exc.head)] if exc.head else [])
    return {**out, "committed": True, "node_id": node["node_id"]}


def _apply_skills(content: Dict, names: Optional[List[str]], kg: _KG, ctx: Context) -> Dict:
    """Set the skills list from the program, then drop hard-suppressed skills."""
    unknown: List[str] = []
    if names is not None:
        ranked = []
        seen = set()
        for name in names:
            rec = kg.skill(name)
            if rec is None:
                unknown.append(name)
                continue
            if rec["name"].lower() in seen:
                continue
            seen.add(rec["name"].lower())
            ranked.append({"name": rec["name"], "category": rec.get("category"), "score": None})
        content["skills_ranked"] = ranked
    before = content.get("skills_ranked") or []
    kept = [s for s in before if str(s.get("name", "")).strip().lower() not in ctx.suppressed_skills]
    content["skills_ranked"] = kept
    dropped = sorted(s["name"] for s in before if s not in kept)
    return {"unknown": unknown, "suppressed": dropped}


def patch_plan(user_id, program_id_: str, edits: List[Dict], *,
               dry_run: bool = False) -> Dict[str, Any]:
    """Apply JSON-pointer edits to a saved program and execute the result."""
    user_id = tree._uuid(user_id)
    with Session(_db.engine) as session:
        row = session.get(PlanProgram, program_id_)
        if row is None or row.user_id != user_id:
            return PlanError("not_found", f"No saved program {program_id_!r}.").as_dict()
        saved = dict(row.program)
    try:
        patched = apply_patch(saved, [{k: v for k, v in e.items() if v is not None or k == "value"}
                                      for e in edits])
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        return PlanError("invalid_patch", str(exc)).as_dict()
    return execute_plan(user_id, patched, dry_run=dry_run)
