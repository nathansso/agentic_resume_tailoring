"""Host-filled ingestion and jobs (issue #192).

On the harness path the host's own model reads the resume, repos and posting.
ART hands it the schema to fill (`ingest_schema`), then validates, deduplicates
and stores what comes back:

- `upsert_items` stores KG records through `agents.kg_store.KGStoreMixin`, the
  same save, merge, heal and tombstone code the resume parser runs, so a
  host-filled payload lands as the same rows the parser would write. A
  `manually_edited` row is never changed. `correct: true` overwrites a stale
  field on a matched row (the #189 run found an ended role still reading
  "Present") instead of only filling blanks.
- `open_job` stores the posting, compiles the host's requirements into a
  `JDProfile` with the parser's own pure compile and edit-preserving merge,
  computes keyword weights, and resolves the user's **job-scoped rules**: yes/no
  questions about a posting that change a profile field (a graduation date that
  depends on whether the role requires enrollment after it). The host answers
  them today; Jev will (#193). An unanswered rule comes back `needs_answer`.
- `apply_job_rules` writes the resolved values into a job's content; the
  executor calls it on every run.

Every function returns data, never raises for bad input: invalid records and
requirements come back as structured errors. Model-free by rule
(`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple
from types import SimpleNamespace
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlmodel import Session, select

import database.db as _db
import services
from agents.checks import exp_key, proj_key
from agents.extraction_schemas import (
    AchievementItem, EducationItem, ExperienceItem, JDProfileExtraction, JDRequirementItem,
    ProjectItem, SkillItem,
)
from agents.jd_payload import (
    PROFILE_VERSION, compile_profile_payload, extraction_key, iter_requirements, merge_edits,
    payload_digest, text_digest,
)
from agents.kg_store import KGStoreMixin, _clean_date
from agents.skill_matching import match_requirement_terms
from agents.skill_postprocessor import normalize_skill_name, postprocess_skills
from database.models import (
    Achievement, Education, Experience, JDProfile, JobDescription, JobHead, JobRule, Project,
    Skill, UserSkill,
)
from harness import library
from harness.tools import KINDS, _records, ach_key, edu_key, skill_key

log = logging.getLogger(__name__)

APPLICATION_STATUSES = ("drafting", "applied", "interview", "closed")
DEFAULT_SOURCE = "host"
TOP_TERMS = 15


# ── schemas ──────────────────────────────────────────────────────────────────

def _strict(model: type) -> type:
    """The extraction schema, refusing unknown fields so a host's typo surfaces."""
    return type(model.__name__, (model,), {"model_config": ConfigDict(extra="forbid")})


RULE_FIELDS = {"education": ("degree", "start_date", "end_date"),
               "experience": ("title", "company", "start_date", "end_date")}


class RuleItem(BaseModel):
    """A job-scoped rule: a field whose value depends on a yes/no question about
    the posting. Record one when the user states such a rule."""
    model_config = ConfigDict(extra="forbid")
    item_key: str = Field(description="Key of an education or experience item "
                                      "(from list_items), e.g. 'edu:<institution>|<degree>'.")
    field: Literal["degree", "title", "company", "start_date", "end_date"] = Field(
        description="The field the rule sets on that item.")
    question: str = Field(description="A yes/no question about a posting, e.g. 'Does the "
                                      "posting require enrollment after the internship ends?'")
    value_if_yes: str
    value_if_no: Optional[str] = Field(None, description="Omit to leave the stored value.")


class VariantTags(BaseModel):
    model_config = ConfigDict(extra="forbid")
    track: Optional[str] = Field(None, description="The track the wording was written for, "
                                                   "e.g. data_science (see save_baseline).")
    job_id: Optional[str] = Field(None, description="The job it was written for, if any.")


