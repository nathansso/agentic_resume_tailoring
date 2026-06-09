"""Tests for the LaTeX resume formatter (issue: LaTeX export pipeline).

Fast tests (no pdflatex): verify .tex source structure and escaping.
Integration test (requires pdflatex): marked @pytest.mark.integration.
"""
import shutil
import warnings
from uuid import uuid4

import pytest
from sqlmodel import Session

from agents.formatter import (
    ResumeFormatterAgent,
    _compile_tex_to_pdf,
    _convert_inline,
    _escape_tex,
)
from database.models import Skill, User, UserSkill


# ── fixtures ──────────────────────────────────────────────────────────────────

_SAMPLE_CONTENT = {
    "experiences": [
        {
            "title": "Software Engineer",
            "company": "Acme Corp",
            "start_date": "Jan 2023",
            "end_date": "Present",
            "location": "San Diego, CA",
            "bullets": [
                "**Built** a distributed cache reducing API latency by 40%",
                "Led team of 5 engineers across 3 time zones",
            ],
        }
    ],
    "projects": [
        {
            "name": "ART Resume Tailoring",
            "tech_stack": "Python, FastAPI, React",
            "dates": "2024",
            "bullets": [
                "**Designed** an agentic pipeline for resume tailoring",
            ],
        }
    ],
    "skills_emphasized": ["Python", "FastAPI"],
}


def _seed_user(engine, email="latex@example.com") -> User:
    with Session(engine) as s:
        user = User(name="Nathaniel Oliver", email=email)
        user.phone = "619-555-0100"
        user.location = "San Diego, CA"
        user.github_username = "nathansso"
        user.linkedin_url = "https://linkedin.com/in/nathanoliver"
        s.add(user)
        s.commit()
        s.refresh(user)
        return user


def _seed_skill(engine, user_id, name, category="language"):
    with Session(engine) as s:
        skill = Skill(name=name, category=category)
        s.add(skill)
        s.commit()
        s.refresh(skill)
        us = UserSkill(
            user_id=user_id,
            skill_id=skill.skill_id,
            confidence_score=0.9,
        )
        s.add(us)
        s.commit()


# ── _escape_tex ───────────────────────────────────────────────────────────────

def test_escape_ampersand():
    assert _escape_tex("Data & Science") == r"Data \& Science"

def test_escape_percent():
    assert _escape_tex("50% faster") == r"50\% faster"

def test_escape_dollar():
    assert _escape_tex("$100k salary") == r"\$100k salary"

def test_escape_hash():
    assert _escape_tex("issue #42") == r"issue \#42"

def test_escape_underscore():
    assert _escape_tex("snake_case") == r"snake\_case"

def test_escape_backslash_first():
    # backslash must not be double-escaped
    result = _escape_tex("a\\b")
    assert result == r"a\textbackslash{}b"
    assert result.count(r"\textbackslash{}") == 1


# ── _convert_inline ───────────────────────────────────────────────────────────

def test_convert_bold():
    result = _convert_inline("**Built** a feature")
    assert r"\textbf{Built}" in result
    assert "**" not in result

def test_convert_italic():
    result = _convert_inline("*Python* developer")
    assert r"\textit{Python}" in result

def test_convert_mixed():
    result = _convert_inline("**Led** a *cross-functional* team")
    assert r"\textbf{Led}" in result
    assert r"\textit{cross" in result

def test_convert_escapes_outside_markers():
    result = _convert_inline("Increased revenue by 50% with **Python & Go**")
    assert r"50\%" in result
    assert r"\textbf{Python \& Go}" in result

def test_convert_no_markers():
    result = _convert_inline("plain text with 100% coverage")
    assert r"100\%" in result
    assert "**" not in result


# ── .tex source structure ─────────────────────────────────────────────────────

def test_tex_contains_jake_commands(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    # Jake's custom commands must be present
    assert r"\resumeSubHeadingListStart" in tex
    assert r"\resumeSubHeadingListEnd" in tex
    assert r"\resumeSubheading" in tex
    assert r"\resumeProjectHeading" in tex
    assert r"\resumeItemListStart" in tex
    assert r"\resumeItemListEnd" in tex
    assert r"\resumeItem{" in tex


def test_tex_document_structure(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    assert r"\documentclass[letterpaper,11pt]{article}" in tex
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex
    assert r"\pdfgentounicode=1" in tex


def test_tex_header_contains_name_and_contact(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    assert "Nathaniel Oliver" in tex
    assert "619" in tex                     # phone
    assert "linkedin.com/in" in tex         # linkedin
    assert "github.com/nathansso" in tex    # github


def test_tex_experience_section(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    assert "Software Engineer" in tex
    assert "Acme Corp" in tex
    assert "Jan 2023" in tex
    # bullet content (bold marker converted to \textbf)
    assert r"\textbf{Built}" in tex


def test_tex_project_section(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    assert "ART Resume Tailoring" in tex
    assert r"\emph{Python, FastAPI, React}" in tex


def test_tex_skills_section(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    _seed_skill(isolated_engine, user.user_id, "Python", "language")
    _seed_skill(isolated_engine, user.user_id, "React", "framework")

    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT)

    assert r"\section{Skills}" in tex or r"\section{skills}" in tex.lower()
    assert "Python" in tex
    assert r"\textbf{Languages" in tex


def test_tex_special_chars_escaped_in_content(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    content = {
        "experiences": [
            {
                "title": "Engineer & Lead",
                "company": "50% Corp",
                "bullets": ["Managed $1M budget"],
            }
        ],
        "projects": [],
        "skills_emphasized": [],
    }
    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(content)

    assert r"Engineer \& Lead" in tex
    assert r"50\% Corp" in tex
    assert r"\$1M budget" in tex


def test_section_order_respected(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)
    tex = agent._build_tex(_SAMPLE_CONTENT, section_order=["projects", "experience", "skills", "education"])

    proj_pos = tex.find(r"\section{Projects}")
    exp_pos  = tex.find(r"\section{Experience}")
    assert proj_pos < exp_pos, "Projects section should appear before Experience"


# ── deprecated format_markdown() ─────────────────────────────────────────────

def test_format_markdown_emits_deprecation_warning(isolated_engine, monkeypatch):
    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    agent = ResumeFormatterAgent(user.user_id)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        agent.format_markdown(_SAMPLE_CONTENT)

    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ── integration: full pdflatex compile ───────────────────────────────────────

@pytest.mark.integration
def test_pdflatex_produces_valid_pdf(isolated_engine, monkeypatch):
    """Requires pdflatex to be installed on the host."""
    if not shutil.which("pdflatex"):
        pytest.skip("pdflatex not installed")

    import agents.formatter as fmt_module
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)

    user = _seed_user(isolated_engine)
    _seed_skill(isolated_engine, user.user_id, "Python", "language")

    agent = ResumeFormatterAgent(user.user_id)
    pdf_bytes = agent.format_pdf(_SAMPLE_CONTENT)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes[:4] == b"%PDF"  # valid PDF magic bytes
