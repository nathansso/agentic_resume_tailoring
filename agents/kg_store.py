"""Model-free KG persistence: dedup, merge, heal and tombstones (issue #192).

Moved verbatim from `agents/parser.py`, whose module imports the LLM stack, so the
harness can store host-extracted records through exactly the code the resume parser
uses (`harness/ingest.py::upsert_items`). `ResumeParserAgent` inherits every method
from `KGStoreMixin`, so its names and behaviour are unchanged.

The one addition: each `_save_*` returns an outcome per input item, in input order,
as `(status, row)`. `status` is one of `created`, `merged`, `unchanged`,
`skipped_manual`, `skipped_tombstone`, `skipped_empty`; `row` is the stored row it
landed on, or None. The parser ignores the return value.

Reads `database.db.engine` at call time (#175), and imports nothing that reaches a
model client; `tests/test_harness_boundary.py` holds that line.
"""
import logging
import re
from typing import Any, Dict, List

from sqlmodel import Session, select

import database.db as _db
from database.clock import utc_now
from database.db import next_seq
from database.models import Achievement, DeletedEntry, Education, Experience, Project, Skill, UserSkill
from agents.skill_postprocessor import normalize_skill_name
from agents import project_context as _ctx
from institution import canonicalize_institution

logger = logging.getLogger(__name__)

# Date strings the extractor emits when a real date is absent — coerced to None
# at save time so the knowledge graph never stores "Not specified" as a date
# (issue #72). Mirrors the tailor/formatter placeholder sets.
_PLACEHOLDER_DATE_TOKENS = {
    "", "not specified", "unknown", "unspecified", "n/a", "na", "none", "tbd", "-", "?",
}

# Sentinel title/company/name values the extractor emits when a real value is
# absent. Treated as wildcards during dedup so a placeholder-titled row folds
# into the real one instead of surviving as a duplicate (issue #72 follow-up).
_PLACEHOLDER_NAMES = {"", "unknown", "unknown position", "n/a", "na", "none", "?"}


def _clean_date(value):
    """Coerce a placeholder date string to None; pass real dates through."""
    v = str(value or "").strip()
    return None if v.lower() in _PLACEHOLDER_DATE_TOKENS else v


