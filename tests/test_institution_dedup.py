"""Institution canonicalization + cross-source dedup (issue #95).

Resume and LinkedIn record the same school under different name forms
('UC San Diego' vs 'University of California, San Diego'). ROR's affiliation
matcher resolves both to one canonical id so the rows deduplicate, while
genuinely distinct degrees at one school (B.S. vs M.S., or two same-level
majors) stay separate.

These tests never touch the network: `_query_ror` is stubbed and ROR is enabled
per-test via the `ROR_LOOKUP_ENABLED` env (the suite disables it by default).
"""
import pytest
from sqlmodel import Session, select

import institution as inst
import agents.parser as parser_module
from agents.parser import ResumeParserAgent
from database.models import Education, Experience, InstitutionCanonical, User

_UCSD_ROR = "https://ror.org/0168r3w48"


def _fake_ror(name: str):
    """Stub ROR: any UCSD-ish string resolves to the one canonical id; a marked
    'unknown' string resolves to nothing; everything else is a clean no-match."""
    low = (name or "").lower()
    if "san diego" in low and ("uc" in low or "california" in low):
        return (_UCSD_ROR, "University of California San Diego")
    return None


@pytest.fixture()
def ror_on(monkeypatch):
    """Enable ROR with the stubbed matcher for a single test."""
    monkeypatch.setenv("ROR_LOOKUP_ENABLED", "1")
    monkeypatch.setattr(inst, "_query_ror", _fake_ror)
    inst._MEMO.clear()


def _make_user(engine, email="i95@example.com") -> User:
    with Session(engine) as s:
        user = User(name="I95", email=email)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _make_parser(isolated_engine, monkeypatch, user):
    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    agent = ResumeParserAgent.__new__(ResumeParserAgent)
    agent.user = user
    agent.llm = None
    return agent


# ── canonicalize_institution: resolution, caching, fallback ──────────────────

def test_canonicalize_resolves_variants_to_same_key(isolated_engine, ror_on):
    a = inst.canonicalize_institution("UC San Diego")
    b = inst.canonicalize_institution("University of California, San Diego")
    assert a == b == _UCSD_ROR


def test_canonicalize_caches_after_first_lookup(isolated_engine, ror_on, monkeypatch):
    calls = {"n": 0}

    def counting(name):
        calls["n"] += 1
        return _fake_ror(name)

    monkeypatch.setattr(inst, "_query_ror", counting)
    inst._MEMO.clear()

    inst.canonicalize_institution("UC San Diego")
    inst._MEMO.clear()  # force the second call to fall through to the DB cache
    inst.canonicalize_institution("UC San Diego")

    assert calls["n"] == 1  # network hit once; second lookup served from cache
    with Session(isolated_engine) as s:
        row = s.get(InstitutionCanonical, "uc san diego")
    assert row is not None and row.canonical_key == _UCSD_ROR


def test_canonicalize_unmatched_name_caches_self(isolated_engine, ror_on):
    key = inst.canonicalize_institution("Some Bootcamp LLC")
    assert key == "some bootcamp llc"  # normalized fallback
    with Session(isolated_engine) as s:
        row = s.get(InstitutionCanonical, "some bootcamp llc")
    assert row is not None and row.canonical_key == "some bootcamp llc"


def test_canonicalize_transient_failure_not_cached(isolated_engine, monkeypatch):
    monkeypatch.setenv("ROR_LOOKUP_ENABLED", "1")
    inst._MEMO.clear()

    def boom(name):
        raise inst._RORUnavailable("network down")

    monkeypatch.setattr(inst, "_query_ror", boom)

    assert inst.canonicalize_institution("UC San Diego") == "uc san diego"
    with Session(isolated_engine) as s:
        row = s.get(InstitutionCanonical, "uc san diego")
    assert row is None  # transient failure must stay uncached so a later ingest retries


def test_canonicalize_disabled_returns_normalized(isolated_engine, monkeypatch):
    monkeypatch.setenv("ROR_LOOKUP_ENABLED", "0")
    inst._MEMO.clear()
    # No stub: if this hit the network the test would fail/flake.
    assert inst.canonicalize_institution("UC San Diego") == "uc san diego"


