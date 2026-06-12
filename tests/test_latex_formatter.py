"""Tests for the LaTeX/docx resume formatter.

Fast tests (no pdflatex): verify .tex source structure and escaping.
Integration test (requires pdflatex): marked @pytest.mark.integration.

Test data mirrors Jake's resume sample content so the generated .tex can be
dropped directly into Overleaf against his template for visual verification.
"""
import io
import os
import shutil
import warnings

import pytest
from sqlmodel import Session

import agents.formatter as fmt_module
from agents.formatter import (
    ResumeFormatterAgent,
    _compile_tex_to_pdf,
    _convert_inline,
    _escape_tex,
)
from database.models import Skill, User, UserSkill


# ── Jake-style sample data ────────────────────────────────────────────────────

_JAKE_CONTENT = {
    "experiences": [
        {
            "title": "Undergraduate Research Assistant",
            "company": "Texas A&M University",
            "start_date": "June 2020",
            "end_date": "Present",
            "location": "College Station, TX",
            "bullets": [
                "Developed a REST API using FastAPI and PostgreSQL to store data from learning management systems",
                "Developed a full-stack web application using Flask, React, PostgreSQL and Docker to analyze GitHub data",
                "Explored ways to visualize GitHub collaboration in a classroom setting",
            ],
        },
        {
            "title": "Information Technology Support Specialist",
            "company": "Southwestern University",
            "start_date": "Sep. 2018",
            "end_date": "Present",
            "location": "Georgetown, TX",
            "bullets": [
                "Communicate with managers to set up campus computers used on campus",
                "Assess and troubleshoot computer problems brought by students, faculty and staff",
                "Maintain upkeep of computers, classroom equipment, and 200 printers across campus",
            ],
        },
    ],
    "projects": [
        {
            "name": "Gitlytics",
            "tech_stack": "Python, Flask, React, PostgreSQL, Docker",
            "dates": "June 2020 -- Present",
            "bullets": [
                "Developed a full-stack web application using Flask serving a REST API with React as the frontend",
                "Implemented GitHub OAuth to get data from user's repositories",
                "Visualized GitHub data to show collaboration",
                "Used Celery and Redis for asynchronous tasks",
            ],
        },
        {
            "name": "Simple Paintball",
            "tech_stack": "Spigot API, Java, Maven, TravisCI, Git",
            "dates": "May 2018 -- May 2020",
            "bullets": [
                "Developed a Minecraft server plugin to entertain kids during free time for a previous job",
                "Published plugin to websites gaining 2K+ downloads and an average 4.5/5-star review",
                "Implemented continuous delivery using TravisCI to build the plugin upon new a release",
            ],
        },
    ],
    "skills_emphasized": ["Python", "Java", "React"],
}


def _seed_jake_user(engine) -> User:
    with Session(engine) as s:
        user = User(
            name="Jake Ryan",
            email="jake@su.edu",
            phone="123-456-7890",
            location="Georgetown, TX",
            github_username="jake",
            linkedin_url="https://linkedin.com/in/jake",
        )
        s.add(user)
        s.commit()
        s.refresh(user)

        for name, cat in [
            ("Java", "language"),
            ("Python", "language"),
            ("C/C++", "language"),
            ("SQL", "language"),
            ("JavaScript", "language"),
            ("React", "framework"),
            ("Node.js", "framework"),
            ("Flask", "framework"),
            ("FastAPI", "framework"),
            ("Git", "tool"),
            ("Docker", "tool"),
            ("TravisCI", "tool"),
        ]:
            sk = Skill(name=name, category=cat)
            s.add(sk)
            s.commit()
            s.refresh(sk)
            s.add(UserSkill(user_id=user.user_id, skill_id=sk.skill_id, confidence_score=0.9))
        s.commit()
        s.refresh(user)
        return user


# ── _escape_tex ───────────────────────────────────────────────────────────────

def test_escape_ampersand():
    assert _escape_tex("Texas A&M University") == r"Texas A\&M University"

