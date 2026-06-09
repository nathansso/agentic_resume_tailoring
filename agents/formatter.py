"""
Resume Formatter Agent — Converts tailored JSON output into a formatted resume.

Primary output path: LaTeX → PDF via pdflatex (Jake's Resume layout).
Markdown output is retained for debugging but is not the intended pipeline.
"""
import logging
import re
import warnings
from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlmodel import Session, select

from database.db import engine
from database.models import User, Experience, Skill, UserSkill
from agents.skill_postprocessor import should_reject_skill, normalize_skill_name

logger = logging.getLogger(__name__)

_DEFAULT_SECTION_ORDER = ["education", "experience", "projects", "skills"]

# ── Jake's Resume LaTeX preamble ─────────────────────────────────────────────
# Adapted from https://github.com/jakegut/resume (MIT License)

_JAKE_PREAMBLE = r"""\documentclass[letterpaper,11pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\input{glyphtounicode}

\pagestyle{fancy}
\fancyhf{}
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}

\urlstyle{same}
\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

\pdfgentounicode=1

%-------------------------
% Custom commands (Jake's Resume)
\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}

\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeSubSubheading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \textit{\small#1} & \textit{\small #2} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeSubItem}[1]{\resumeItem{#1}\vspace{-4pt}}

\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}
"""


# ── TeX utilities ─────────────────────────────────────────────────────────────

_TEX_SPECIAL = {
    "\\": r"\textbackslash{}",
    "&":  r"\&",
    "%":  r"\%",
    "$":  r"\$",
    "#":  r"\#",
    "_":  r"\_",
    "{":  r"\{",
    "}":  r"\}",
    "~":  r"\textasciitilde{}",
    "^":  r"\textasciicircum{}",
}
_TEX_ESCAPE_RE = re.compile(r"[\\&%$#_{}~^]")


def _escape_tex(text: str) -> str:
    """Escape special LaTeX characters in user-supplied text (single-pass)."""
    return _TEX_ESCAPE_RE.sub(lambda m: _TEX_SPECIAL[m.group()], text)


def _convert_inline(text: str) -> str:
    """Convert **bold** and *italic* markdown markers to LaTeX equivalents,
    escaping all other text for TeX."""
    parts = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)
    result = []
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            result.append(r"\textbf{" + _escape_tex(part[2:-2]) + "}")
        elif part.startswith("*") and part.endswith("*"):
            result.append(r"\textit{" + _escape_tex(part[1:-1]) + "}")
        else:
            result.append(_escape_tex(part))
    return "".join(result)


def _compile_tex_to_pdf(tex_str: str) -> bytes:
    """Write tex_str to a temp file, compile with pdflatex, return PDF bytes."""
    import subprocess
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as tmpdir:
        tex_path = os.path.join(tmpdir, "resume.tex")
        pdf_path = os.path.join(tmpdir, "resume.pdf")
        with open(tex_path, "w", encoding="utf-8") as f:
            f.write(tex_str)

        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-output-directory", tmpdir, tex_path],
            capture_output=True,
            timeout=60,
        )

        if not os.path.exists(pdf_path):
            stdout = result.stdout.decode("utf-8", errors="replace")
            raise RuntimeError(
                f"pdflatex failed (exit {result.returncode}):\n{stdout[-3000:]}"
            )

        with open(pdf_path, "rb") as f:
            return f.read()


