"""Skill matcher ordering determinism (issue #171, found via replay).

`SkillMatcherAgent.match` loaded a job's skills with an unordered
`select(JobSkill)`. SQLite and Postgres returned them in different orders, and
that order is rendered verbatim into the tailoring planner's prompt as
`missing_skills` — so the same profile against the same JD sent the model a
different prompt depending on the engine.

Found by recording a product-mode benchmark run on one engine and replaying it
on the other: the replay reported a cassette miss on the third task's planner
prompt, and the only difference between the two rendered prompts was the order
of `missing_skills`. The plumbing stub cannot see this class of bug at all — it
routes on prompt *markers* and ignores prompt content entirely.

Same class as #158 (first-wins merges over unordered queries) and exactly what
#149's deviations predicted: "an unordered query can pass here and diverge in
production after an UPDATE or VACUUM."
"""
import random

import pytest
from sqlmodel import Session

from database.models import JobDescription, JobSkill, Skill

# One required/heavier group and one tied group, so both the weight ordering
# and the name tiebreak are exercised.
JOB_SKILLS = [
    ("Swift", True, 0.9),
    ("iOS Development", True, 0.9),
    ("Java", True, 0.7),
    ("Code Review", False, 0.5),
    ("Cross-functional Collaboration", False, 0.5),
    ("Native Application Development", False, 0.5),
    ("A/B Testing and Experimentation", False, 0.5),
    ("English (Spoken and Written)", False, 0.5),
]


@pytest.fixture
def matcher_engine(isolated_engine, monkeypatch):
    """`isolated_engine` plus the matcher's own module-level engine reference.

    `agents/matcher.py` does `from database.db import engine` at import time and
    is not in conftest's patch list, so the first test in a session to import it
    binds *that* test's engine for every later one — the second test here would
    otherwise read an empty database and quietly assert nothing.
    """
    import agents.matcher as matcher_module

    monkeypatch.setattr(matcher_module, "engine", isolated_engine)
    return isolated_engine


def _seed_job(engine, rows):
    """One job whose JobSkill rows are inserted in the given order. Returns its id."""
    with Session(engine) as session:
        job = JobDescription(title="iOS Software Engineer I", company="Duolingo",
                             description="Swift, iOS, Java. Code review.",
                             status="analyzed")
        session.add(job)
        session.commit()
        session.refresh(job)
        for name, required, weight in rows:
            skill = Skill(name=name, category="Tool")
            session.add(skill)
            session.flush()
            session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id,
                                 required=required, weight=weight))
        session.commit()
        return job.job_id


def _missing_skills(job_id, user_id):
    from agents.matcher import SkillMatcherAgent

    result = SkillMatcherAgent().match(user_id, job_id)
    return list(result.missing_skills or [])


def test_missing_skills_order_is_independent_of_row_insertion_order(matcher_engine):
    """Row order must not reach the planner prompt.

    Insertion order is the only thing that differs between the jobs here, and
    it is precisely what the two database engines disagree about.
    """
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(matcher_engine)

    orders = []
    for seed in range(4):
        rows = list(JOB_SKILLS)
        random.Random(seed).shuffle(rows)
        job_id = _seed_job(matcher_engine, rows)
        orders.append(_missing_skills(job_id, user.user_id))

    assert all(o == orders[0] for o in orders), (
        f"missing_skills follows row order: {orders}")
    # The seed user knows only Python, so nothing here matches and the whole
    # set is ordered — an empty list would make the assertion above vacuous.
    assert len(orders[0]) == len(JOB_SKILLS)


def test_missing_skills_are_ordered_by_weight_then_required_then_name(matcher_engine):
    """The pinned order is meaning-preserving, not arbitrary.

    Weight is the analyzer's prominence proxy, so the most important missing
    skills stay first; ties break on content (required first, then name) rather
    than on storage order or on a per-database uuid (#158).
    """
    from conftest import _seed_user_and_skill

    user = _seed_user_and_skill(matcher_engine)
    job_id = _seed_job(matcher_engine, list(reversed(JOB_SKILLS)))
    missing = _missing_skills(job_id, user.user_id)

    # Rows were inserted in reverse, so any of this that still holds is the
    # sort's doing. The 0.9 pair leads, ordered by name within its weight, then
    # the 0.7, then the tied 0.5s by name.
    assert missing[:3] == ["iOS Development", "Swift", "Java"]
    assert missing[3:] == sorted(missing[3:], key=str.lower)