def test_escape_percent():
    assert _escape_tex("95% coverage") == r"95\% coverage"

def test_escape_dollar():
    assert _escape_tex("$1M budget") == r"\$1M budget"

def test_escape_hash():
    assert _escape_tex("issue #42") == r"issue \#42"

def test_escape_underscore():
    assert _escape_tex("snake_case_var") == r"snake\_case\_var"

def test_escape_backslash_single_pass():
    result = _escape_tex("a\\b")
    assert result == r"a\textbackslash{}b"
    assert result.count(r"\textbackslash{}") == 1

def test_escape_braces():
    assert _escape_tex("{hello}") == r"\{hello\}"

def test_escape_tilde():
    assert _escape_tex("~") == r"\textasciitilde{}"


# ── _convert_inline ───────────────────────────────────────────────────────────

def test_convert_bold_to_textbf():
    result = _convert_inline("**Developed** a REST API")
    assert r"\textbf{Developed}" in result
    assert "**" not in result

def test_convert_italic_to_textit():
    result = _convert_inline("based on *The Legend of Zelda*")
    assert r"\textit{The Legend of Zelda}" in result

def test_convert_escapes_special_chars_outside_markers():
    result = _convert_inline("Contributed 50% via Git & GitHub")
    assert r"50\%" in result
    assert r"Git \& GitHub" in result

def test_convert_bold_content_with_special_chars():
    result = _convert_inline("**Python & Go**")
    assert r"\textbf{Python \& Go}" in result

def test_convert_no_markers_still_escapes():
    result = _convert_inline("2K+ downloads at 4.5/5 stars")
    assert "**" not in result
    assert result == r"2K+ downloads at 4.5/5 stars"


# ── .tex document structure ───────────────────────────────────────────────────