class VariantItem(BaseModel):
    """One approved phrasing of one experience or project bullet: wording the user
    wrote, kept in the bullet library and started from instead of regenerated."""
    model_config = ConfigDict(extra="forbid")
    item_key: str = Field(description="Key of an experience or project (from list_items), "
                                      "e.g. 'exp:<title>|<company>' or 'proj:<name>'.")
    text: str = Field(description="The bullet exactly as the user approved it.")
    cites: List[str] = Field(default_factory=list, description=(
        "Evidence ids the bullet rests on: an item key, or '<item key>#b<n>' for a source "
        "bullet. Omit to cite the item itself."))
    tags: Optional[VariantTags] = None


SCHEMAS: Dict[str, type] = {
    "experience": _strict(ExperienceItem),
    "education": _strict(EducationItem),
    "project": _strict(ProjectItem),
    "skill": _strict(SkillItem),
    "achievement": _strict(AchievementItem),
    "requirement": _strict(JDRequirementItem),
    "rule": RuleItem,
    "variant": VariantItem,
}
SCHEMA_KINDS = tuple(SCHEMAS)

# The field a record must carry to be stored at all.
_IDENTITY = {"experience": "company", "education": "institution", "project": "name",
             "skill": "name", "achievement": "title"}

_NOTES = {
    "experience": "One role. Bullets verbatim from the source. Dedup is by company plus title.",
    "education": "One degree. end_date is the graduation date. Dedup is by institution plus degree.",
    "project": "One project. Dedup is by repo_url, else name.",
    "skill": "One skill. Names are normalized and deduplicated against the stored skills.",
    "achievement": "One award or honor. Dedup is by title.",
    "requirement": "One atomic requirement from a posting, for open_job.",
    "rule": "A job-scoped rule. open_job asks for its answer per posting.",
    "variant": "One approved bullet for an experience or project, from the user's own curated "
               "library. Arrives approved. Dedup is by item_key plus the bullet's words.",
}
_REQUIRED = {"variant": ["item_key", "text"]}


def ingest_schema(kind: str) -> Dict[str, Any]:
    """The JSON schema of the record the host fills for `kind`."""
    model = SCHEMAS.get(kind)
    if model is None:
        return {"kind": kind, "error": {
            "code": "unknown_kind", "message": f"No schema for {kind!r}.",
            "suggestions": list(SCHEMA_KINDS)}}
    return {"kind": kind, "json_schema": model.model_json_schema(),
            "required": [_IDENTITY[kind]] if kind in _IDENTITY else _REQUIRED.get(kind, []),
            "note": _NOTES[kind]}


def _errors(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}"
                     for e in exc.errors())


# ── upsert_items ─────────────────────────────────────────────────────────────

class _Store(KGStoreMixin):
    def __init__(self, user_id: UUID):
        self.user = SimpleNamespace(user_id=user_id)


_KEY_OF = {"experience": exp_key, "project": proj_key, "education": edu_key,
           "skill": skill_key, "achievement": ach_key}
# The parser's save order (`ResumeParserAgent.parse_and_save`).
_SAVE_ORDER = ("experience", "education", "achievement", "project", "skill")

_MODEL = {"experience": Experience, "education": Education, "project": Project,
          "achievement": Achievement}
# Fields `correct` may overwrite: everything but the identity the match was made on.
_CORRECTABLE = {
    "experience": ("start_date", "end_date", "description", "bullets"),
    "education": ("degree", "location", "start_date", "end_date", "gpa"),
    "project": ("description", "repo_url", "demo_url", "start_date", "end_date"),
    "achievement": ("description", "issuer", "date"),
}


def _row_key(kind: str, row: Optional[Dict]) -> Optional[str]:
    if row is None:
        return None
    if kind == "education" and not row.get("degree"):
        row = dict(row, degree=None)
    return _KEY_OF[kind](row)


def _matches(kind: str, row, data: Dict) -> bool:
    m = KGStoreMixin
    if kind == "experience":
        return m._experiences_match(row.title, row.company,
                                    data.get("title") or "Unknown", data.get("company"))
    if kind == "education":
        return m._education_match(row.institution, row.degree,
                                  data.get("institution"), data.get("degree") or "")
    if kind == "project":
        return m._projects_match(row.name, row.repo_url, data.get("name"), data.get("repo_url"))
    return m._achievements_match(row.title, row.issuer, data.get("title"), data.get("issuer"))


