"""Per-user education storage and rendering (issue #73).

The formatter previously hardcoded one user's education into every export,
leaking it to all users. These tests pin the fix: education comes from the
per-user Education table, and a user with no rows gets no education section.
"""
import io

import pytest
from sqlmodel import Session, select

import agents.formatter as fmt_module
import agents.parser as parser_module
from agents.formatter import ResumeFormatterAgent
from database.models import Education, User

# The strings that used to be hardcoded — they must never appear for a user
# who does not have them in the DB.
_LEAKED_MARKERS = [
    "University of California, San Diego",
    "M.S. Data Science",
    "Mathematics",
]


def _make_user(engine, email="edu@example.com", name="Edu User") -> User:
    with Session(engine) as s:
        user = User(name=name, email=email)
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _seed_education(engine, user_id, entries):
    with Session(engine) as s:
        for e in entries:
            s.add(Education(user_id=user_id, **e))
        s.commit()


def _make_parser(isolated_engine, monkeypatch, user):
    """ResumeParserAgent wired to the test DB, bypassing __init__ (no LLM)."""
    monkeypatch.setattr(parser_module, "engine", isolated_engine)
    agent = parser_module.ResumeParserAgent.__new__(parser_module.ResumeParserAgent)
    agent.user = user
    agent.llm = None
    return agent


_ENTRIES = [
    {
        "institution": "Texas A&M University",
        "degree": "B.S. Computer Science",
        "location": "College Station, TX",
        "end_date": "May 2021",
        "gpa": "3.8/4.0",
    },
    {
        "institution": "Southwestern University",
        "degree": "A.A. Liberal Arts",
        "location": "Georgetown, TX",
        "start_date": "Aug 2015",
        "end_date": "May 2017",
    },
]


# ── LaTeX path ────────────────────────────────────────────────────────────────

def test_tex_education_renders_user_rows(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)
    _seed_education(isolated_engine, user.user_id, _ENTRIES)

    tex = ResumeFormatterAgent(user.user_id)._build_tex({})

    assert r"Texas A\&M University" in tex
    assert "B.S. Computer Science, GPA: 3.8/4.0" in tex
    assert "Southwestern University" in tex
    assert "Aug 2015 -- May 2017" in tex
    for marker in _LEAKED_MARKERS:
        assert marker not in tex


def test_tex_education_omitted_when_no_rows(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)

    tex = ResumeFormatterAgent(user.user_id)._build_tex({})

    assert r"\section{Education}" not in tex
    for marker in _LEAKED_MARKERS:
        assert marker not in tex


def test_tex_education_isolated_between_users(isolated_engine, monkeypatch):
    """User B's export must not contain user A's education."""
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user_a = _make_user(isolated_engine, email="a@example.com", name="User A")
    user_b = _make_user(isolated_engine, email="b@example.com", name="User B")
    _seed_education(isolated_engine, user_a.user_id, [_ENTRIES[0]])
    _seed_education(
        isolated_engine, user_b.user_id,
        [{"institution": "MIT", "degree": "B.S. Physics", "end_date": "June 2020"}],
    )

    tex_b = ResumeFormatterAgent(user_b.user_id)._build_tex({})

    assert "MIT" in tex_b
    assert "Texas" not in tex_b


# ── DOCX path ─────────────────────────────────────────────────────────────────

def test_docx_education_renders_user_rows(isolated_engine, monkeypatch):
    from docx import Document

    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)
    _seed_education(isolated_engine, user.user_id, [_ENTRIES[0]])

    docx_bytes = ResumeFormatterAgent(user.user_id).format_docx({})
    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)

    assert "Texas A&M University" in text
    assert "B.S. Computer Science, GPA: 3.8/4.0" in text
    for marker in _LEAKED_MARKERS:
        assert marker not in text


def test_docx_education_omitted_when_no_rows(isolated_engine, monkeypatch):
    from docx import Document

    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)

    docx_bytes = ResumeFormatterAgent(user.user_id).format_docx({})
    text = "\n".join(p.text for p in Document(io.BytesIO(docx_bytes)).paragraphs)

    assert "EDUCATION" not in text.upper()
    for marker in _LEAKED_MARKERS:
        assert marker not in text


# ── Markdown (deprecated) path ────────────────────────────────────────────────

def test_markdown_education_from_db(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)
    _seed_education(isolated_engine, user.user_id, [_ENTRIES[0]])

    block = ResumeFormatterAgent(user.user_id)._build_education()

    assert "**Texas A&M University**" in block
    assert "B.S. Computer Science" in block
    for marker in _LEAKED_MARKERS:
        assert marker not in block


def test_markdown_education_empty_when_no_rows(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _make_user(isolated_engine)
    assert ResumeFormatterAgent(user.user_id)._build_education() == ""


# ── Parser: resume extraction save + dedup ────────────────────────────────────

def test_save_education_persists_rows(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_education(
        [{"institution": "Texas A&M University", "degree": "B.S. Computer Science",
          "location": "College Station, TX", "end_date": "May 2021", "gpa": 3.8}],
        "resume",
    )

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].institution == "Texas A&M University"
    assert rows[0].gpa == "3.8"  # numeric GPA coerced to string


def test_save_education_dedups_on_reingest(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)
    data = [{"institution": "Texas A&M University", "degree": "B.S. Computer Science"}]

    agent._save_education(data, "resume")
    agent._save_education(data, "resume")

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1


def test_save_education_skips_blank_institution(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_education([{"institution": "  ", "degree": "B.S."}], "resume")

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert rows == []


# ── Parser: LinkedIn structured mapping ───────────────────────────────────────

_LINKEDIN_RECORD = {
    "education": [
        {"title": "UCSD", "degree": "BS", "field": "CS",
         "start_year": "2019", "end_year": "2023"},
    ],
}


def test_linkedin_structured_education_saved(isolated_engine, monkeypatch):
    user = _make_user(isolated_engine)
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_LINKEDIN_RECORD)

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].institution == "UCSD"
    assert rows[0].degree == "BS, CS"
    assert rows[0].start_date == "2019"
    assert rows[0].end_date == "2023"


def test_linkedin_education_merges_with_existing(isolated_engine, monkeypatch):
    """A resume-ingested institution is not duplicated by the LinkedIn record."""
    user = _make_user(isolated_engine)
    _seed_education(
        isolated_engine, user.user_id,
        [{"institution": "UCSD", "degree": "B.S. Computer Science"}],
    )
    agent = _make_parser(isolated_engine, monkeypatch, user)

    agent._save_linkedin_structured(_LINKEDIN_RECORD)
    agent._save_linkedin_structured(_LINKEDIN_RECORD)  # re-ingest is idempotent too

    with Session(isolated_engine) as s:
        rows = s.exec(select(Education).where(Education.user_id == user.user_id)).all()
    assert len(rows) == 1
    assert rows[0].degree == "B.S. Computer Science"