def test_tex_documentclass(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\documentclass[letterpaper,11pt]{article}" in tex

def test_tex_jake_preamble_packages(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    for pkg in [r"\usepackage{marvosym}", r"\usepackage{latexsym}",
                r"\usepackage{enumitem}", r"\usepackage[hidelinks]{hyperref}",
                r"\usepackage{fancyhdr}", r"\usepackage{tabularx}",
                r"\usepackage{titlesec}", r"\pdfgentounicode=1"]:
        assert pkg in tex, f"Missing package: {pkg}"

def test_tex_jake_custom_commands(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    for cmd in [r"\resumeItem", r"\resumeSubheading", r"\resumeSubSubheading",
                r"\resumeProjectHeading", r"\resumeSubItem",
                r"\resumeSubHeadingListStart", r"\resumeSubHeadingListEnd",
                r"\resumeItemListStart", r"\resumeItemListEnd"]:
        assert cmd in tex, f"Missing command definition: {cmd}"

def test_tex_document_begin_end(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\begin{document}" in tex
    assert r"\end{document}" in tex

def test_tex_header_name_and_contact(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\textbf{\Huge \scshape Jake Ryan}" in tex
    assert "123-456-7890" in tex
    assert r"mailto:jake@su.edu" in tex
    assert "linkedin.com/in/jake" in tex
    assert "github.com/jake" in tex

def test_tex_experience_uses_resumesubheading(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    # arg1=title, arg2=dates, arg3=company, arg4=location (Jake's order)
    assert "{Undergraduate Research Assistant}{June 2020 -- Present}" in tex
    assert "{Texas A\\&M University}{College Station, TX}" in tex

def test_tex_experience_bullets_use_resumeitem(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\resumeItemListStart" in tex
    assert r"\resumeItemListEnd" in tex
    assert r"\resumeItem{Developed a REST API" in tex

def test_tex_projects_use_resumeprojectheading(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\resumeProjectHeading" in tex
    assert r"\textbf{Gitlytics} $|$ \emph{Python, Flask, React, PostgreSQL, Docker}" in tex
    assert "{June 2020 -- Present}" in tex

def test_tex_skills_section_is_technical_skills(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(_JAKE_CONTENT)
    assert r"\section{Technical Skills}" in tex
    assert r"\textbf{Languages \& Libraries}" in tex

def test_tex_section_order_respected(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(
        _JAKE_CONTENT, section_order=["projects", "experience", "skills", "education"]
    )
    assert tex.index(r"\section{Projects}") < tex.index(r"\section{Experience}")

def test_tex_header_stays_above_sections_with_custom_order(isolated_engine, monkeypatch):
    """Name/contact header is pinned at the top regardless of section_order (issue 22)."""
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(
        _JAKE_CONTENT, section_order=["projects", "skills", "experience", "education"]
    )
    first_section = tex.index(r"\section{")
    assert tex.index("Jake Ryan") < first_section
    assert tex.index("123-456-7890") < first_section

def test_tex_special_chars_escaped(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    content = {
        "experiences": [{
            "title": "Engineer & Lead",
            "company": "50% Corp",
            "start_date": "Jan 2023",
            "end_date": "Present",
            "location": "",
            "bullets": ["Managed $1M budget across 3 accounts"],
        }],
        "projects": [],
        "skills_emphasized": [],
    }
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id)._build_tex(content)
    assert r"Engineer \& Lead" in tex
    assert r"50\% Corp" in tex
    assert r"\$1M budget" in tex


# ── format_tex() ──────────────────────────────────────────────────────────────

def test_format_tex_returns_string(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id).format_tex(_JAKE_CONTENT)
    assert isinstance(tex, str)
    assert r"\documentclass" in tex


# ── format_docx() ─────────────────────────────────────────────────────────────

def test_format_docx_returns_bytes(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    result = ResumeFormatterAgent(user.user_id).format_docx(_JAKE_CONTENT)
    assert isinstance(result, bytes)
    assert len(result) > 500

def test_format_docx_is_valid_docx(isolated_engine, monkeypatch):
    """Verify the bytes can be opened as a Word document."""
    from docx import Document
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    docx_bytes = ResumeFormatterAgent(user.user_id).format_docx(_JAKE_CONTENT)
    doc = Document(io.BytesIO(docx_bytes))
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "Jake Ryan" in full_text
    assert "Gitlytics" in full_text


# ── format_markdown() deprecation ────────────────────────────────────────────

def test_format_markdown_emits_deprecation_warning(isolated_engine, monkeypatch):
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ResumeFormatterAgent(user.user_id).format_markdown(_JAKE_CONTENT)
    assert any(issubclass(w.category, DeprecationWarning) for w in caught)


# ── save .tex to Downloads for manual Overleaf verification ──────────────────

def test_save_tex_to_downloads(isolated_engine, monkeypatch):
    """Generate a Jake-layout .tex and save to ~/Downloads for Overleaf inspection."""
    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    tex = ResumeFormatterAgent(user.user_id).format_tex(_JAKE_CONTENT)

    downloads = os.path.join(os.path.expanduser("~"), "Downloads", "jake_resume_art.tex")
    with open(downloads, "w", encoding="utf-8") as f:
        f.write(tex)

    assert os.path.exists(downloads)
    assert os.path.getsize(downloads) > 1000


# ── integration: full pdflatex compile ───────────────────────────────────────

@pytest.mark.integration
def test_pdflatex_produces_valid_pdf(isolated_engine, monkeypatch):
    """Requires pdflatex. Verifies the .tex compiles and output is a valid PDF."""
    if not shutil.which("pdflatex"):
        pytest.skip("pdflatex not installed")

    monkeypatch.setattr(fmt_module, "engine", isolated_engine)
    user = _seed_jake_user(isolated_engine)
    pdf_bytes = ResumeFormatterAgent(user.user_id).format_pdf(_JAKE_CONTENT)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 1000
    assert pdf_bytes[:4] == b"%PDF"

    # Also save PDF to Downloads for visual inspection
    downloads = os.path.join(os.path.expanduser("~"), "Downloads", "jake_resume_art.pdf")
    with open(downloads, "wb") as f:
        f.write(pdf_bytes)
    assert os.path.exists(downloads)
