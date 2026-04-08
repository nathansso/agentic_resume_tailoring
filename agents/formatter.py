"""
Resume Formatter Agent — Converts tailored JSON output into a formatted markdown resume.

Produces markdown matching the user's resume style:
- Header with name, email, location, LinkedIn
- Education section (pulled from DB/config — not tailored)
- Projects section with bold lead-ins
- Experience section with bold lead-ins
- Skills section grouped by category
"""
import logging
import json
import re
from typing import Dict, Any, List, Optional
from uuid import UUID
from sqlmodel import Session, select

from database.db import engine
from database.models import User, Experience, Skill, UserSkill
from agents.skill_postprocessor import should_reject_skill, normalize_skill_name

logger = logging.getLogger(__name__)


def sanitize_text(text: str) -> str:
    """Replace problematic Unicode characters with ASCII-safe equivalents."""
    replacements = {
        "\u2013": "-",   # en dash
        "\u2014": "-",   # em dash
        "\u2018": "'",   # left single quote
        "\u2019": "'",   # right single quote (apostrophe)
        "\u201c": '"',   # left double quote
        "\u201d": '"',   # right double quote
        "\u2026": "...", # ellipsis
        "\u00a0": " ",   # non-breaking space
        "\u200b": "",    # zero-width space
    }
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
    return text


class ResumeFormatterAgent:
    """Formats tailored resume content into styled markdown."""

    def __init__(self, user_id: UUID):
        self.user_id = user_id

    def format_markdown(self, tailored_content: Dict, job_title: str = "") -> str:
        """
        Convert tailored_content JSON into a formatted markdown resume.
        
        Args:
            tailored_content: The JSON from the tailor agent (experiences, projects, skills_emphasized)
            job_title: Optional job title for the output filename context
            
        Returns:
            Formatted markdown string
        """
        sections = []

        # 1. Header
        header = self._build_header()
        if header:
            sections.append(header)

        # 2. Education (static — not tailored)
        education = self._build_education()
        if education:
            sections.append(education)

        # 3. Projects (tailored)
        projects = self._build_projects(tailored_content.get("projects", []))
        if projects:
            sections.append(projects)

        # 4. Experience (tailored)
        experiences = self._build_experiences(tailored_content.get("experiences", []))
        if experiences:
            sections.append(experiences)

        # 5. Skills (emphasized from tailored + full from DB)
        skills = self._build_skills(tailored_content.get("skills_emphasized", []))
        if skills:
            sections.append(skills)

        return sanitize_text("\n".join(sections))

    def _build_header(self) -> str:
        """Build the resume header from user profile."""
        with Session(engine) as session:
            user = session.exec(select(User).where(User.user_id == self.user_id)).first()
            if not user:
                return ""

            parts = [user.name, "  "]
            contact = []
            if user.email and user.email != "user@example.com":
                contact.append(user.email)
            if user.linkedin_url:
                contact.append(user.linkedin_url)
            parts.append(" | ".join(contact))
            return "\n".join(parts)

    def _build_education(self) -> str:
        """Build education section.
        
        Education is static and not tailored. We pull it from the original
        resume markdown if available, otherwise skip.
        """
        # For now, return a placeholder that the user can fill in.
        # In future, this could be stored in the DB or parsed from the original resume.
        return (
            "Education  \n"
            "**University of California\\- San Diego \\-** M.S. Data Science, Expected June 2027  \n"
            "GPA: 4.0/4.0  \n"
            "**University of California\\- San Diego \\-** B.S. Mathematics & Economics, Minor in Data Science, June 2025  \n"
            "**Notable Coursework:** OOP, DSA, Micro/Macroeconomics, Econometrics, ML & Probabilistic Modeling"
        )

    def _build_projects(self, projects: List[Dict]) -> str:
        """Build the projects section from tailored project data."""
        if not projects:
            return ""

        lines = ["Projects"]
        for proj in projects:
            name = proj.get("name", "Untitled Project")
            lines.append(f"**{name}**\n")

            bullets = proj.get("bullets", [])
            for bullet in bullets:
                # Add bold lead-in if not already present
                formatted = self._format_bullet(bullet)
                lines.append(f"* {formatted}")
            lines.append("")

        return "\n".join(lines)

    def _build_experiences(self, experiences: List[Dict]) -> str:
        """Build the experience section from tailored experience data."""
        if not experiences:
            return ""

        lines = ["Experience"]
        for exp in experiences:
            title = exp.get("title", "Unknown Role")
            company = exp.get("company", "Unknown Company")
            start = exp.get("start_date", "")
            end = exp.get("end_date", "")
            date_range = f"{start} - {end}" if start else ""

            lines.append(f"**{title},** {company}\t{date_range}")

            # Description/subtitle if present
            desc = exp.get("description", "")
            if desc:
                lines.append(f"*{desc}*\n")

            bullets = exp.get("bullets", [])
            for bullet in bullets:
                formatted = self._format_bullet(bullet)
                lines.append(f"* {formatted}")
            lines.append("")

        return "\n".join(lines)

    def _build_skills(self, emphasized_skills: List[str]) -> str:
        """Build the skills section, grouping by category."""
        with Session(engine) as session:
            user_skills = session.exec(
                select(UserSkill).where(UserSkill.user_id == self.user_id)
            ).all()

            # Group skills by category
            categories: Dict[str, List[str]] = {}
            for us in user_skills:
                skill = session.exec(
                    select(Skill).where(Skill.skill_id == us.skill_id)
                ).first()
                if not skill:
                    continue
                # Filter out noise skills
                if should_reject_skill(skill.name):
                    continue
                canonical_name = normalize_skill_name(skill.name)
                cat = skill.category or "Other"
                # Normalize category names
                cat = self._normalize_category(cat)
                if cat not in categories:
                    categories[cat] = []
                if canonical_name not in categories[cat]:
                    categories[cat].append(canonical_name)

        if not categories:
            return ""

        # Preferred category order
        order = [
            "Languages & Libraries",
            "AI & Machine Learning",
            "Data Engineering",
            "Tools",
            "Cloud",
            "Other",
        ]

        lines = ["Skills"]
        for cat in order:
            if cat in categories:
                skills_list = ", ".join(sorted(categories[cat]))
                lines.append(f"**{cat}:** {skills_list}")
                del categories[cat]

        # Any remaining categories not in our preferred order
        for cat, skills in categories.items():
            skills_list = ", ".join(sorted(skills))
            lines.append(f"**{cat}:** {skills_list}")

        return "\n".join(lines)

    @staticmethod
    def _format_bullet(bullet: str) -> str:
        """Ensure bullet has a bold lead-in phrase for consistent formatting."""
        bullet = bullet.strip()
        # Already has bold lead-in
        if bullet.startswith("**"):
            return bullet
        # Try to bold the first clause (up to first verb or comma)
        # Simple heuristic: bold up to the first comma or first 5-6 words
        words = bullet.split()
        if len(words) <= 3:
            return f"**{bullet}**"
        # Find a natural break point
        for i, word in enumerate(words):
            if i >= 4 and (word.endswith(",") or word.endswith(":")):
                lead = " ".join(words[: i + 1]).rstrip(",:")
                rest = " ".join(words[i + 1 :]).lstrip(",: ")
                return f"**{lead}** {rest}" if rest else f"**{lead}**"
        # Default: bold first 4 words
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