def _correct(user_id: UUID, kind: str, data: Dict) -> Optional[Tuple[str, Dict, str]]:
    """Overwrite the matched row's given fields. None when nothing matches, so
    the record is stored like any other."""
    model = _MODEL[kind]
    with Session(_db.engine) as session:
        rows = session.exec(select(model).where(model.user_id == user_id)).all()
        row = next((r for r in rows if _matches(kind, r, data)), None)
        if row is None:
            return None
        if getattr(row, "manually_edited", False):
            return ("skipped_manual", row.model_dump(),
                    "The user edited this item by hand; ask them to change it in the editor.")
        changed = []
        for field in _CORRECTABLE[kind]:
            if field not in data:
                continue
            value = data[field]
            if field in ("start_date", "end_date"):
                value = _clean_date(value)
            elif field == "gpa" and value is not None:
                value = str(value).strip() or None
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed.append(field)
        if changed:
            row.updated_at = datetime.utcnow()
            session.add(row)
            session.commit()
            session.refresh(row)
        return ("corrected" if changed else "unchanged", row.model_dump(),
                f"Set {', '.join(changed)}." if changed else None)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9+#]+", " ", (name or "").lower())).strip()


def _existing_skill_names(user_id: UUID) -> Dict[str, str]:
    """This user's skill names by normalized form, so 'Time-Series Analysis'
    lands on a stored 'Time series analysis' instead of beside it (#189)."""
    with Session(_db.engine) as session:
        names = session.exec(select(Skill.name).join(UserSkill, UserSkill.skill_id == Skill.skill_id)
                             .where(UserSkill.user_id == user_id)).all()
    return {_norm(n): n for n in sorted(set(names))}


def _save_skills(store: _Store, user_id: UUID, items: List[Tuple[int, Dict]], source: str,
                 out: Dict[int, Dict]) -> None:
    known = _existing_skill_names(user_id)
    batch, group_of = [], {}
    for idx, data in items:
        cleaned = postprocess_skills([data])
        if not cleaned:
            out[idx] = {"status": "filtered", "key": None,
                        "message": "Not stored: reads as noise, not a skill."}
            continue
        name = cleaned[0]["name"]
        name = known.get(_norm(name), name)
        group = normalize_skill_name(name).lower()
        if group in group_of:
            group_of[group].append(idx)
        else:
            group_of[group] = [idx]
            batch.append(dict(data, name=name))
    saved = postprocess_skills(batch)
    outcomes = store._save_skills(saved, source) or []
    by_group = {normalize_skill_name(s["name"]).lower(): o for s, o in zip(saved, outcomes)}
    for group, idxs in group_of.items():
        status, row = by_group.get(group, ("skipped_empty", None))
        for n, idx in enumerate(idxs):
            out[idx] = {"status": status if n == 0 else "merged",
                        "key": skill_key(row) if row else None, "message": None}


def _upsert_rule(user_id: UUID, data: Dict) -> Dict:
    key = (data["item_key"] or "").strip().lower()
    kind = {"edu": "education", "exp": "experience"}.get(key.split(":", 1)[0])
    if kind is None or data["field"] not in RULE_FIELDS[kind]:
        allowed = {k: list(v) for k, v in RULE_FIELDS.items()}
        return {"status": "invalid", "key": None,
                "message": f"A rule targets an education or experience field: {allowed}."}
    if key not in {r["key"] for r in _records(user_id, [kind])}:
        return {"status": "invalid", "key": None,
                "message": f"No {kind} with key {key!r}; see list_items."}
    question = " ".join(data["question"].split())
    with Session(_db.engine) as session:
        rules = session.exec(select(JobRule).where(JobRule.user_id == user_id,
                                                   JobRule.item_key == key,
                                                   JobRule.field == data["field"])).all()
        rule = next((r for r in rules if r.question.lower() == question.lower()), None)
        if rule is None:
            rule = JobRule(user_id=user_id, item_key=key, field=data["field"], question=question,
                           value_if_yes=data["value_if_yes"], value_if_no=data.get("value_if_no"))
            status = "created"
        elif (rule.value_if_yes, rule.value_if_no) != (data["value_if_yes"], data.get("value_if_no")):
            rule.value_if_yes, rule.value_if_no = data["value_if_yes"], data.get("value_if_no")
            rule.updated_at = datetime.utcnow()
            status = "merged"
        else:
            return {"status": "unchanged", "key": f"rule:{rule.rule_id}", "message": None}
        session.add(rule)
        session.commit()
        return {"status": status, "key": f"rule:{rule.rule_id}", "message": None}