def sanitize_text(text: str) -> str:
    """Replace problematic Unicode characters with ASCII-safe equivalents."""
    replacements = {
        "–": "-",
        "—": "-",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
        " ": " ",
        "​": "",
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


# ── Formatter agent ───────────────────────────────────────────────────────────

class ResumeFormatterAgent:
    """Formats tailored resume content into a styled PDF using Jake's Resume layout."""

    def __init__(self, user_id: UUID):
        self.user_id = user_id
        self._style: Optional[dict] = self._load_style()

    def _load_style(self) -> Optional[dict]:
        try:
            from tui.services import get_resume_style
            return get_resume_style(self.user_id)
        except Exception:
            return None

    def _resolve_label(self, key: str) -> str:
        return (self._style or {}).get("section_labels", {}).get(key, key.capitalize())

    def _build_known_labels(self) -> frozenset:
        labels: set = set()
        for v in (self._style or {}).get("section_labels", {}).values():
            labels.add(v)
        return frozenset(labels)

    # ── Primary path: LaTeX → PDF ─────────────────────────────────────────────

    def format_pdf(
        self,
        tailored_content: Dict,
        job_title: str = "",
        section_order: Optional[List[str]] = None,
    ) -> bytes:
        """Convert tailored_content to a PDF using Jake's Resume LaTeX layout."""
        tex = self._build_tex(tailored_content, section_order=section_order)
        return _compile_tex_to_pdf(tex)

    def _build_tex(
        self,
        tailored_content: Dict,
        section_order: Optional[List[str]] = None,
    ) -> str:
        """Assemble the full LaTeX document string."""
        style = self._style or {}
        order = section_order or style.get("section_order") or _DEFAULT_SECTION_ORDER

        builders = {
            "education": self._build_tex_education,
            "experience": lambda: self._build_tex_experiences(
                tailored_content.get("experiences", [])
            ),
            "projects": lambda: self._build_tex_projects(
                tailored_content.get("projects", [])
            ),
            "skills": lambda: self._build_tex_skills(
                tailored_content.get("skills_emphasized", [])
            ),
        }

        body_parts = [self._build_tex_header()]
        seen: set = set()
        for key in order:
            if key in seen or key not in builders:
                continue
            seen.add(key)
            block = builders[key]()
            if block:
                body_parts.append(block)
        for key, fn in builders.items():
            if key not in seen:
                block = fn()
                if block:
                    body_parts.append(block)

        body = "\n\n".join(body_parts)
        return (
            _JAKE_PREAMBLE
            + "\n\\begin{document}\n\n"
            + body
            + "\n\n\\end{document}\n"
        )

    def _build_tex_header(self) -> str:
        """Jake's centered header block."""
        with Session(engine) as session:
            user = session.exec(select(User).where(User.user_id == self.user_id)).first()
            if not user:
                return ""

        name = _escape_tex(user.name)

        contact_parts = []
        if user.phone:
            contact_parts.append(_escape_tex(user.phone))
        if user.email and user.email != "user@example.com":
            email_esc = _escape_tex(user.email)
            contact_parts.append(
                rf"\href{{mailto:{email_esc}}}{{\underline{{{email_esc}}}}}"
            )
        if user.linkedin_url:
            url_esc = _escape_tex(user.linkedin_url)
            display = url_esc.replace("https://", "").replace("http://", "")
            contact_parts.append(rf"\href{{{url_esc}}}{{\underline{{{display}}}}}")
        if user.github_username:
            gh_url = _escape_tex(f"https://github.com/{user.github_username}")
            gh_display = f"github.com/{_escape_tex(user.github_username)}"
            contact_parts.append(rf"\href{{{gh_url}}}{{\underline{{{gh_display}}}}}")
        if user.location:
            contact_parts.append(_escape_tex(user.location))

        contact_line = " $|$ ".join(contact_parts)

        return (
            r"\begin{center}"
            + "\n"
            + rf"    \textbf{{\Huge \scshape {name}}} \\ \vspace{{1pt}}"
            + "\n"
            + rf"    \small {contact_line}"
            + "\n"
            + r"\end{center}"
        )

    def _build_tex_education(self) -> str:
        """Jake's \\resumeSubheading blocks for education."""
        label = _escape_tex(self._resolve_label("education"))
        return "\n".join([
            rf"\section{{{label}}}",
            r"  \resumeSubHeadingListStart",
            r"    \resumeSubheading",
            r"      {University of California, San Diego}{La Jolla, CA}",
            r"      {M.S. Data Science, GPA: 4.0/4.0}{Expected June 2027}",
            r"    \resumeSubheading",
            r"      {University of California, San Diego}{La Jolla, CA}",
            r"      {B.S. Mathematics \& Economics, Minor in Data Science}{June 2025}",
            r"    \resumeSubItem{Notable Coursework: OOP, DSA, Micro/Macroeconomics, Econometrics, ML \& Probabilistic Modeling}",
            r"  \resumeSubHeadingListEnd",
        ])

    def _build_tex_experiences(self, experiences: List[Dict]) -> str:
        """Jake's \\resumeSubheading + item lists for each role."""
        if not experiences:
            return ""

        label = _escape_tex(self._resolve_label("experience"))
        lines = [
            rf"\section{{{label}}}",
            r"  \resumeSubHeadingListStart",
        ]

        for exp in experiences:
            title      = _escape_tex(exp.get("title", ""))
            company    = _escape_tex(exp.get("company", ""))
            start      = _escape_tex(exp.get("start_date", ""))
            end        = _escape_tex(exp.get("end_date", ""))
            date_range = f"{start} -- {end}" if start else end
            location   = _escape_tex(exp.get("location", ""))

            lines += [
                r"    \resumeSubheading",
                f"      {{{title}}}{{{date_range}}}",
                f"      {{{company}}}{{{location}}}",
            ]

            bullets = exp.get("bullets", [])
            if bullets:
                lines.append(r"      \resumeItemListStart")
                for bullet in bullets:
                    lines.append(
                        r"        \resumeItem{" + _convert_inline(bullet.strip()) + "}"
                    )
                lines.append(r"      \resumeItemListEnd")

        lines.append(r"  \resumeSubHeadingListEnd")
        return "\n".join(lines)

    def _build_tex_projects(self, projects: List[Dict]) -> str:
        """Jake's \\resumeProjectHeading blocks."""
        if not projects:
            return ""

        label = _escape_tex(self._resolve_label("projects"))
        lines = [
            rf"\section{{{label}}}",
            r"    \resumeSubHeadingListStart",
        ]

        for proj in projects:
            name  = _escape_tex(proj.get("name", "Untitled"))
            techs = _escape_tex(proj.get("tech_stack", proj.get("technologies", "")))
            dates = _escape_tex(proj.get("date_range", proj.get("dates", "")))

            heading = (
                rf"\textbf{{{name}}} $|$ \emph{{{techs}}}" if techs
                else rf"\textbf{{{name}}}"
            )

            lines += [
                r"      \resumeProjectHeading",
                f"          {{{heading}}}{{{dates}}}",
            ]

            bullets = proj.get("bullets", [])
            if bullets:
                lines.append(r"          \resumeItemListStart")
                for bullet in bullets:
                    lines.append(
                        r"            \resumeItem{" + _convert_inline(bullet.strip()) + "}"
                    )
                lines.append(r"          \resumeItemListEnd")

        lines.append(r"    \resumeSubHeadingListEnd")
        return "\n".join(lines)

    def _build_tex_skills(self, emphasized_skills: List[str]) -> str:
        """Jake's skills itemize block grouped by category."""
        with Session(engine) as session:
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == self.user_id)
            ).all()

            categories: Dict[str, List[str]] = {}
            for us in user_skills:
                skill = session.exec(
                    select(Skill).where(Skill.skill_id == us.skill_id)
                ).first()
                if not skill:
                    continue
                if should_reject_skill(skill.name):
                    continue
                canonical = normalize_skill_name(skill.name)
                cat = self._normalize_category(skill.category or "Other")
                categories.setdefault(cat, [])
                if canonical not in categories[cat]:
                    categories[cat].append(canonical)

        if not categories:
            return ""

        label = _escape_tex(self._resolve_label("skills"))
        cat_order = [
            "Languages & Libraries", "AI & Machine Learning", "Data Engineering",
            "Tools", "Cloud", "Other",
        ]

        skill_lines = []
        for cat in cat_order:
            if cat in categories:
                skills_str = _escape_tex(", ".join(sorted(categories.pop(cat))))
                skill_lines.append(
                    rf"     \textbf{{{_escape_tex(cat)}}}{{: {skills_str}}} \\"
                )
        for cat, skills in categories.items():
            skills_str = _escape_tex(", ".join(sorted(skills)))
            skill_lines.append(
                rf"     \textbf{{{_escape_tex(cat)}}}{{: {skills_str}}} \\"
            )

        return "\n".join([
            rf"\section{{{label}}}",
            r" \begin{itemize}[leftmargin=0.15in, label={}]",
            r"    \small{\item{",
            *skill_lines,
            r"    }}",
            r" \end{itemize}",
        ])

    # ── Deprecated: Markdown path ─────────────────────────────────────────────

    def format_markdown(
        self,
        tailored_content: Dict,
        job_title: str = "",
        section_order: Optional[List[str]] = None,
    ) -> str:
        """[DEPRECATED] Produce a plain Markdown resume string.

        The primary export pipeline is format_pdf() → LaTeX → pdflatex.
        This method is retained for debugging and plain-text inspection only.
        """
        warnings.warn(
            "format_markdown() is deprecated — use format_pdf() for the primary export path.",
            DeprecationWarning,
            stacklevel=2,
        )
        style = self._style or {}
        order = section_order or style.get("section_order") or _DEFAULT_SECTION_ORDER
        bullet = style.get("bullet_prefix", "- ")

        builders = {
            "education": self._build_education,
            "experience": lambda: self._build_experiences(
                tailored_content.get("experiences", []),
                label=self._resolve_label("experience"),
                bullet_prefix=bullet,
            ),
            "projects": lambda: self._build_projects(
                tailored_content.get("projects", []),
                label=self._resolve_label("projects"),
                bullet_prefix=bullet,
            ),
            "skills": lambda: self._build_skills(
                tailored_content.get("skills_emphasized", []),
                label=self._resolve_label("skills"),
            ),
        }

        sections = [self._build_header()]
        seen: set = set()
        for key in order:
            if key in seen or key not in builders:
                continue
            seen.add(key)
            block = builders[key]()
            if block:
                sections.append(block)
        for key, fn in builders.items():
            if key not in seen:
                block = fn()
                if block:
                    sections.append(block)

        return sanitize_text("\n".join(sections))

    # ── Markdown section builders (used only by deprecated format_markdown()) ─

    def _build_header(self) -> str:
        with Session(engine) as session:
            user = session.exec(select(User).where(User.user_id == self.user_id)).first()
            if not user:
                return ""
            style_header = (self._style or {}).get("header", {})
            sep = style_header.get("contact_separator", " | ")
            field_order = style_header.get("contact_fields", ["email", "linkedin"])
            field_values = {
                "email": user.email if user.email and user.email != "user@example.com" else None,
                "linkedin": user.linkedin_url,
                "phone": user.phone,
                "location": user.location,
                "github": f"github.com/{user.github_username}" if user.github_username else None,
            }
            contact = [field_values[f] for f in field_order if field_values.get(f)]
            if not contact:
                contact = [v for v in field_values.values() if v]
            return "\n".join([user.name, "  ", sep.join(contact)])

    def _build_education(self) -> str:
        label = self._resolve_label("education")
        return (
            f"{label}  \n"
            "**University of California\\- San Diego \\-** M.S. Data Science, Expected June 2027  \n"
            "GPA: 4.0/4.0  \n"
            "**University of California\\- San Diego \\-** B.S. Mathematics & Economics, Minor in Data Science, June 2025  \n"
            "**Notable Coursework:** OOP, DSA, Micro/Macroeconomics, Econometrics, ML & Probabilistic Modeling"
        )

    def _build_projects(
        self,
        projects: List[Dict],
        label: str = "Projects",
        bullet_prefix: str = "- ",
    ) -> str:
        if not projects:
            return ""
        lines = [label]
        for proj in projects:
            lines.append(f"**{proj.get('name', 'Untitled Project')}**\n")
            for bullet in proj.get("bullets", []):
                lines.append(f"{bullet_prefix}{self._format_bullet(bullet)}")
            lines.append("")
        return "\n".join(lines)

    def _build_experiences(
        self,
        experiences: List[Dict],
        label: str = "Experience",
        bullet_prefix: str = "- ",
    ) -> str:
        if not experiences:
            return ""
        lines = [label]
        for exp in experiences:
            title   = exp.get("title", "Unknown Role")
            company = exp.get("company", "Unknown Company")
            start   = exp.get("start_date", "")
            end     = exp.get("end_date", "")
            date_range = f"{start} - {end}" if start else ""
            lines.append(f"**{title},** {company}\t{date_range}")
            desc = exp.get("description", "")
            if desc:
                lines.append(f"*{desc}*\n")
            for bullet in exp.get("bullets", []):
                lines.append(f"{bullet_prefix}{self._format_bullet(bullet)}")
            lines.append("")
        return "\n".join(lines)

    def _build_skills(
        self,
        emphasized_skills: List[str],
        label: str = "Skills",
    ) -> str:
        with Session(engine) as session:
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == self.user_id)
            ).all()
            categories: Dict[str, List[str]] = {}
            for us in user_skills:
                skill = session.exec(
                    select(Skill).where(Skill.skill_id == us.skill_id)
                ).first()
                if not skill:
                    continue
                if should_reject_skill(skill.name):
                    continue
                canonical = normalize_skill_name(skill.name)
                cat = self._normalize_category(skill.category or "Other")
                categories.setdefault(cat, [])
                if canonical not in categories[cat]:
                    categories[cat].append(canonical)

        if not categories:
            return ""

        order = [
            "Languages & Libraries", "AI & Machine Learning", "Data Engineering",
            "Tools", "Cloud", "Other",
        ]
        lines = [label]
        for cat in order:
            if cat in categories:
                lines.append(f"**{cat}:** {', '.join(sorted(categories[cat]))}")
                del categories[cat]
        for cat, skills in categories.items():
            lines.append(f"**{cat}:** {', '.join(sorted(skills))}")
        return "\n".join(lines)

    @staticmethod
    def _format_bullet(bullet: str) -> str:
        bullet = bullet.strip()
        if bullet.startswith("**"):
            return bullet
        words = bullet.split()
        if len(words) <= 3:
            return f"**{bullet}**"
        for i, word in enumerate(words):
            if i >= 4 and (word.endswith(",") or word.endswith(":")):
                lead = " ".join(words[: i + 1]).rstrip(",:")
                rest = " ".join(words[i + 1:]).lstrip(",: ")
                return f"**{lead}** {rest}" if rest else f"**{lead}**"
        lead = " ".join(words[:4])
        rest = " ".join(words[4:])
        return f"**{lead}** {rest}" if rest else f"**{lead}**"

    @staticmethod
    def _normalize_category(cat: str) -> str:
        cat_lower = cat.lower().strip()
        if any(kw in cat_lower for kw in ["language", "library", "libraries", "framework"]):
            return "Languages & Libraries"
        if any(kw in cat_lower for kw in ["ml", "machine learning", "ai", "deep learning", "technique"]):
            return "AI & Machine Learning"
        if any(kw in cat_lower for kw in ["data engineer", "etl", "pipeline", "database"]):
            return "Data Engineering"
        if any(kw in cat_lower for kw in ["tool", "devops", "infrastructure"]):
            return "Tools"
        if any(kw in cat_lower for kw in ["cloud", "aws", "gcp", "azure"]):
            return "Cloud"
        return cat
