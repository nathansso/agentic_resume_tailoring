"""Distractor pool admission and answer-key invariance (issue #172, chunk 5).

The load-bearing property: injecting distractors changes the size of a profile
and **nothing else**. Two levels of check, because they can fail independently:

* the *admission rules* hold against the live corpus (fast, no model), and
* the *matcher* really does return an identical answer key once they hold.

The second is what the acceptance criterion asks for; the first is what makes a
failure diagnosable when it does not.
"""
import pytest
from sqlmodel import Session

from database.models import JobDescription, JobSkill, Skill, UserSkill
from eval.distractors import (
    ADMITTED,
    REJECTED,
    AdmissionError,
    Verdict,
    admitted_terms,
    audit,
    committed_admission,
    corpus_keywords,
    is_lexically_admissible,
    lexical_conflicts,
    semantic_threshold,
)
from eval.profile_banks import DISTRACTOR_CANDIDATES


# ── the bank and the committed audit ──────────────────────────────────────────

def test_candidates_are_unique():
    assert len(DISTRACTOR_CANDIDATES) == len(set(DISTRACTOR_CANDIDATES))


def test_every_candidate_has_a_verdict():
    """A candidate in neither dict was never audited, and would otherwise sit in
    the bank looking considered."""
    for term in DISTRACTOR_CANDIDATES:
        assert term in ADMITTED or term in REJECTED, term


def test_admitted_and_rejected_are_disjoint():
    assert not (set(ADMITTED) & set(REJECTED))


def test_admitted_terms_come_from_the_bank():
    assert set(ADMITTED) <= set(DISTRACTOR_CANDIDATES)


def test_pool_is_large_enough_to_be_a_dial():
    """A handful of distractors cannot move a 15-skill profile past MAX_SKILLS,
    which is the pressure the pool exists to create."""
    from agents.skill_scorer import MAX_SKILLS

    assert len(ADMITTED) > MAX_SKILLS


def test_committed_admission_rejects_an_unaudited_term():
    with pytest.raises(AdmissionError, match="Cuneiform"):
        committed_admission(list(DISTRACTOR_CANDIDATES) + ["Cuneiform"])


# ── rule L, re-derived against the live corpus ────────────────────────────────

def test_every_admitted_term_shares_no_keyword_with_any_posting():
    """The verify-don't-trust check, and the one that actually protects
    `keyword_coverage`.

    Re-derived from the committed corpus on every run rather than read out of
    `ADMITTED`: a corpus refresh that introduces a term is exactly the event this
    has to catch, and a stored verdict would not notice.
    """
    keywords = corpus_keywords()
    offenders = {term: lexical_conflicts(term, keywords)
                 for term in sorted(ADMITTED)}
    assert {t: c for t, c in offenders.items() if c} == {}


def test_lexical_rejections_really_do_conflict():
    """A rejection reason has to be true, or the bank teaches the next reader
    something false about why a candidate is unusable."""
    keywords = corpus_keywords()
    for term, reason in sorted(REJECTED.items()):
        if reason.startswith("lexical:"):
            assert lexical_conflicts(term, keywords), term


def test_lexical_rule_uses_the_metric_s_own_extractor():
    """`keyword_coverage` invariance is only implied if the check runs on the
    vocabulary that metric scores over."""
    from agents.ats_scorer import ATSScoringEngine

    keywords = {"python", "airflow"}
    assert lexical_conflicts("Python", keywords) == ["python"]
    assert lexical_conflicts("COBOL", keywords) == []
    assert (ATSScoringEngine._extract_keywords("Python") & keywords) == {"python"}


def test_corpus_keywords_covers_the_whole_dataset():
    keywords = corpus_keywords()
    assert len(keywords) > 1000
    # Terms the corpus demonstrably contains, so an empty or truncated read fails
    # rather than passing vacuously.
    assert {"python", "sql"} <= keywords


def test_a_term_in_the_corpus_is_not_lexically_admissible():
    keywords = corpus_keywords()
    assert not is_lexically_admissible("Python", keywords)


# ── rule S ────────────────────────────────────────────────────────────────────

def test_semantic_threshold_tracks_the_matcher():
    """Admission replays the matcher's own channel, so it must read the matcher's
    own constant. A drifting copy would audit against a threshold the pipeline no
    longer uses."""
    from agents.matcher import SkillMatcherAgent

    assert semantic_threshold() == SkillMatcherAgent.SEMANTIC_THRESHOLD


def test_committed_semantic_scores_are_below_the_threshold():
    threshold = semantic_threshold()
    for term, (_, score) in sorted(ADMITTED.items()):
        assert score < threshold, f"{term} at {score}"


def test_audit_without_an_encoder_never_reports_admitted():
    """Rule S not having run is not the same as having passed, and the weaker
    result must not be silently upgraded."""
    verdicts = audit(["COBOL", "Python"], encoder=None)
    assert admitted_terms(verdicts) == []


def test_audit_runs_rule_s_only_on_lexical_survivors():
    """Encoding is the expensive step; a lexically-rejected candidate is already
    out and must not be paid for."""
    seen = []

    def encoder(texts):
        import numpy as np

        seen.append(list(texts))
        return np.zeros((len(texts), 4))

    audit(["COBOL", "Python"], encoder=encoder, keywords={"python"})
    encoded_terms = [t for batch in seen for t in batch]
    assert "COBOL" in encoded_terms
    assert "Python" not in encoded_terms


def test_verdict_reason_names_the_conflicting_keyword():
    verdict = Verdict("Perl", False, ("perl",))
    assert "perl" in verdict.reason


# ── answer-key invariance, through the real matcher ───────────────────────────

