"""
Resume Formatter Agent — Converts tailored JSON output into a formatted markdown resume.

Produces markdown matching the user's resume style (captured during ingestion):
- Header with name, contact fields in the order they appeared on the original resume
- Sections rendered in the order captured from the original resume
- Section labels preserved as-is (e.g. "Work Experience" not just "Experience")
- Bullet prefix style matched to the original
- Falls back to sensible defaults when no style has been captured
"""
import logging
import re
from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlmodel import Session, select

from database.db import engine
from database.models import User, Experience, Skill, UserSkill
from agents.skill_postprocessor import should_reject_skill, normalize_skill_name

logger = logging.getLogger(__name__)

_DEFAULT_SECTION_ORDER = ["education", "experience", "projects", "skills"]

_RESUME_CSS = """\
@page {
    size: letter;
    margin: 1in;
}
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 11pt;
    color: #111111;
    line-height: 1.4;
}
h1 {
    font-size: 18pt;
    font-weight: bold;
    border-bottom: 2pt solid #111111;
    padding-bottom: 4pt;
    margin-bottom: 6pt;
}
h2 {
    font-size: 12pt;
    font-weight: bold;
    border-bottom: 0.5pt solid #666666;
    padding-bottom: 2pt;
    margin-top: 12pt;
    margin-bottom: 4pt;
}
p {
    margin-top: 2pt;
    margin-bottom: 5pt;
}
ul {
    margin-top: 2pt;
    margin-bottom: 8pt;
    padding-left: 16pt;
}
li {
    margin-bottom: 3pt;
}
strong {
    font-weight: bold;
}
em {
    font-style: italic;
}
"""

_SECTION_NAMES = frozenset({"Education", "Projects", "Experience", "Skills"})


def _promote_section_headings(md_text: str, known_labels: Optional[frozenset] = None) -> str:
    """Promote the name line and section-header lines to Markdown heading levels.

    format_markdown() outputs flat text (no # markers). This gives the PDF
    renderer proper <h1>/<h2> anchors without changing the .md output contract.
    """
    effective_labels = known_labels if known_labels is not None else _SECTION_NAMES
    lines = []
    first_content_seen = False
    for line in md_text.split("\n"):
        stripped = line.strip()
        if stripped in effective_labels:
            lines.append(f"## {stripped}")
        elif not first_content_seen and stripped:
            lines.append(f"# {stripped}")
            first_content_seen = True
        else:
            lines.append(line)
    return "\n".join(lines)


def _md_to_pdf_bytes(md_text: str, known_labels: Optional[frozenset] = None) -> bytes:
    """Convert a Markdown string to PDF bytes via HTML + xhtml2pdf."""
    import io
    import logging as _logging
    import markdown as _markdown
    from xhtml2pdf import pisa

    _logging.getLogger("xhtml2pdf").setLevel(_logging.ERROR)

    enhanced_md = _promote_section_headings(md_text, known_labels)
    html_body = _markdown.markdown(enhanced_md, extensions=["tables"])
    html = (
        "<!DOCTYPE html><html><head>"
        "<meta charset='utf-8'/>"
        f"<style>{_RESUME_CSS}</style>"
        f"</head><body>{html_body}</body></html>"
    )
    pdf_buf = io.BytesIO()
    status = pisa.CreatePDF(html, dest=pdf_buf)
    if status.err:
        raise RuntimeError(f"PDF generation failed (err={status.err})")
    return pdf_buf.getvalue()


