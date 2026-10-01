"""The plan executor (issue #197): run a host's whole plan as one program.

`execute_plan` takes a `harness.program.Program` and does, in order
(docs/harness.md § 7):

1. **Base.** The parent node's content, which must be HEAD (`stale_parent`
   otherwise). A job with no history starts from a copy of its track baseline
   when one applies (#229, #199: the track Jev chooses among the saved ones,
   else the one named after its role family; skills re-ranked for this
   posting), else from `kg_default_content`: every
   experience and project with its source bullets, skills ranked against the
   posting, achievements and education verbatim.
2. **Arbitration.** A node that names a key the knowledge graph does not hold,
   cites something that does not resolve, names a variant that is not an
   approved one of the item (#229), or crosses a hard (strength-5) preference
   is *refused*: it never executes, and its reason is reported.
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
from agents.jd_payload import iter_requirements
from agents.skill_matching import match_requirement_terms, priority_skills
from agents.skill_scorer import MAX_SKILLS, rank_and_select_skills, score_skills
from database.models import JDProfile, JobDescription, PlanProgram, UserJobResult
from harness import ART_VERSION, library, tree
from harness.acceptance import (
    Context, accept, metric_vector, preference_violations, term_pattern,
)
from harness.decisions.coverage import (
    make_coverage_checker, node_detail as coverage_node_detail, summary as coverage_summary,
)
from harness.decisions.negative_pins import (
    make_pin_checker, reviews as pin_reviews, violations as pin_violations,
)
from harness.decisions.support import make_support_checker, reviews as support_reviews
from harness.ingest import apply_job_rules, list_rules, resolve_rules
from harness.program import Program, apply_patch, program_id
from harness.tools import _records, edu_key, skill_key

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
        self.tombstones = _tombstones(user_id)
        self.source_bullets: Dict[str, List[str]] = {}
        for r in self.records:
            if r["kind"] == "experience":
                self.source_bullets[r["key"]] = [b for b in r["record"].get("bullets") or [] if b]
            elif r["kind"] == "project":
                blurbs = [b["content"] for b in r["record"].get("blurbs") or [] if b.get("content")]
                desc = (r["record"].get("description") or "").strip()
                self.source_bullets[r["key"]] = blurbs or ([desc] if desc else [])
        # The bullet library (#229): every variant of this user by id (any status, so a
        # draft is refused for what it is), and the normalized text of the approved ones.
        self.variants = library.variants_by_id(user_id)
        self.approved_texts: Dict[str, set] = {}
        for v in self.variants.values():
            if v["status"] == library.APPROVED:
                self.approved_texts.setdefault(v["item_key"], set()).add(library.norm_text(v["text"]))

    def of(self, kind: str) -> List[Dict]:
        return [r for r in self.records if r["kind"] == kind]

    def item(self, key: str) -> Dict:
        """A page item for a KG experience/project, with its source bullets."""
        rec = self.by_key[key]["record"]
        bullets = list(self.source_bullets.get(key, []))
        # Each source bullet cites itself (#198), keyed by its text.
        cites = {b: [f"{key}#b{n}"] for n, b in enumerate(bullets)}
        if key.startswith("exp:"):
            return {"title": rec["title"], "company": rec["company"],
                    "start_date": "" if rec.get("start") == "?" else rec.get("start") or "",
                    "end_date": "" if rec.get("end") == "?" else rec.get("end") or "",
                    "bullets": bullets, "cites": cites}
        return {"name": rec["name"], "bullets": bullets, "cites": cites}

    def resolves(self, cite: str) -> bool:
        cite = (cite or "").strip().lower()
        if cite in self.by_key:
            return True
        key, sep, idx = cite.rpartition("#b")
        return bool(sep) and idx.isdigit() and int(idx) < len(self.source_bullets.get(key, []))

    def tombstoned(self, key: str) -> bool:
        """Whether `key` names an item the user deleted (and has not re-added)."""
        key = (key or "").strip().lower().rpartition("#b")[0] or (key or "").strip().lower()
        if key in self.by_key:
            return False
        return _matches_tombstone(key, self.tombstones)

    def cite_status(self, cite: str) -> Optional[str]:
        """None when `cite` resolves; else `tombstoned` or `unresolved` (#198)."""
        if self.resolves(cite):
            return None
        base = (cite or "").strip().lower()
        base = base.rpartition("#b")[0] if "#b" in base else base
        return "tombstoned" if self.tombstoned(base) else "unresolved"


    def skill(self, name: str) -> Optional[Dict]:
        r = self.by_key.get(skill_key({"name": name}))
        return r["record"] if r else None


def _tombstones(user_id: UUID) -> List[Tuple[str, str, Optional[str]]]:
    """The user's deletions as `(entity type, key_a, key_b)` (#92)."""
    from database.models import DeletedEntry

    with Session(_db.engine) as session:
        rows = session.exec(select(DeletedEntry).where(DeletedEntry.user_id == user_id)).all()
    return [(r.entity_type, r.key_a, r.key_b) for r in rows]


def _matches_tombstone(key: str, tombs) -> bool:
    """The deleted-row matchers the ingest path uses, so a tombstone catches the
    same variants (`agents/kg_store.KGStoreMixin`)."""
    from agents.kg_store import KGStoreMixin as M

    prefix, _, rest = key.partition(":")
    a, _, b = rest.partition("|")
    for kind, ka, kb in tombs:
        if prefix == "exp" and kind == "experience" and M._experiences_match(ka, kb, a, b):
            return True
        if prefix == "proj" and kind == "project" and M._projects_match(ka, kb, rest, None):
            return True
        if prefix == "edu" and kind == "education" and M._education_match(ka, kb, a, b):
            return True
    return False


def _int(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def job_requirements(user_id: UUID, job_id: UUID) -> List[Dict]:
    """The requirements stored on a job's JD profile, in source order."""
    with Session(_db.engine) as session:
        profile = session.exec(select(JDProfile).where(
            JDProfile.job_id == job_id, JDProfile.user_id == user_id)).first()
        return iter_requirements(profile.payload if profile else None)


def _prioritize_skills(ranked: List[Dict], skills: List[Dict], jd_text: str,
                       requirements: Sequence[Dict]) -> List[Dict]:
    """Skills the posting's requirements name, ahead of the TF-IDF order (#233).

    A skill matched by a required requirement comes first, then one matched by a
    preferred requirement (`agents.skill_matching.match_requirement_terms`: most
    critical first, then posting order); the rest keep their TF-IDF order. A
    matched skill the TF-IDF cap dropped is brought back, and the list is held to
    the skills cap. With no matches nothing changes."""
    names = priority_skills(match_requirement_terms(
        [s["name"] for s in skills], requirements)["matches"])
    if not names:
        return ranked
    have = {s["name"]: s for s in ranked}
    scores = None
    head: List[Dict] = []
    for name in names:
        if name in have:
            head.append(have[name])
            continue
        if scores is None:
            scores = {s["name"]: s for s in (score_skills(skills, jd_text or "") or [])}
        row = scores.get(name) or next(s for s in skills if s["name"] == name)
        head.append({"name": name, "category": row.get("category") or "Other",
                     "score": row.get("score", 0.0)})
    chosen = set(names)
    return (head + [s for s in ranked if s["name"] not in chosen])[:MAX_SKILLS]


def _kg_skill_rows(kg: _KG) -> List[Dict]:
    return [{"name": s["record"]["name"], "category": s["record"].get("category"),
             "proficiency": _int(s["record"].get("proficiency"))} for s in kg.of("skill")]


def _rank_skills(pool: List[Dict], all_skills: List[Dict], jd_text: str,
                 requirements: Optional[Sequence[Dict]]) -> List[Dict]:
    """`pool` ranked against the posting (TF-IDF, capped), then, with the posting's
    `requirements`, skills they name brought to the front from `all_skills` (#233)."""
    ranked = rank_and_select_skills(pool, jd_text or "") if pool else None
    if not ranked:
        ranked = [{"name": s["name"], "category": s["category"], "score": 0.0}
                  for s in sorted(pool, key=lambda s: s["name"].lower())]
    if requirements and pool:
        ranked = _prioritize_skills(ranked, all_skills, jd_text, requirements)
    return ranked


def kg_default_content(user_id: UUID, jd_text: str, kg: Optional[_KG] = None,
                       requirements: Optional[Sequence[Dict]] = None) -> Dict:
    """The version a job with no history starts from: the whole KG, untailored
    except for skills ranked against the posting. With the posting's
    `requirements`, skills they name rank first (#233); without them the
    ranking is exactly the TF-IDF one."""
    kg = kg or _KG(user_id)
    skills = _kg_skill_rows(kg)
    ranked = _rank_skills(skills, skills, jd_text, requirements)
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


_RULE_SECTIONS = {"edu": ("education", edu_key), "exp": ("experiences", exp_key)}


def baseline_content(user_id: UUID, jd_text: str, kg: _KG, requirements: Optional[Sequence[Dict]],
                     node_content: Dict) -> Dict:
    """A job's first version when a track baseline applies (#229): a copy of the baseline
    node's content, adjusted to this posting in the two places a baseline from another job
    would be wrong.

    - **Skills** are re-ranked against this posting with the same ranking the KG default
      uses, over the skills the baseline lists that the KG still holds, so the track's
      skill set carries over and another job's skill order does not. Skills the posting's
      requirements name come to the front (#233), as they do for the default. A baseline
      with no skills the KG still has takes the default list.
    - **Job-scoped rule fields** (#192) go back to the stored KG value, so a value another
      job's answer wrote (a graduation date) does not leak; the answers for *this* posting
      are applied right after, as for any base.

    Item content (bullets, cites, titles) stays exactly as the baseline has it.
    """
    default = kg_default_content(user_id, jd_text, kg, requirements)
    content = copy.deepcopy(node_content)
    held = {str(s.get("name", "")).strip().lower() for s in content.get("skills_ranked") or []}
    all_skills = _kg_skill_rows(kg)
    pool = [s for s in all_skills if s["name"].strip().lower() in held]
    content["skills_ranked"] = (_rank_skills(pool, all_skills, jd_text, requirements)
                                if pool else default["skills_ranked"])
    for rule in list_rules(user_id):
        section, key_fn = _RULE_SECTIONS[rule["item_key"].split(":", 1)[0]]
        stored = {key_fn(i): i for i in default.get(section) or []}.get(rule["item_key"])
        for item in content.get(section) or []:
            if key_fn(item) == rule["item_key"] and stored is not None and rule["field"] in stored:
                item[rule["field"]] = stored[rule["field"]]
    return content


def start_content(user_id: UUID, job: JobDescription, kg: _KG,
                  requirements: Optional[Sequence[Dict]]) -> Tuple[Dict, Optional[Dict]]:
    """`(content, baseline)` for a job with no history: a copy of its track baseline when
    one applies (`baseline` is `{track, node_id, role_family, family_source, source, p}`), else the whole KG
    (`kg_default_content`, exactly as before #229) and `None`."""
    choice = library.choose_baseline(user_id, job.job_id)
    if choice is not None:
        try:
            node = tree.get_node(user_id, choice["node_id"])
        except tree.NotFound:
            node = None
        if node and node["content"]:
            return baseline_content(user_id, job.description or "", kg, requirements,
                                    node["content"]), choice
    return kg_default_content(user_id, job.description or "", kg, requirements), None


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


def _negative_terms(skills: Dict[str, Dict]) -> Dict[str, Dict]:
    """Negative pins (#198): a hard suppression by term, not by key, names a
    fact that must never render, in bullets as well as skills."""
    return {t: p for t, p in skills.items()
            if not (p.get("target_key") or "").strip().lower().startswith("skill:")}


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
        negative_terms=set(_negative_terms(skills)),
        source_bullets=kg.source_bullets,
        cite_status=kg.cite_status,
        matched_skills=matched,
        line_counter=_line_counter(),
        max_bullet_lines=max_bullet_lines,
        approved_variants=kg.approved_texts,
    )
    return ctx, {"suppress": suppress, "emphasize": emphasize,
                 "negative_terms": _negative_terms(skills)}


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
        if kg.tombstoned(key):
            return f"tombstoned: the user deleted {node['item_key']}"
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
            if kg.tombstoned(rep):
                return f"tombstoned: the user deleted {node['replacement_key']}"
            return f"unknown_key: {node['replacement_key']} is not in the knowledge graph"
        if _locate(content, rep) is not None:
            return f"already_on_page: {node['replacement_key']}"
        if rep in prefs["suppress"]:
            return f"hard_preference: {prefs['suppress'][rep].get('text')!r} suppresses {rep}"
    if op == "delete":
        section, _ = loc
        if len(content.get(section) or []) <= 1:
            return f"would_empty_section: {section}"
    target = (node["replacement_key"] if op == "replace" else node["item_key"]).strip().lower()
    for i, bullet in enumerate(node.get("bullets") or []):
        named = (bullet.get("from_variant") or "").strip().lower()
        if named:
            # Only an approved variant of this very item can be started from (#229).
            variant = kg.variants.get(named)
            if variant is None:
                return f"unknown_variant: bullet {i} names {bullet['from_variant']}, which is not one of your variants"
            if variant["status"] != library.APPROVED:
                return (f"variant_not_approved: bullet {i} names a {variant['status']} variant; "
                        "only approved variants can be started from (approve_variant)")
            if variant["item_key"] != target:
                return (f"variant_wrong_item: bullet {i} names a variant of {variant['item_key']}, "
                        f"not {target}")
        if not bullet["cites"]:
            return f"uncited_bullet: bullet {i} cites nothing"
        gone = [c for c in bullet["cites"] if kg.cite_status(c) == "tombstoned"]
        if gone:
            return (f"tombstoned_cite: bullet {i} cites {', '.join(gone)}, which the user "
                    "deleted")
        bad = [c for c in bullet["cites"] if not kg.resolves(c)]
        if bad:
            return f"unresolved_cite: bullet {i} cites {', '.join(bad)}"
        for term, pref in sorted(prefs.get("negative_terms", {}).items()):
            if term_pattern(term).search(bullet["text"].lower()):
                return (f"negative_pin: bullet {i} mentions {term!r}, which "
                        f"{pref.get('text')!r} says must never appear")
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
        items[i]["cites"] = _cites_of(node["bullets"])
    elif node["op"] == "replace":
        new = kg.item(node["replacement_key"].strip().lower())
        if node.get("bullets"):
            new["bullets"] = [b["text"] for b in node["bullets"]]
            new["cites"] = _cites_of(node["bullets"])
        items[i] = new
    return out


def _cites_of(bullets: List[Dict]) -> Dict[str, List[str]]:
    """What each written bullet rests on, keyed by its text (#198)."""
    out: Dict[str, List[str]] = {}
    for b in bullets:
        out.setdefault(b["text"], [])
        out[b["text"]] += [c for c in b["cites"] if c not in out[b["text"]]]
    return out


def _target_key(node: Dict) -> str:
    """The item a node's bullets will sit under: the replacement for a replace."""
    return (node["replacement_key"] if node["op"] == "replace" else node["item_key"]).strip().lower()


def _with_variant_cites(node: Dict, kg: _KG) -> Dict:
    """The node with each uncited bullet that names an approved variant, or is verbatim
    one of its item's approved variants, citing what that variant cites (#229). The saved
    program is left as the host wrote it; only what arbitration and `_apply` see changes."""
    if not node.get("bullets"):
        return node
    key = _target_key(node)
    by_text = {library.norm_text(v["text"]): v for v in kg.variants.values()
               if v["item_key"] == key and v["status"] == library.APPROVED}
    out = copy.deepcopy(node)
    for b in out["bullets"]:
        if b["cites"]:
            continue
        v = kg.variants.get((b.get("from_variant") or "").strip().lower()) \
            or by_text.get(library.norm_text(b["text"]))
        if v is not None and v["status"] == library.APPROVED:
            b["cites"] = list(v["cites"])
    return out


def _variant_origin(node: Dict, kg: _KG) -> Dict[Tuple[str, str], str]:
    """`(item key, normalized bullet) -> variant text` for the node's bullets that name an
    approved variant: what the `variant_drift` guard measures."""
    key = _target_key(node)
    out = {}
    for b in node.get("bullets") or []:
        v = kg.variants.get((b.get("from_variant") or "").strip().lower())
        if v is not None and v["status"] == library.APPROVED:
            out[(key, library.norm_text(b["text"]))] = v["text"]
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
        if v.startswith("negative_pin:"):
            term, _, where = v[len("negative_pin:"):].partition("@")
            hint = f"revise {where.split(' :: ', 1)[0]} so it no longer mentions {term!r}"
        else:
            hint = "delete it" if v.startswith("suppressed:") else "restore it"
        violations.append({"check": "preferences", "detail": v, "hint": hint})
    if ctx.pin_page_checker is not None:
        # Jev over the whole page (#232): a reworded mention in text the plan never touched.
        listed = {v["detail"] for v in violations}
        for v in pin_violations(ctx.pin_page_checker(content)):
            if v not in listed:
                term, _, where = v[len("negative_pin:"):].partition("@")
                violations.append({"check": "preferences", "detail": v,
                                   "hint": f"revise {where.split(' :: ', 1)[0]} so it no longer mentions {term!r}"})
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
    requirements = job_requirements(user_id, job_id)
    # No history: the job's track baseline when one applies (#229), else the whole KG.
    baseline = None
    if head:
        base = head["content"]
    else:
        base, baseline = start_content(user_id, job, kg, requirements)
    # Job-scoped rules answered for this posting (#192), e.g. the graduation
    # date a post-internship enrollment requirement calls for.
    rules_applied = apply_job_rules(base, resolve_rules(user_id, job_id))
    working = copy.deepcopy(base)
    # The cited-bullet support check (#193): Jev, through the cached engine.
    # Built here, not in `_context`, because it reads the base for "original".
    # A bullet that names an approved variant gets that variant's text as extra evidence (#229),
    # in this dict, shared with the checker and `ctx`: the accepted nodes' variant bullets plus,
    # while a node is evaluated, its own.
    evidence: Dict[Tuple[str, str], str] = {}
    ctx.variant_evidence = evidence
    ctx.support_checker = make_support_checker(kg.source_bullets, base, kg.approved_texts, evidence)
    # The negative-pin check (#232): Jev on whether a changed bullet or item field mentions a
    # pinned topic in other words. The pin's own statement describes the topic.
    pins = [{"term": t, "statement": p.get("text")} for t, p in sorted(prefs["negative_terms"].items())]
    ctx.pin_checker = make_pin_checker(pins, base) if pins else None
    # Finalize checks the whole page, so a paraphrase already in the base cannot render.
    ctx.pin_page_checker = make_pin_checker(pins, None) if pins else None
    # Semantic requirement coverage (#126): Jev's yes/no per (bullet, requirement) over the
    # posting's required and preferred requirements, a target beside the literal `coverage`.
    ctx.coverage_checker = make_coverage_checker(requirements)
    base_vector = metric_vector(working, ctx)
    current = base_vector

    results = []
    accepted_origin: Dict[Tuple[str, str], str] = {}
    order = sorted(prog["nodes"], key=lambda n: (_section_rank(base, n["item_key"].strip().lower()),
                                                 prog["nodes"].index(n)))
    for node in order:
        row = {"id": node["id"], "op": node["op"], "item_key": node["item_key"]}
        node = _with_variant_cites(node, kg)
        reason = _refusal(node, working, kg, prefs, pref_ids)
        if reason:
            results.append({**row, "status": "refused", "reason": reason})
            continue
        if node["op"] == "keep":
            results.append({**row, "status": "kept", "reason": None})
            continue
        candidate = _apply(node, working, kg)
        # variant_drift is a property of this node's bullets alone (#229).
        origin = _variant_origin(node, kg)
        ctx.variant_origin = origin
        evidence.update(origin)
        vector = metric_vector(candidate, ctx)
        ctx.variant_origin = {}
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
        if ctx.coverage_checker:
            # What this node did to the requirements' semantic coverage (#126), compactly.
            detail = coverage_node_detail(ctx.coverage_checker(working),
                                          ctx.coverage_checker(candidate), candidate)
            if detail:
                row["semantic"] = detail
        touched = {key, (node.get("replacement_key") or "").strip().lower()}
        review = support_reviews([f for f in ctx.support_checker(candidate)
                                  if f["item"] in touched])
        if ctx.pin_checker:
            review += pin_reviews([f for f in ctx.pin_checker(candidate) if f["item"] in touched])
        if review:
            row["review"] = review
        results.append(row)
        if verdict["accepted"]:
            working, current = candidate, vector
            accepted_origin.update(origin)
        evidence.clear()
        evidence.update(accepted_origin)

    skill_notes = _apply_skills(working, prog.get("skills"), kg, ctx)
    if prog.get("section_order") is not None:
        working["_section_order"] = list(prog["section_order"])
    ctx.variant_origin = accepted_origin
    final_vector = metric_vector(working, ctx)
    ctx.variant_origin = {}
    lines = ctx.line_counter(working) if ctx.line_counter else None
    violations, cut_hints, budget = _finalize(working, prog, ctx, kg, lines)

    out = {
        "program_id": pid, "parent": parent, "dry_run": dry_run,
        "committed": False, "node_id": None,
        "nodes": results, "skills": skill_notes,
        "metrics": {"base": base_vector, "final": final_vector},
        "line_budget": budget or {"status": "unmeasured"},
        "violations": violations, "cut_hints": cut_hints, "rules_applied": rules_applied,
    }
    if baseline:
        out["baseline"] = baseline
    # Only when Jev (or its cache) answered for at least one bullet: with no key
    # the result is what it was before #193.
    findings = [f for f in ctx.support_checker(working) if f["status"] == "checked"]
    if findings:
        out["support"] = {"checked": len(findings), "review": support_reviews(findings)}
    pin_findings = ([f for f in ctx.pin_page_checker(working) if f["status"] == "checked"]
                    if ctx.pin_page_checker else [])
    if pin_findings:
        out["negative_pins"] = {"checked": len(pin_findings), "review": pin_reviews(pin_findings)}
    # Only when Jev (or its cache) answered: with no key the result is what it was before #126.
    semantic = coverage_summary(ctx.coverage_checker(working), working) if ctx.coverage_checker else None
    if semantic:
        out["semantic_coverage"] = semantic
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
                        "program_id": pid,
                        # The track baseline this job's first version was copied from (#229).
                        **({"baseline": baseline} if baseline else {})},
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