def upsert_items(user_id: UUID, records: Sequence[Dict], source: str = DEFAULT_SOURCE) -> Dict:
    """Validate and store host-filled records. One result per record, in order."""
    out: Dict[int, Dict] = {}
    pending: Dict[str, List[Tuple[int, Dict]]] = {k: [] for k in _SAVE_ORDER}
    variants: List[Tuple[int, Dict]] = []
    for idx, rec in enumerate(records):
        kind, raw = rec.get("kind"), rec.get("data") or {}
        model = SCHEMAS.get(kind)
        if model is None or kind == "requirement":
            out[idx] = {"status": "invalid", "key": None,
                        "message": f"Unknown kind {kind!r}; one of {', '.join(KINDS)}, rule, variant."}
            continue
        try:
            data = model.model_validate(raw).model_dump(exclude_none=True)
        except ValidationError as exc:
            out[idx] = {"status": "invalid", "key": None, "message": _errors(exc)}
            continue
        if kind == "rule":
            out[idx] = _upsert_rule(user_id, data)
            continue
        if kind == "variant":
            variants.append((idx, data))     # after the KG records, so one call can send both
            continue
        ident = _IDENTITY[kind]
        if not str(data.get(ident) or "").strip():
            out[idx] = {"status": "invalid", "key": None, "message": f"{ident} is required."}
            continue
        if rec.get("correct") and kind in _CORRECTABLE:
            done = _correct(user_id, kind, data)
            if done is not None:
                status, row, message = done
                out[idx] = {"status": status, "key": _row_key(kind, row), "message": message}
                continue
        pending[kind].append((idx, data))

    store = _Store(user_id)
    savers = {"experience": lambda d: store._save_experiences(d, source),
              "education": lambda d: store._save_education(d, source),
              "achievement": lambda d: store._save_achievements(d, source),
              "project": lambda d: store._save_projects(d, source)}
    touched = False
    for kind in _SAVE_ORDER:
        items = pending[kind]
        if not items:
            continue
        touched = True
        if kind == "skill":
            _save_skills(store, user_id, items, source, out)
            continue
        outcomes = savers[kind]([d for _, d in items]) or []
        for (idx, _), (status, row) in zip(items, outcomes):
            message = {"skipped_manual": "The user edited this item by hand; it was left as is.",
                       "skipped_tombstone": "The user deleted this item; it was not re-added.",
                       "skipped_empty": "Not stored: no dates, description or bullets, and "
                                        "no full title and company."}.get(status)
            out[idx] = {"status": status, "key": _row_key(kind, row), "message": message}
    if touched:
        # The parser's self-heal, in the same order, so the stored rows match.
        with Session(_db.engine) as session:
            store._heal_experiences(session, user_id)
            store._heal_projects(session, user_id)
            store._heal_education(session, user_id)
            store._heal_achievements(session, user_id)
            session.commit()
    if variants:
        out.update(library.import_variants(user_id, variants))

    results =[{"index": i, "kind": records[i].get("kind"), **out[i]} for i in range(len(records))]
    counts: Dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {"results": results, "counts": counts}


# ── job-scoped rules ─────────────────────────────────────────────────────────

def list_rules(user_id: UUID) -> List[Dict]:
    with Session(_db.engine) as session:
        rows = session.exec(select(JobRule).where(JobRule.user_id == user_id)).all()
    return [{"rule_id": str(r.rule_id), "item_key": r.item_key, "field": r.field,
             "question": r.question, "value_if_yes": r.value_if_yes,
             "value_if_no": r.value_if_no}
            for r in sorted(rows, key=lambda r: (r.created_at, str(r.rule_id)))]