class KGStoreMixin:
    """Persistence for parsed or host-filled KG records. Needs `self.user.user_id`."""

    def _save_experiences(self, data: List[Dict], source: str):
        with Session(_db.engine) as session:
            existing_exps = list(session.exec(
                select(Experience).where(Experience.user_id == self.user.user_id)
            ).all())
            tombs = self._load_tombstones(session, self.user.user_id, "experience")
            outcomes = []
            for item in data:
                title = str(item.get("title") or "").strip() or "Unknown"
                company = str(item.get("company") or "").strip() or "Unknown"
                start = _clean_date(item.get("start_date"))
                end = _clean_date(item.get("end_date"))
                bullets = item.get("bullets") or []
                desc = item.get("description")

                # Fuzzy dedup (issue #72): 'IDXExchange' merges with 'IDX Exchange'
                # instead of creating a second, sparser row. A placeholder title
                # on either side still merges on company alone, so an 'Unknown
                # Position' row folds into the real one. Enrich the existing row
                # with anything it is missing rather than dropping the new data.
                match = next(
                    (e for e in existing_exps
                     if self._experiences_match(e.title, e.company, title, company)),
                    None,
                )
                if match:
                    # A user-edited row is authoritative — never revert/enrich it
                    # from a re-ingest (issue #92).
                    if match.manually_edited:
                        outcomes.append(("skipped_manual", match))
                        continue
                    changed = False
                    if self._is_placeholder_name(match.title) and not self._is_placeholder_name(title):
                        match.title = title
                        match.updated_at = utc_now()
                        session.add(match)
                        changed = True
                    if self._merge_experience(match, start, end, desc, bullets):
                        session.add(match)
                        changed = True
                    outcomes.append(("merged" if changed else "unchanged", match))
                    logger.debug(f"Merged experience into: {match.title} @ {match.company}")
                    continue

                # Don't resurrect a row the user deleted (issue #92).
                if self._experience_tombstoned(tombs, title, company):
                    logger.debug(f"Skipping tombstoned experience: {title} @ {company}")
                    outcomes.append(("skipped_tombstone", None))
                    continue

                # Don't auto-add an essentially empty stub (issue #85).
                if not self._experience_is_includable(title, company, start, end, desc, bullets):
                    logger.debug(f"Skipping content-empty experience: {title} @ {company}")
                    outcomes.append(("skipped_empty", None))
                    continue

                exp = Experience(
                    user_id=self.user.user_id,
                    title=title,
                    company=company,
                    start_date=start,
                    end_date=end,
                    description=desc,
                    bullets=bullets,
                    seq=next_seq(session, Experience, self.user.user_id),
                )
                session.add(exp)
                existing_exps.append(exp)
                outcomes.append(("created", exp))
            session.commit()
            return self._outcomes(outcomes)

    @staticmethod
    def _merge_experience(row, start, end, description, bullets) -> bool:
        """Fill a row's missing date/description/bullets from another source.
        Idempotent — re-ingesting the same data changes nothing. Returns whether
        the row was modified."""
        changed = False
        if start and not row.start_date:
            row.start_date = start; changed = True
        if end and not row.end_date:
            row.end_date = end; changed = True
        if description and not (row.description or "").strip():
            row.description = description; changed = True
        if bullets and not (row.bullets or []):
            row.bullets = bullets; changed = True
        if changed:
            row.updated_at = utc_now()
        return changed

    # ── Self-heal for existing rows (issue #72) ──────────────────────────────

    @classmethod
    def _exp_row_richness(cls, e) -> tuple:
        """Prefer a user-edited row, then a real (non-placeholder) title, then
        more bullets, then real dates, then description. The manual-edit term
        ranks first so a user-corrected row always survives a dedup merge and is
        never clobbered by a re-ingested duplicate (issue #92). The title term
        next keeps the real 'Data Science Intern' row over an 'Unknown Position'
        duplicate."""
        return (1 if getattr(e, "manually_edited", False) else 0,
                0 if cls._is_placeholder_name(e.title) else 1,
                len(e.bullets or []),
                bool(e.start_date) + bool(e.end_date),
                len((e.description or "").strip()))

    @classmethod
    def _heal_experiences(cls, session, user_id) -> int:
        """Coerce placeholder dates and merge fuzzy-duplicate experience rows for
        a user, keeping the richest of each group and deleting the rest. Returns
        the number of rows removed. Idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Experience).where(Experience.user_id == user_id)
            .order_by(Experience.seq.is_(None), Experience.seq, Experience.created_at, Experience.experience_id)
        ).all())
        kept: List = []
        removed = 0
        for e in rows:
            # A user-edited row's fields are authoritative — don't coerce them.
            if not e.manually_edited:
                cs, ce = _clean_date(e.start_date), _clean_date(e.end_date)
                if cs != e.start_date or ce != e.end_date:
                    e.start_date, e.end_date = cs, ce
                    e.updated_at = utc_now()
                    session.add(e)
            match = next(
                (k for k in kept
                 if cls._experiences_match(k.title, k.company, e.title, e.company)),
                None,
            )
            if match is None:
                kept.append(e)
                continue
            # Keep the richer row; delete the poorer. A user-edited survivor is
            # not backfilled, so a deliberately cleared field stays cleared.
            winner, loser = (e, match) if cls._exp_row_richness(e) > cls._exp_row_richness(match) else (match, e)
            if not winner.manually_edited:
                cls._merge_experience(winner, loser.start_date, loser.end_date,
                                      loser.description, loser.bullets)
            session.add(winner)
            # Projects done in the loser now belong to the survivor; flushed
            # before the delete so a Postgres FK never sees a dangling id.
            _ctx.repoint(session, Project, "experience_id", loser.experience_id, winner.experience_id)
            session.flush()
            session.delete(loser)
            if winner is e:
                kept[kept.index(match)] = e
            removed += 1
        return removed

    @classmethod
    def _heal_projects(cls, session, user_id) -> int:
        """Coerce placeholder dates and merge fuzzy-duplicate project rows for a
        user, keeping the richer of each pair. Returns rows removed."""
        rows = list(session.exec(
            select(Project).where(Project.user_id == user_id)
            .order_by(Project.seq.is_(None), Project.seq, Project.created_at, Project.project_id)
        ).all())

        def richness(p) -> tuple:
            # A user-edited row (issue #92) outranks all else so it survives and
            # is not backfilled from a re-ingested duplicate.
            return (1 if getattr(p, "manually_edited", False) else 0,
                    len((p.description or "").strip()),
                    1 if (p.metrics or {}) else 0,
                    bool(p.repo_url) + bool(p.demo_url),
                    bool(p.start_date) + bool(p.end_date))

        kept: List = []
        removed = 0
        for p in rows:
            if not p.manually_edited:
                cs, ce = _clean_date(p.start_date), _clean_date(p.end_date)
                if cs != p.start_date or ce != p.end_date:
                    p.start_date, p.end_date = cs, ce
                    p.updated_at = utc_now()
                    session.add(p)
            match = next(
                (k for k in kept
                 if cls._projects_match(k.name, k.repo_url, p.name, p.repo_url)),
                None,
            )
            if match is None:
                kept.append(p)
                continue
            richer, poorer = (p, match) if richness(p) > richness(match) else (match, p)
            # Backfill the survivor's blanks from the row being removed — unless
            # the survivor is a user edit, whose cleared fields must stay cleared.
            if not richer.manually_edited:
                for field in ("description", "repo_url", "demo_url", "start_date", "end_date"):
                    if not getattr(richer, field, None) and getattr(poorer, field, None):
                        setattr(richer, field, getattr(poorer, field))
                if not (richer.metrics or {}) and (poorer.metrics or {}):
                    richer.metrics = poorer.metrics
            # A context the user confirmed on either copy survives the merge.
            if (richer.context_status or _ctx.UNREVIEWED) == _ctx.UNREVIEWED                     and (poorer.context_status or _ctx.UNREVIEWED) != _ctx.UNREVIEWED:
                richer.experience_id, richer.education_id = poorer.experience_id, poorer.education_id
                richer.context_status = poorer.context_status
            _ctx.repoint(session, Achievement, "project_id", poorer.project_id, richer.project_id)
            richer.updated_at = utc_now()
            session.add(richer)
            session.flush()
            session.delete(poorer)
            if richer is p:  # p won: replace match in kept
                kept[kept.index(match)] = p
            removed += 1
        return removed

    @classmethod
    def _heal_education(cls, session, user_id) -> int:
        """Merge fuzzy-duplicate education rows for a user, keeping the richest
        of each institution+degree group and backfilling the survivor's blanks.
        Distinct degrees at one school stay separate. Returns rows removed;
        idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Education).where(Education.user_id == user_id)
            .order_by(Education.seq.is_(None), Education.seq, Education.created_at, Education.education_id)
        ).all())

        def richness(e) -> tuple:
            # A user-edited row (issue #92) outranks all else.
            return (1 if getattr(e, "manually_edited", False) else 0,
                    bool(e.gpa),
                    bool(e.start_date) + bool(e.end_date),
                    bool(e.location),
                    len((e.degree or "").strip()))

        kept: List = []
        removed = 0
        for e in rows:
            match = next(
                (k for k in kept
                 if cls._education_match(k.institution, k.degree, e.institution, e.degree)),
                None,
            )
            if match is None:
                kept.append(e)
                continue
            richer, poorer = (e, match) if richness(e) > richness(match) else (match, e)
            if not richer.manually_edited:
                for field in ("degree", "location", "start_date", "end_date", "gpa"):
                    if not getattr(richer, field, None) and getattr(poorer, field, None):
                        setattr(richer, field, getattr(poorer, field))
            _ctx.repoint(session, Project, "education_id", poorer.education_id, richer.education_id)
            richer.updated_at = utc_now()
            session.add(richer)
            session.flush()
            session.delete(poorer)
            if richer is e:  # e won: replace match in kept
                kept[kept.index(match)] = e
            removed += 1
        return removed

    def _save_education(self, data: List[Dict], source: str):
        with Session(_db.engine) as session:
            existing = list(session.exec(
                select(Education).where(Education.user_id == self.user.user_id)
            ).all())
            tombs = self._load_tombstones(session, self.user.user_id, "education")
            outcomes = []
            for item in data:
                institution = str(item.get("institution") or "").strip()
                degree = str(item.get("degree") or "").strip()
                if not institution:
                    outcomes.append(("skipped_empty", None))
                    continue

                # Fuzzy dedup on institution + degree, so re-ingested rows with
                # trivially different strings merge instead of duplicating, while
                # distinct degrees at one school stay separate (issue #73 follow-up).
                match = next(
                    (e for e in existing
                     if self._education_match(e.institution, e.degree, institution, degree)),
                    None,
                )
                if match:
                    if match.manually_edited:  # user-edited row is authoritative (issue #92)
                        outcomes.append(("skipped_manual", match))
                        continue
                    logger.debug(f"Merging duplicate education: {degree} at {institution}")
                    changed = False
                    if not match.degree and degree:
                        match.degree = degree; changed = True
                    gpa = item.get("gpa")
                    for field, val in (("location", item.get("location")),
                                       ("start_date", item.get("start_date")),
                                       ("end_date", item.get("end_date")),
                                       ("gpa", str(gpa).strip() if gpa else None)):
                        v = str(val or "").strip()
                        if v and not getattr(match, field, None):
                            setattr(match, field, v); changed = True
                    if changed:
                        match.updated_at = utc_now()
                        session.add(match)
                    outcomes.append(("merged" if changed else "unchanged", match))
                    continue

                # Don't resurrect a row the user deleted (issue #92).
                if self._education_tombstoned(tombs, institution, degree):
                    logger.debug(f"Skipping tombstoned education: {degree} at {institution}")
                    outcomes.append(("skipped_tombstone", None))
                    continue

                gpa = item.get("gpa")
                row = Education(
                    user_id=self.user.user_id,
                    institution=institution,
                    degree=degree,
                    location=item.get("location"),
                    start_date=item.get("start_date"),
                    end_date=item.get("end_date"),
                    gpa=str(gpa).strip() if gpa else None,
                    seq=next_seq(session, Education, self.user.user_id),
                )
                session.add(row)
                existing.append(row)
                outcomes.append(("created", row))
            session.commit()
            return self._outcomes(outcomes)

    def _save_achievements(self, data: List[Dict], source: str):
        with Session(_db.engine) as session:
            existing = list(session.exec(
                select(Achievement).where(Achievement.user_id == self.user.user_id)
            ).all())
            outcomes = []
            for item in data:
                title = str(item.get("title") or "").strip()
                if not title:
                    outcomes.append(("skipped_empty", None))
                    continue
                issuer = str(item.get("issuer") or "").strip() or None

                # Fuzzy dedup on title (+ issuer as enrichment), so a resume line
                # and its LinkedIn honors_and_awards entry fold into one row
                # instead of duplicating. Never drops content — merges blanks.
                match = next(
                    (a for a in existing
                     if self._achievements_match(a.title, a.issuer, title, issuer)),
                    None,
                )
                if match:
                    changed = self._merge_achievement(match, item)
                    if changed:
                        session.add(match)
                    outcomes.append(("merged" if changed else "unchanged", match))
                    logger.debug(f"Merged achievement into: {match.title}")
                    continue

                row = Achievement(
                    user_id=self.user.user_id,
                    title=title,
                    description=str(item.get("description") or "").strip() or None,
                    issuer=issuer,
                    date=str(item.get("date") or "").strip() or None,
                    seq=next_seq(session, Achievement, self.user.user_id),
                )
                session.add(row)
                existing.append(row)
                outcomes.append(("created", row))
            session.commit()
            return self._outcomes(outcomes)

    @staticmethod
    def _merge_achievement(row, item: Dict) -> bool:
        """Fill a row's missing description/issuer/date from another source.
        Idempotent — re-ingesting the same data changes nothing. Returns whether
        the row was modified."""
        changed = False
        for field in ("description", "issuer", "date"):
            val = str(item.get(field) or "").strip()
            if val and not getattr(row, field, None):
                setattr(row, field, val)
                changed = True
        if changed:
            row.updated_at = utc_now()
        return changed

    @classmethod
    def _heal_achievements(cls, session, user_id) -> int:
        """Merge fuzzy-duplicate achievement rows for a user, keeping the richest
        of each title group and backfilling the survivor's blanks. Returns rows
        removed; idempotent when the data is already clean."""
        rows = list(session.exec(
            select(Achievement).where(Achievement.user_id == user_id)
            .order_by(Achievement.seq.is_(None), Achievement.seq, Achievement.created_at, Achievement.achievement_id)
        ).all())

        def richness(a) -> tuple:
            return (len((a.description or "").strip()),
                    bool(a.issuer),
                    bool(a.date))

        kept: List = []
        removed = 0
        for a in rows:
            match = next(
                (k for k in kept
                 if cls._achievements_match(k.title, k.issuer, a.title, a.issuer)),
                None,
            )
            if match is None:
                kept.append(a)
                continue
            richer, poorer = (a, match) if richness(a) > richness(match) else (match, a)
            for field in ("description", "issuer", "date"):
                if not getattr(richer, field, None) and getattr(poorer, field, None):
                    setattr(richer, field, getattr(poorer, field))
            if richer.project_id is None and poorer.project_id is not None:
                richer.project_id = poorer.project_id
            richer.updated_at = utc_now()
            session.add(richer)
            session.delete(poorer)
            if richer is a:  # a won: replace match in kept
                kept[kept.index(match)] = a
            removed += 1
        return removed

    def _save_projects(self, data: List[Dict], source: str, repo_metrics: Dict[str, Dict] = None):
        # GitHub signals keyed by repo name (issue #46) — matched case-insensitively
        # against extracted project names so complexity scoring can use them later.
        metrics_by_name = {k.lower(): v for k, v in (repo_metrics or {}).items()}
        with Session(_db.engine) as session:
            existing_projects = list(session.exec(
                select(Project).where(Project.user_id == self.user.user_id)
            ).all())
            tombs = self._load_tombstones(session, self.user.user_id, "project")
            outcomes = []
            for item in data:
                name = str(item.get("name") or "").strip() or "Unknown"
                metrics = metrics_by_name.get(name.lower(), {})

                # Fuzzy dedup (issue #72): '…Prediction(Stacked…)' merges with
                # '…Prediction (Stacked…)' instead of duplicating. A shared repo
                # URL merges a GitHub-ingested repo with its resume line even
                # when the names diverge.
                match = next(
                    (p for p in existing_projects
                     if self._projects_match(p.name, p.repo_url, name, item.get("repo_url"))),
                    None,
                )
                if match:
                    if match.manually_edited:  # user-edited row is authoritative (issue #92)
                        outcomes.append(("skipped_manual", match))
                        continue
                    changed = False
                    if metrics:
                        match.metrics = metrics; changed = True  # refresh GitHub signals
                    for field in ("description", "repo_url", "demo_url"):
                        val = str(item.get(field) or "").strip()
                        if val and not getattr(match, field, None):
                            setattr(match, field, val); changed = True
                    for field in ("start_date", "end_date"):
                        val = _clean_date(item.get(field))
                        if val and not getattr(match, field, None):
                            setattr(match, field, val); changed = True
                    if changed:
                        session.add(match)
                    outcomes.append(("merged" if changed else "unchanged", match))
                    logger.debug(f"Merged project into: {match.name}")
                    continue

                # Don't resurrect a row the user deleted (issue #92).
                if self._project_tombstoned(tombs, name, item.get("repo_url")):
                    logger.debug(f"Skipping tombstoned project: {name}")
                    outcomes.append(("skipped_tombstone", None))
                    continue

                proj = Project(
                    user_id=self.user.user_id,
                    name=name,
                    description=item.get("description"),
                    repo_url=item.get("repo_url"),
                    demo_url=item.get("demo_url"),
                    start_date=_clean_date(item.get("start_date")),
                    end_date=_clean_date(item.get("end_date")),
                    metrics=metrics,
                    seq=next_seq(session, Project, self.user.user_id),
                )
                session.add(proj)
                existing_projects.append(proj)
                outcomes.append(("created", proj))
            session.commit()
            return self._outcomes(outcomes)

    @staticmethod
    def _norm_name(name: Any) -> str:
        name = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
        return re.sub(r"\s+", " ", name).strip()

    @classmethod
    def _names_match(cls, a: Any, b: Any) -> bool:
        """Equal after normalization, ignoring spacing ('IDXExchange' ==
        'IDX Exchange'), or containment for long names so
        'Recipe Review Analysis - Classification Model …' merges with
        'Recipe Review Analysis' instead of duplicating it."""
        na, nb = cls._norm_name(a), cls._norm_name(b)
        if not na or not nb:
            return False
        if na == nb or na.replace(" ", "") == nb.replace(" ", ""):
            return True
        shorter, longer = sorted((na, nb), key=len)
        return len(shorter) >= 10 and shorter in longer

    @classmethod
    def _is_placeholder_name(cls, value: Any) -> bool:
        """True when a title/company/name is a sentinel like 'Unknown Position'
        or '?' that should act as a wildcard during dedup rather than a real,
        distinguishing value."""
        return cls._norm_name(value) in _PLACEHOLDER_NAMES

    @classmethod
    def _institutions_match(cls, a: Any, b: Any) -> bool:
        """Same institution/employer when their canonical keys agree — a ROR id
        when the name resolves, so 'UC San Diego' and 'University of California,
        San Diego' match (issue #95) — or, when ROR can't resolve them (most
        companies), when the raw names fuzzy-match as before."""
        ka, kb = canonicalize_institution(a), canonicalize_institution(b)
        if ka and kb and ka == kb:
            return True
        return cls._names_match(a, b)

    @classmethod
    def _experiences_match(cls, t1: Any, c1: Any, t2: Any, c2: Any) -> bool:
        """Two experiences are the same job when their companies match (canonical
        key, else fuzzy) and either their titles fuzzy-match or one side's title
        is a placeholder. Lets a sparse 'Unknown Position @ IDXExchange' row fold
        into the real 'Data Science Intern @ IDX Exchange' one, across any pair
        of sources."""
        if not cls._institutions_match(c1, c2):
            return False
        return (cls._names_match(t1, t2)
                or cls._is_placeholder_name(t1)
                or cls._is_placeholder_name(t2))

    @classmethod
    def _projects_match(cls, a_name: Any, a_repo: Any, b_name: Any, b_repo: Any) -> bool:
        """Same project when they share a repo URL — the strongest cross-source
        signal, matching a GitHub-ingested repo to its resume line even when the
        names diverge — or, failing that, when their names fuzzy-match."""
        ra = str(a_repo or "").strip().lower().rstrip("/")
        rb = str(b_repo or "").strip().lower().rstrip("/")
        if ra and rb and ra == rb:
            return True
        return cls._names_match(a_name, b_name)

    # Degree-level tokens, checked most-advanced first. Matching on level rather
    # than raw string lets an abbreviated 'BS, CS' merge with a spelled-out
    # 'B.S. Computer Science' while keeping an 'M.S.' distinct from a 'B.S.' at
    # the same school. Patterns run against the normalized (space-joined) degree,
    # so 'b\s*s' covers both 'bs' and 'b s'.
    _DEGREE_LEVEL_PATTERNS = [
        ("phd", re.compile(r"\b(ph\s*d|phd|doctor\w*|dphil)\b")),
        ("master", re.compile(r"\b(m\s*b\s*a|mba|master\w*|m\s*s|m\s*sc|m\s*a|m\s*eng)\b")),
        ("bachelor", re.compile(r"\b(bachelor\w*|b\s*s|b\s*sc|b\s*a|b\s*eng)\b")),
        ("associate", re.compile(r"\b(associate\w*|a\s*a\s*s)\b")),
    ]

    # Connective words that aren't part of a field of study, stripped before
    # comparing the field portions of two same-level degrees (issue #95).
    _DEGREE_STOPWORDS = re.compile(
        r"\b(in|of|the|and|minor|major|concentration|with|honors|degree)\b")

    @classmethod
    def _degree_level(cls, degree: Any) -> str:
        """Coarse degree level ('bachelor'/'master'/'phd'/'associate') extracted
        from a free-form degree string, or '' when none is recognized."""
        norm = cls._norm_name(degree)
        for level, pattern in cls._DEGREE_LEVEL_PATTERNS:
            if pattern.search(norm):
                return level
        return ""

    @classmethod
    def _degree_field_tokens(cls, degree: Any) -> List[str]:
        """The field-of-study tokens of a degree, with level markers ('B.S.',
        'M.S.') and connective words removed: 'B.S. Mathematics & Economics,
        Minor in Data Science' -> ['mathematics','economics','data','science']."""
        norm = cls._norm_name(degree)
        for _level, pattern in cls._DEGREE_LEVEL_PATTERNS:
            norm = pattern.sub(" ", norm)
        norm = cls._DEGREE_STOPWORDS.sub(" ", norm)
        return [t for t in norm.split() if t]

    @classmethod
    def _degrees_compatible(cls, deg_a: Any, deg_b: Any) -> bool:
        """Whether two same-level degrees name the same field. True when the
        degree/field strings fuzzy-match, or one field is an acronym of the other
        ('CS' vs 'Computer Science'). False when they name distinct fields, or one
        has no identifiable field so agreement can't be confirmed (so an MBA and
        an M.S. Data Science at one school stay separate). (issue #95)"""
        if cls._names_match(deg_a, deg_b):
            return True
        fa, fb = cls._degree_field_tokens(deg_a), cls._degree_field_tokens(deg_b)
        if not fa or not fb:
            return False
        if cls._names_match(" ".join(fa), " ".join(fb)):
            return True
        # Acronym: a single-token field vs the initials of a multi-token field.
        for short, long_toks in ((fa, fb), (fb, fa)):
            if len(short) == 1 and len(long_toks) > 1:
                if short[0] == "".join(t[0] for t in long_toks):
                    return True
        return False

    @classmethod
    def _education_match(cls, inst_a: Any, deg_a: Any, inst_b: Any, deg_b: Any) -> bool:
        """Same education entry when institutions canonicalize to the same key
        ('UC San Diego' == 'University of California, San Diego' via ROR) and the
        degrees are compatible. A blank/unknown degree on either side folds into
        the fuller one; two distinct degrees at one school stay separate — an M.S.
        and a B.S. by level, and two same-level majors (B.S. Math vs B.S. Physics)
        by field. (issue #95)"""
        if not cls._institutions_match(inst_a, inst_b):
            return False
        da, db = cls._norm_name(deg_a), cls._norm_name(deg_b)
        if not da or not db:
            return True  # a blank degree can't distinguish — merge and backfill
        la, lb = cls._degree_level(deg_a), cls._degree_level(deg_b)
        if la and lb and la != lb:
            return False  # different levels (B.S. vs M.S.) are distinct entries
        return cls._degrees_compatible(deg_a, deg_b)

    @classmethod
    def _achievements_match(cls, title_a: Any, issuer_a: Any, title_b: Any, issuer_b: Any) -> bool:
        """Same achievement when their titles fuzzy-match ('Deans List' ==
        "Dean's List"), or when one title is a substring of a longer title and
        the issuers agree — folding a resume line into its LinkedIn honors entry
        across sources. Issuer alone never matches; a distinct award keeps its
        own row."""
        if cls._names_match(title_a, title_b):
            return True
        ia, ib = cls._norm_name(issuer_a), cls._norm_name(issuer_b)
        if ia and ib and ia == ib:
            ta, tb = cls._norm_name(title_a), cls._norm_name(title_b)
            if ta and tb and (ta in tb or tb in ta):
                return True
        return False

    @staticmethod
    def _enrich(row, item: Dict, fields: List[str]) -> bool:
        """Fill missing scalar fields; append genuinely new description text.
        Idempotent: re-ingesting the same record changes nothing."""
        changed = False
        for field in fields:
            val = str(item.get(field) or "").strip()
            if val and not getattr(row, field, None):
                setattr(row, field, val)
                changed = True
        desc = str(item.get("description") or "").strip()
        if desc and row.description and desc not in row.description:
            row.description = f"{row.description}\n\n[LinkedIn] {desc}"
            changed = True
        if changed:
            row.updated_at = utc_now()
        return changed

    # ── User-deletion tombstones (issue #92) ─────────────────────────────────
    # A row the user deleted in the Data Explorer must not be resurrected by a
    # re-ingest. Save paths load the user's tombstones once and skip creating a
    # row that matches one, reusing the same fuzzy-match functions the deduper
    # uses so a tombstone catches the same variants.

    @staticmethod
    def _load_tombstones(session, user_id, entity_type: str) -> List:
        return list(session.exec(
            select(DeletedEntry).where(
                DeletedEntry.user_id == user_id,
                DeletedEntry.entity_type == entity_type,
            )
        ).all())

    @classmethod
    def _experience_tombstoned(cls, tombs, title, company) -> bool:
        return any(cls._experiences_match(t.key_a, t.key_b, title, company) for t in tombs)

    @classmethod
    def _project_tombstoned(cls, tombs, name, repo_url) -> bool:
        return any(cls._projects_match(t.key_a, t.key_b, name, repo_url) for t in tombs)

    @classmethod
    def _education_tombstoned(cls, tombs, institution, degree) -> bool:
        return any(cls._education_match(t.key_a, t.key_b, institution, degree) for t in tombs)

    @classmethod
    def _experience_is_includable(cls, title, company, start, end, description, bullets) -> bool:
        """Whether an experience is substantive enough to auto-add (issue #85).

        Include it when it has any real content (a date, a description, or a
        bullet) or a complete identity (both a real title and a real company).
        This blocks the 'essentially empty' stub — e.g. an 'Unknown Position @
        SomeCo' company grouping with no dates or detail — from being silently
        added to the knowledge graph, while keeping a legitimately minimal
        'Data Scientist @ Acme' row the user can flesh out later via the Data
        Explorer.
        """
        has_content = (bool(bullets) or bool(str(description or "").strip())
                       or bool(start) or bool(end))
        identity_complete = (not cls._is_placeholder_name(title)
                             and not cls._is_placeholder_name(company))
        return has_content or identity_complete

    def _save_skills(self, data: List[Dict], source: str):
        touched_skill_ids = set()
        outcomes = []
        with Session(_db.engine) as session:
            for item in data:
                # Normalize name via alias map
                raw_name = normalize_skill_name(item.get("name", "").strip())
                if not raw_name:
                    outcomes.append(("skipped_empty", None))
                    continue

                # Get or create the Skill node
                skill = session.exec(select(Skill).where(Skill.name == raw_name)).first()
                if not skill:
                    skill = Skill(name=raw_name, category=item.get("category"))
                    session.add(skill)
                    session.commit()
                    session.refresh(skill)
                touched_skill_ids.add(skill.skill_id)

                # Check if this exact user+skill+source edge already exists
                existing_link = session.exec(
                    select(UserSkill).where(
                        UserSkill.user_id == self.user.user_id,
                        UserSkill.skill_id == skill.skill_id,
                        UserSkill.evidence_source == source,
                    )
                ).first()

                if existing_link:
                    # Same source — update proficiency if higher
                    new_prof = item.get("proficiency", 1)
                    raised = new_prof > (existing_link.proficiency or 0)
                    if raised:
                        existing_link.proficiency = new_prof
                        session.add(existing_link)
                    outcomes.append(("merged" if raised else "unchanged", skill))
                    continue

                # New evidence source — create a new edge
                link = UserSkill(
                    user_id=self.user.user_id,
                    skill_id=skill.skill_id,
                    proficiency=item.get("proficiency", 1),
                    evidence_source=source
                )
                session.add(link)
                outcomes.append(("created", skill))
            session.commit()
            result = self._outcomes(outcomes)

            # Recompute cached embeddings for skills touched by this ingest
            # (issue #54). Bounded to new/changed skills; degrades to a no-op if
            # the embedding model is unavailable.
            try:
                from agents.skill_embeddings import ensure_skill_embeddings
                ensure_skill_embeddings(session, touched_skill_ids)
            except Exception as exc:
                logger.warning("Skill embedding refresh skipped: %s", exc)
            return result

    @staticmethod
    def _outcomes(pairs) -> List:
        """`(status, row)` pairs as `(status, row fields)`, read while the session
        that owns the rows is still open. Attribute access, not `model_dump`: a
        committed row is expired, and only `getattr` reloads it."""
        return [(status, None if row is None else {f: getattr(row, f) for f in type(row).model_fields})
                for status, row in pairs]