# ── Education dedup across institution name forms (the issue #95 case) ────────

def test_education_dedups_across_institution_forms(isolated_engine, monkeypatch, ror_on):
    """The literal #95 rows: full-name resume entry + blank-degree LinkedIn entry
    under the abbreviated school name collapse to one, backfilling the blanks."""
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_education(
        [{"institution": "University of California, San Diego",
          "degree": "B.S. Mathematics & Economics, Minor in Data Science",
          "location": "La Jolla, CA", "end_date": "June 2025"}],
        "resume",
    )
    agent._save_education(
        [{"institution": "UC San Diego", "degree": "", "start_date": "2021-09"}],
        "linkedin",
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.degree.startswith("B.S. Mathematics")
    assert row.end_date == "June 2025"
    assert row.start_date == "2021-09"  # backfilled from the LinkedIn row


def test_heal_education_collapses_existing_variant_forms(isolated_engine, monkeypatch, ror_on):
    """Self-heal cleans a dirty DB that already has the two institution forms."""
    user = _make_user(isolated_engine)
    with Session(isolated_engine) as s:
        s.add(Education(user_id=user.user_id,
                        institution="University of California, San Diego",
                        degree="B.S. Mathematics & Economics", end_date="June 2025"))
        s.add(Education(user_id=user.user_id, institution="UC San Diego", degree=""))
        s.commit()

    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    with Session(isolated_engine) as s:
        removed = ResumeParserAgent._heal_education(s, user.user_id)
        s.commit()
    assert removed == 1
    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1


def test_education_keeps_ms_and_bs_distinct_across_forms(isolated_engine, monkeypatch, ror_on):
    """Even once the two name forms canonicalize together, a B.S. and an M.S. at
    the same school remain distinct rows (the user's real UCSD case)."""
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)
    agent._save_education(
        [{"institution": "University of California, San Diego",
          "degree": "B.S. Mathematics & Economics"}],
        "resume",
    )
    agent._save_education(
        [{"institution": "UC San Diego", "degree": "M.S. Data Science"}],
        "linkedin",
    )
    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 2


# ── Degree distinctness: same level, different field ─────────────────────────

def test_same_level_different_major_stays_distinct():
    assert not ResumeParserAgent._education_match(
        "UCSD", "B.S. Mathematics", "UCSD", "B.S. Physics")


def test_mba_and_ms_stay_distinct():
    assert not ResumeParserAgent._education_match(
        "UCSD", "MBA", "UCSD", "M.S. Data Science")


def test_field_acronym_merges():
    """Abbreviated same-field degrees still merge: 'CS' == 'Computer Science'."""
    assert ResumeParserAgent._education_match(
        "UCSD", "B.S. Computer Science", "UCSD", "BS, CS")


def test_blank_degree_still_merges():
    assert ResumeParserAgent._education_match(
        "UCSD", "B.S. Mathematics & Economics", "UCSD", "")


# ── Experiences: academic employer name forms merge ──────────────────────────

def test_experience_merges_across_employer_name_forms(isolated_engine, monkeypatch, ror_on):
    """The Financial Assistant role at UCSD, ingested under two employer name
    forms, folds into one row instead of duplicating (issue #95)."""
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_experiences(
        [{"title": "Financial Assistant", "company": "UC San Diego",
          "start_date": "2023-09", "end_date": "2025-06",
          "bullets": ["Prepared invoices and purchase orders."]}],
        "linkedin",
    )
    agent._save_experiences(
        [{"title": "Financial Assistant",
          "company": "University of California, San Diego"}],
        "resume",
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(Experience).where(Experience.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].bullets == ["Prepared invoices and purchase orders."]


def test_experience_match_falls_back_for_companies():
    """Non-academic employers ROR can't resolve still fuzzy-match on the name
    (unchanged behavior): 'IDXExchange' folds into 'IDX Exchange'."""
    assert ResumeParserAgent._experiences_match(
        "Data Science Intern", "IDXExchange",
        "Data Science Intern", "IDX Exchange")