JOB_SKILLS = [
    ("Python", True, 0.9),
    ("SQL", True, 0.8),
    ("Kubernetes", True, 0.7),   # the candidate lacks this one
    ("Terraform", False, 0.5),   # and this one
]

USER_SKILLS = ["Python", "SQL", "pandas"]


@pytest.fixture
def matcher_engine(isolated_engine, monkeypatch):
    """`isolated_engine` plus the matcher's own module-level engine reference.

    `agents/matcher.py` binds `engine` at import time and is not in conftest's
    patch list (#175), so without this the test reads whichever database the
    first matcher-importing test in the session created.
    """
    import agents.matcher as matcher_module

    monkeypatch.setattr(matcher_module, "engine", isolated_engine)
    # The semantic channel is exercised by the integration test below; here it is
    # switched off so the fast suite loads no model and the other three channels
    # are asserted deterministically.
    monkeypatch.setattr(matcher_module, "get_embedding_model", lambda: None)
    return isolated_engine


def _seed_user(engine, skill_names):
    from database.models import User

    with Session(engine) as session:
        user = User(name="Distractor Fixture", email="distractors@example.com")
        session.add(user)
        session.commit()
        session.refresh(user)
        for name in skill_names:
            skill = Skill(name=name, category="Tool")
            session.add(skill)
            session.flush()
            session.add(UserSkill(user_id=user.user_id, skill_id=skill.skill_id))
        session.commit()
        return user.user_id


def _seed_job(engine):
    with Session(engine) as session:
        job = JobDescription(title="Data Engineer", company="Fixture Co",
                             description="Python, SQL, Kubernetes, Terraform.",
                             status="analyzed")
        session.add(job)
        session.commit()
        session.refresh(job)
        for name, required, weight in JOB_SKILLS:
            skill = Skill(name=name, category="Tool")
            session.add(skill)
            session.flush()
            session.add(JobSkill(job_id=job.job_id, skill_id=skill.skill_id,
                                 required=required, weight=weight))
        session.commit()
        return job.job_id


def _add_skills(engine, user_id, names):
    with Session(engine) as session:
        for name in names:
            skill = Skill(name=name, category="Tool")
            session.add(skill)
            session.flush()
            session.add(UserSkill(user_id=user_id, skill_id=skill.skill_id))
        session.commit()


def _answer_key(user_id, job_id):
    from agents.matcher import SkillMatcherAgent

    result = SkillMatcherAgent().match(user_id, job_id)
    return {
        "matched": {k: dict(v) for k, v in (result.matched_skills or {}).items()},
        "missing": list(result.missing_skills or []),
        "ats_score": result.ats_score,
    }


def test_injecting_every_admitted_distractor_does_not_move_the_answer_key(
        matcher_engine):
    """The acceptance criterion: profile size changes, labels do not.

    All 31 at once rather than one at a time — a pool is injected as a set, and
    an interaction between two distractors would be invisible to a per-term test.
    """
    user_id = _seed_user(matcher_engine, USER_SKILLS)
    job_id = _seed_job(matcher_engine)

    before = _answer_key(user_id, job_id)
    _add_skills(matcher_engine, user_id, sorted(ADMITTED))
    after = _answer_key(user_id, job_id)

    assert after == before


def test_the_fixture_would_notice_a_relevant_skill(matcher_engine):
    """Control: the same procedure with a *genuinely relevant* skill must move
    the key. Without this, a matcher that ignored user skills entirely would pass
    the invariance test above.
    """
    user_id = _seed_user(matcher_engine, USER_SKILLS)
    job_id = _seed_job(matcher_engine)

    before = _answer_key(user_id, job_id)
    _add_skills(matcher_engine, user_id, ["Kubernetes"])
    after = _answer_key(user_id, job_id)

    assert after != before
    assert "Kubernetes" in after["matched"]
    assert "Kubernetes" in before["missing"]


def test_profile_size_really_did_change(matcher_engine):
    """Guards the other half of the claim: 'changes profile size without changing
    any label' is two assertions, and only one of them is about labels."""
    user_id = _seed_user(matcher_engine, USER_SKILLS)
    with Session(matcher_engine) as session:
        before = len(session.exec(
            UserSkill.__table__.select().where(
                UserSkill.user_id == user_id)).all())
    _add_skills(matcher_engine, user_id, sorted(ADMITTED))
    with Session(matcher_engine) as session:
        after = len(session.exec(
            UserSkill.__table__.select().where(
                UserSkill.user_id == user_id)).all())
    assert after == before + len(ADMITTED)


@pytest.mark.integration
@pytest.mark.slow
def test_answer_key_invariance_holds_with_the_semantic_channel_live(
        isolated_engine, monkeypatch):
    """The same invariance with the real encoder, so rule S is exercised rather
    than assumed. Integration-gated for the model load."""
    import agents.matcher as matcher_module

    monkeypatch.setattr(matcher_module, "engine", isolated_engine)
    user_id = _seed_user(isolated_engine, USER_SKILLS)
    job_id = _seed_job(isolated_engine)

    before = _answer_key(user_id, job_id)
    _add_skills(isolated_engine, user_id, sorted(ADMITTED))
    after = _answer_key(user_id, job_id)

    assert after == before


@pytest.mark.integration
@pytest.mark.slow
def test_committed_admission_still_matches_the_live_audit():
    """The commitment cannot drift from the corpus and model it describes."""
    from eval.distractors import live_encoder

    verdicts = audit(DISTRACTOR_CANDIDATES, live_encoder())
    assert admitted_terms(verdicts) == sorted(ADMITTED)
    for verdict in verdicts:
        if verdict.admitted:
            keyword, score = ADMITTED[verdict.term]
            assert verdict.nearest_keyword == keyword
            assert verdict.nearest_score == pytest.approx(score, abs=5e-3)