def _eligibility(user_id: UUID, job_id: UUID) -> Dict[str, Dict]:
    with Session(_db.engine) as session:
        profile = session.exec(select(JDProfile).where(JDProfile.job_id == job_id,
                                                       JDProfile.user_id == user_id)).first()
        return dict(profile.eligibility or {}) if profile else {}


def resolve_rules(user_id: UUID, job_id: UUID) -> List[Dict]:
    """Every rule with this posting's answer and the value it resolves to."""
    answers = _eligibility(user_id, job_id)
    out = []
    for rule in list_rules(user_id):
        ans = answers.get(rule["rule_id"])
        if ans is None:
            out.append({**rule, "status": "needs_answer", "answer": None, "value": None})
            continue
        value = rule["value_if_yes"] if ans["answer"] else rule["value_if_no"]
        out.append({**rule, "status": "answered", "answer": ans["answer"],
                    "quote": ans.get("quote"), "value": value})
    return out


def apply_job_rules(content: Dict, rules: Sequence[Dict]) -> List[Dict]:
    """Write each answered rule's value into its item in `content`, in place.
    Idempotent. Returns what changed."""
    sections = {"edu": ("education", edu_key), "exp": ("experiences", exp_key)}
    applied = []
    for rule in rules:
        if rule.get("status") != "answered" or rule.get("value") is None:
            continue
        section, key_fn = sections[rule["item_key"].split(":", 1)[0]]
        for item in content.get(section) or []:
            if key_fn(item) != rule["item_key"]:
                continue
            if item.get(rule["field"]) != rule["value"]:
                applied.append({"rule_id": rule["rule_id"], "item_key": rule["item_key"],
                                "field": rule["field"], "from": item.get(rule["field"]),
                                "to": rule["value"]})
                item[rule["field"]] = rule["value"]
    return applied


# ── open_job ─────────────────────────────────────────────────────────────────

def _find_job(session: Session, user_id: UUID, job_id: Optional[str], title: str,
              company: str, jd_text: str) -> Tuple[Optional[JobDescription], Optional[Dict]]:
    if job_id:
        try:
            job = session.get(JobDescription, UUID(str(job_id)))
        except ValueError:
            job = None
        if job is None or job.user_id != user_id:
            return None, {"code": "not_found", "message": f"No job {job_id!r}."}
        return job, None
    digest = text_digest(jd_text, 0)
    for job in session.exec(select(JobDescription).where(JobDescription.user_id == user_id)).all():
        if (job.title.strip().lower() == title.strip().lower()
                and job.company.strip().lower() == company.strip().lower()
                and text_digest(job.description or "", 0) == digest):
            return job, None
    return None, None