def sanitize_text(text: str) -> str:
    """Replace problematic Unicode characters with ASCII-safe equivalents."""
    replacements = {
        "–": "-",   # en dash
        "—": "-",   # em dash
        "‘": "'",   # left single quote
        "’": "'",   # right single quote (apostrophe)
        "“": '"',   # left double quote
        "”": '"',   # right double quote
        "…": "...", # ellipsis
        " ": " ",   # non-breaking space
        "​": "",    # zero-width space
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


class ResumeFormatterAgent:
    """Formats tailored resume content into styled markdown, driven by the user's captured style."""

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
        """Return the display label for a section from captured style, or a sensible default."""
        return (self._style or {}).get("section_labels", {}).get(key, key.capitalize())

    def _build_known_labels(self) -> frozenset:
        """Build the full set of section label strings for _promote_section_headings."""
        labels = set(_SECTION_NAMES)
        for v in (self._style or {}).get("section_labels", {}).values():
            labels.add(v)
        return frozenset(labels)

    def format_markdown(
        self,
        tailored_content: Dict,
        job_title: str = "",
        section_order: Optional[List[str]] = None,
    ) -> str:
        """Convert tailored_content JSON into a formatted markdown resume.

        Args:
            tailored_content: JSON from the tailor agent (experiences, projects, skills_emphasized)
            job_title: Optional job title for context
            section_order: Optional override for section ordering (canonical keys like
                "experience", "projects", "skills", "education"). When provided, used
                instead of the captured style order. Intended for the tailoring pipeline
                to supply a job-relevance-ranked order.
        """
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
        # Append any builder not covered by the stored order
        for key, fn in builders.items():
            if key not in seen:
                block = fn()
                if block:
                    sections.append(block)

        return sanitize_text("\n".join(sections))

    def format_pdf(
        self,
        tailored_content: Dict,
        job_title: str = "",
        section_order: Optional[List[str]] = None,
    ) -> bytes:
        """Convert tailored_content to a styled PDF and return raw bytes."""
        md_text = self.format_markdown(tailored_content, job_title, section_order)
        return _md_to_pdf_bytes(md_text, self._build_known_labels())

    def _build_header(self) -> str:
        """Build the resume header, using contact field order from captured style."""
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

            parts = [user.name, "  ", sep.join(contact)]
            return "\n".join(parts)

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
        """Build the projects section from tailored project data."""
        if not projects:
            return ""

        lines = [label]
        for proj in projects:
            name = proj.get("name", "Untitled Project")
            lines.append(f"**{name}**\n")

            for bullet in proj.get("bullets", []):
                formatted = self._format_bullet(bullet)
                lines.append(f"{bullet_prefix}{formatted}")
            lines.append("")

        return "\n".join(lines)

    def _build_experiences(
        self,
        experiences: List[Dict],
        label: str = "Experience",
        bullet_prefix: str = "- ",
    ) -> str:
        """Build the experience section from tailored experience data."""
        if not experiences:
            return ""

        lines = [label]
        for exp in experiences:
            title = exp.get("title", "Unknown Role")
            company = exp.get("company", "Unknown Company")
            start = exp.get("start_date", "")
            end = exp.get("end_date", "")
            date_range = f"{start} - {end}" if start else ""

            lines.append(f"**{title},** {company}\t{date_range}")

            desc = exp.get("description", "")
            if desc:
                lines.append(f"*{desc}*\n")

            for bullet in exp.get("bullets", []):
                formatted = self._format_bullet(bullet)
                lines.append(f"{bullet_prefix}{formatted}")
            lines.append("")

        return "\n".join(lines)

    def _build_skills(
        self,
        emphasized_skills: List[str],
        label: str = "Skills",
    ) -> str:
        """Build the skills section, grouping by category."""
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
                canonical_name = normalize_skill_name(skill.name)
                cat = self._normalize_category(skill.category or "Other")
                if cat not in categories:
                    categories[cat] = []
                if canonical_name not in categories[cat]:
                    categories[cat].append(canonical_name)

        if not categories:
            return ""

        order = [
            "Languages & Libraries",
            "AI & Machine Learning",
            "Data Engineering",
            "Tools",
            "Cloud",
            "Other",
        ]

        lines = [label]
        for cat in order:
            if cat in categories:
                skills_list = ", ".join(sorted(categories[cat]))
                lines.append(f"**{cat}:** {skills_list}")
                del categories[cat]

        for cat, skills in categories.items():
            skills_list = ", ".join(sorted(skills))
            lines.append(f"**{cat}:** {skills_list}")

        return "\n".join(lines)

    @staticmethod
    def _format_bullet(bullet: str) -> str:
        """Ensure bullet has a bold lead-in phrase for consistent formatting."""
        bullet = bullet.strip()
        if bullet.startswith("**"):
            return bullet
        words = bullet.split()
        if len(words) <= 3:
            return f"**{bullet}**"
        for i, word in enumerate(words):
            if i >= 4 and (word.endswith(",") or word.endswith(":")):
                lead = " ".join(words[: i + 1]).rstrip(",:")
                rest = " ".join(words[i + 1 :]).lstrip(",: ")
                return f"**{lead}** {rest}" if rest else f"**{lead}**"
        lead = " ".join(words[:4])
        rest = " ".join(words[4:])
        return f"**{lead}** {rest}" if rest else f"**{lead}**"

    @staticmethod
    def _normalize_category(cat: str) -> str:
        """Normalize varied category names into consistent groups."""
        cat_lower = cat.lower().strip()
        if any(kw in cat_lower for kw in ["language", "library", "libraries"]):
            return "Languages & Libraries"
        if any(kw in cat_lower for kw in ["ml", "machine learning", "ai", "deep learning"]):
            return "AI & Machine Learning"
        if any(kw in cat_lower for kw in ["data engineer", "etl", "pipeline", "database"]):
            return "Data Engineering"
        if any(kw in cat_lower for kw in ["framework"]):
            return "Languages & Libraries"
        if any(kw in cat_lower for kw in ["tool", "devops", "infrastructure"]):
            return "Tools"
        if any(kw in cat_lower for kw in ["cloud", "aws", "gcp", "azure"]):
            return "Cloud"
        if any(kw in cat_lower for kw in ["technique"]):
            return "AI & Machine Learning"
        return cat
