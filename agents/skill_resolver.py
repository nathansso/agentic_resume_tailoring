"""One identity per skill (issue #365). No model.

`Skill` rows are global, and a name is one skill when `skill_fold` says so: "PyTorch",
"pytorch" and "torch" are one, "Time-Series Analysis" and "time series analysis" are one.
Every path that links a user (or a job) to a skill finds it here, so a new spelling joins
the existing row instead of creating a second one:

- `SkillResolver` / `resolve_skill`: the row a name belongs to, or None (the caller creates
  it under `canonical_name`).
- `merge_user_skill_duplicates`: the one-off for a user who already has two rows that fold
  together. It repoints that user's own links onto one keeper and never deletes or renames
  a global `Skill` row, because another user may link it.

Model-free by rule (`tests/test_harness_boundary.py`).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Optional
from uuid import UUID

from sqlmodel import Session, select

from agents.skill_matching import canonical_name, skill_fold
from database.clock import utc_now
from database.models import DeletedEntry, JobDescription, JobSkill, Skill, UserSkill


def _sort_time(skill: Skill):
    stamp = skill.created_at
    # SQLite hands back naive datetimes and Postgres aware ones; one database never mixes them.
    return stamp.replace(tzinfo=None) if stamp is not None and stamp.tzinfo else stamp


def keeper_key(skill: Skill):
    """Sort key for choosing among skills that fold together: the alias-canonical spelling
    first ("PyTorch" over "pytorch"), then the oldest, then the id, so the choice is stable."""
    canonical = 0 if skill.name == canonical_name(skill.name) else 1
    stamp = _sort_time(skill)
    return (canonical, stamp is None, stamp, str(skill.skill_id))


def pick_keeper(skills: Iterable[Skill]) -> Optional[Skill]:
    return min(skills, key=keeper_key, default=None)


class SkillResolver:
    """Finds the `Skill` a name belongs to, in this order:

    1. a skill this user already links to whose fold equals the name's fold;
    2. a global skill named exactly `canonical_name(name)`;
    3. a global skill with the same fold (`pick_keeper` among them);
    4. None.

    The indexes are read once, lazily, and `register` adds a row the caller just created, so
    a batch of names costs one scan, not one per name.
    """

    def __init__(self, session: Session, user_id: Optional[UUID] = None):
        self.session = session
        self.user_id = user_id
        self._own: Optional[Dict[str, List[Skill]]] = None
        self._by_name: Optional[Dict[str, List[Skill]]] = None
        self._by_fold: Optional[Dict[str, List[Skill]]] = None

    def _own_index(self) -> Dict[str, List[Skill]]:
        if self._own is None:
            self._own = defaultdict(list)
            if self.user_id is not None:
                rows = self.session.exec(
                    select(Skill).join(UserSkill, UserSkill.skill_id == Skill.skill_id)
                    .where(UserSkill.user_id == self.user_id)).all()
                for skill in {s.skill_id: s for s in rows}.values():
                    self._own[skill_fold(skill.name)].append(skill)
        return self._own

    def _global_indexes(self):
        if self._by_fold is None:
            self._by_name, self._by_fold = defaultdict(list), defaultdict(list)
            for skill in self.session.exec(select(Skill)).all():
                self._by_name[skill.name].append(skill)
                self._by_fold[skill_fold(skill.name)].append(skill)
        return self._by_name, self._by_fold

    def resolve(self, name: str) -> Optional[Skill]:
        fold = skill_fold(name or "")
        if not fold:
            return None
        own = self._own_index().get(fold)
        if own:
            return pick_keeper(own)
        by_name, by_fold = self._global_indexes()
        exact = by_name.get(canonical_name(name))
        if exact:
            return pick_keeper(exact)
        return pick_keeper(by_fold.get(fold, ()))

    def register(self, skill: Skill, linked: bool = True) -> None:
        """Make a skill the caller just created (or linked) findable by the next `resolve`."""
        fold = skill_fold(skill.name)
        self._global_indexes()
        for index, key in ((self._by_name, skill.name), (self._by_fold, fold)):
            if not any(s.skill_id == skill.skill_id for s in index[key]):
                index[key].append(skill)
        if linked and self.user_id is not None:
            own = self._own_index()[fold]
            if not any(s.skill_id == skill.skill_id for s in own):
                own.append(skill)

    def get_or_create(self, name: str, category: Optional[str] = None) -> Optional[Skill]:
        """The skill for `name`, created under its canonical spelling when there is none.
        The caller commits (or flushes)."""
        skill = self.resolve(name)
        if skill is None:
            canon = canonical_name(name or "")
            if not canon:
                return None
            skill = Skill(name=canon, category=category)
            self.session.add(skill)
            self.session.flush()
            self.register(skill, linked=False)
        return skill


def resolve_skill(session: Session, user_id: Optional[UUID], name: str) -> Optional[Skill]:
    """One-shot `SkillResolver(session, user_id).resolve(name)`."""
    return SkillResolver(session, user_id).resolve(name)


# ── merging a user's existing duplicates ─────────────────────────────────────

def _higher(a, b):
    return b if a is None or (b is not None and b > a) else a


def _fold_user_rows(keeper_row: UserSkill, loser_row: UserSkill) -> None:
    """Fold a duplicate (same user, skill and source) into the row that stays."""
    keeper_row.proficiency = _higher(keeper_row.proficiency, loser_row.proficiency)
    keeper_row.confidence_score = max(keeper_row.confidence_score or 0.0, loser_row.confidence_score or 0.0)
    keeper_row.is_core = bool(keeper_row.is_core or loser_row.is_core)
    for field in ("evidence_detail", "source_context"):
        if not getattr(keeper_row, field) and getattr(loser_row, field):
            setattr(keeper_row, field, getattr(loser_row, field))
    if loser_row.created_at and keeper_row.created_at and _stamp(loser_row) < _stamp(keeper_row):
        keeper_row.created_at = loser_row.created_at
    keeper_row.updated_at = utc_now()


def _stamp(row):
    stamp = row.created_at
    return stamp.replace(tzinfo=None) if stamp.tzinfo else stamp


def merge_user_skill_duplicates(session: Session, user_id: UUID) -> int:
    """Leave this user one `Skill` per fold. Returns how many duplicate skills were merged
    away; 0 when the data is already clean, so a second run changes nothing.

    For each fold the user links under more than one `Skill`, the keeper is `pick_keeper`
    among those skills. The user's `UserSkill` rows move onto it (a row that would repeat an
    existing keeper row for the same evidence source is folded into it: highest proficiency
    and confidence, core if either was); so do the `JobSkill` rows of the user's own jobs and
    the `payload.skill_id` of the user's dismissed-skill tombstones. A global `Skill` row is
    never deleted or renamed and another user's rows are never touched. The caller commits.
    """
    links = session.exec(select(UserSkill).where(UserSkill.user_id == user_id)).all()
    skills = {s.skill_id: s for s in session.exec(
        select(Skill).where(Skill.skill_id.in_(list({l.skill_id for l in links})))).all()} if links else {}
    groups: Dict[str, List[Skill]] = defaultdict(list)
    for skill in skills.values():
        groups[skill_fold(skill.name)].append(skill)

    redirect: Dict[UUID, Skill] = {}
    for fold, members in groups.items():
        if fold and len(members) > 1:
            keeper = pick_keeper(members)
            for member in members:
                if member.skill_id != keeper.skill_id:
                    redirect[member.skill_id] = keeper
    if not redirect:
        return 0

    # UserSkill: repoint, or fold into the keeper's row for the same source.
    stay: Dict[tuple, UserSkill] = {(l.skill_id, l.evidence_source): l for l in links if l.skill_id not in redirect}
    for link in sorted(links, key=lambda l: (str(l.created_at), str(l.user_skill_id))):
        keeper = redirect.get(link.skill_id)
        if keeper is None:
            continue
        existing = stay.get((keeper.skill_id, link.evidence_source))
        if existing is None:
            link.skill_id = keeper.skill_id
            link.updated_at = utc_now()
            session.add(link)
            stay[(keeper.skill_id, link.evidence_source)] = link
        else:
            _fold_user_rows(existing, link)
            session.add(existing)
            session.delete(link)

    # JobSkill of the user's own jobs: other users' jobs keep their rows.
    job_ids = [j for j in session.exec(
        select(JobDescription.job_id).where(JobDescription.user_id == user_id)).all()]
    if job_ids:
        job_rows = session.exec(select(JobSkill).where(
            JobSkill.job_id.in_(job_ids), JobSkill.skill_id.in_(list(redirect)))).all()
        held = {(r.job_id, r.skill_id): r for r in session.exec(select(JobSkill).where(
            JobSkill.job_id.in_(job_ids),
            JobSkill.skill_id.in_(list({k.skill_id for k in redirect.values()})))).all()}
        for row in sorted(job_rows, key=lambda r: (str(r.created_at), str(r.job_skill_id))):
            keeper = redirect[row.skill_id]
            existing = held.get((row.job_id, keeper.skill_id))
            if existing is None:
                row.skill_id = keeper.skill_id
                row.updated_at = utc_now()
                session.add(row)
                held[(row.job_id, keeper.skill_id)] = row
            else:
                existing.required = bool(existing.required or row.required)
                existing.weight = max(existing.weight or 0.0, row.weight or 0.0)
                session.add(existing)
                session.delete(row)

    # Dismissed-skill tombstones that name a merged-away id now name the keeper.
    for tomb in session.exec(select(DeletedEntry).where(
            DeletedEntry.user_id == user_id, DeletedEntry.entity_type == "skill")).all():
        payload = tomb.payload or {}
        try:
            old = UUID(str(payload.get("skill_id")))
        except ValueError:
            continue
        keeper = redirect.get(old)
        if keeper is not None:
            # A new dict: an in-place edit of a JSON column is not tracked.
            tomb.payload = {**payload, "skill_id": str(keeper.skill_id), "name": keeper.name,
                            "category": keeper.category}
            session.add(tomb)

    session.flush()
    return len(redirect)