def open_job(user_id: UUID, jd_text: str, requirements: Sequence[Dict], metadata: Dict,
             rule_answers: Optional[Sequence[Dict]] = None,
             job_id: Optional[str] = None) -> Dict[str, Any]:
    """Store a posting and its host-extracted requirements; resolve the user's
    job-scoped rules against it. Re-opening the same posting (or passing
    `job_id`) updates that job instead of creating another."""
    title = (metadata.get("title") or "").strip()
    company = (metadata.get("company") or "").strip()
    supplied_family = str(metadata.get("role_family") or "").strip().lower() or None
    if supplied_family and supplied_family not in library.ROLE_FAMILIES:
        return {"error": {"code": "invalid_arguments",
                          "message": f"metadata.role_family {supplied_family!r} is not a role family.",
                          "suggestions": list(library.ROLE_FAMILIES)}}
    schema_errors: List[Dict] = []
    reqs: List[JDRequirementItem] = []
    for i, raw in enumerate(requirements or []):
        try:
            item = SCHEMAS["requirement"].model_validate(raw)
        except ValidationError as exc:
            schema_errors.append({"where": f"requirements[{i}]", "message": _errors(exc)})
            continue
        if not (item.text or "").strip():
            schema_errors.append({"where": f"requirements[{i}]", "message": "text is required."})
            continue
        reqs.append(item)

    with Session(_db.engine) as session:
        job, err = _find_job(session, user_id, job_id, title, company, jd_text)
        if err:
            return {"error": err}
        created = job is None
        if created:
            if not (title and company and (jd_text or "").strip()):
                return {"error": {"code": "invalid_arguments", "message":
                                  "A new job needs metadata.title, metadata.company and jd_text."}}
            job = JobDescription(user_id=user_id, title=title, company=company,
                                 description=jd_text, source_url=metadata.get("url"),
                                 application_status=metadata.get("status") or "drafting")
        else:
            if (jd_text or "").strip():
                job.description = jd_text
            for attr, value in (("title", title), ("company", company),
                                ("source_url", metadata.get("url")),
                                ("application_status", metadata.get("status"))):
                if value:
                    setattr(job, attr, value)
            job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
        session.refresh(job)
        jid, text = job.job_id, job.description or ""
        summary = {"job_id": str(jid), "created": created, "title": job.title,
                   "company": job.company,
                   "application_status": job.application_status or "drafting"}

        profile = session.exec(select(JDProfile).where(JDProfile.job_id == jid)).first()
        if reqs or profile is None:
            extraction = JDProfileExtraction(requirements=reqs,
                                             title_terms=metadata.get("title_terms") or [])
            payload = compile_profile_payload(job.title, text, extraction)
            if profile is None:
                profile = JDProfile(job_id=jid, user_id=user_id)
            payload = merge_edits(profile.payload or None, payload)
            profile.payload = payload
            profile.payload_hash = payload_digest(payload)
            profile.extraction_key = extraction_key(text)
            profile.extraction_version = PROFILE_VERSION
            profile.role_level = payload.get("role_level")

        known = {r["rule_id"] for r in list_rules(user_id)}
        answers = dict(profile.eligibility or {})
        for i, ans in enumerate(rule_answers or []):
            rid = str(ans.get("rule_id") or "").strip().lower()
            if rid not in known:
                schema_errors.append({"where": f"rule_answers[{i}]",
                                      "message": f"No rule {ans.get('rule_id')!r}; see art_briefing."})
                continue
            answers[rid] = {"answer": bool(ans["answer"]), "quote": ans.get("quote"),
                            "source": "host"}
        profile.eligibility = answers or None
        profile.updated_at = datetime.utcnow()
        session.add(profile)
        session.commit()
        n_reqs = len((profile.payload or {}).get("requirements") or [])
        stored_reqs = iter_requirements(profile.payload)

    # Requirement keywords against the user's skills (#233): what they name, and
    # the terms no skill matches, left for the host to resolve rather than guessed.
    terms_to_skills = match_requirement_terms(
        [s["name"] for s in services.get_skills(user_id)], stored_reqs)

    terms = services.resolve_keyword_weights(jid, user_id, text, persist=True) or {}
    top = sorted(terms.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_TERMS]

    # The job's role family (the host's, else the title map) and its track baseline (#229): a
    # Jev choice over the saved tracks, else the track named after the family (#199). The first
    # plan starts from that baseline while the job has no history.
    family, family_source = library.record_role_family(user_id, jid, summary["title"],
                                                       supplied_family)
    choice = library.choose_baseline(user_id, jid)
    with Session(_db.engine) as session:
        has_history = session.get(JobHead, jid) is not None
    baseline = ({"track": choice["track"], "node_id": choice["node_id"],
                 "applies": not has_history, "source": choice["source"], "p": choice["p"]}
                if choice else None)
    return {**summary, "requirements": n_reqs,
            "top_terms": [{"term": t, "weight": w} for t, w in top],
            "skill_matches": terms_to_skills["matches"],
            "unmatched_terms": terms_to_skills["unmatched"],
            "rules": resolve_rules(user_id, jid), "schema_errors": schema_errors,
            "role_family": family, "role_family_source": family_source,
            "baseline": baseline}
