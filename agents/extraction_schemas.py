"""Typed extraction schemas for the structured-extraction seam (issue #142).

These Pydantic models define the shape every LLM extractor in `agents/parser.py`
and `agents/job_analyzer.py` returns. They are handed to
`llm.get_extractor(schema=...)`, which drives
`ChatModel.with_structured_output(schema)` — so the model's tool call is
validated against these models instead of being coerced from free-form JSON by
`JsonOutputParser` + `_coerce_records`.

Design notes:
- Anthropic tool-calling needs a single root object, so each list of records is
  wrapped in a `*List` model with one list field.
- Every field is optional with a default, mirroring the pre-#142 tolerance for
  missing keys (the old code read everything through `dict.get(...)`). A model
  that omits a field validates fine; the persistence layer fills the blank.
- The extractors `.model_dump()` these back to `List[Dict]`, so the heavily
  tested `_save_*` / dedup / heal persistence layer keeps its existing
  `List[Dict]` contract untouched.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ── Resume parser extractors (agents/parser.py) ──────────────────────────────

class ExperienceItem(BaseModel):
    title: Optional[str] = Field(None, description="Job title / role")
    company: Optional[str] = Field(None, description="Employer or organization")
    start_date: Optional[str] = Field(None, description="Start date, e.g. 'YYYY-MM'")
    end_date: Optional[str] = Field(
        None, description="End date 'YYYY-MM' or 'Present'")
    description: Optional[str] = Field(None, description="Short role summary")
    bullets: List[str] = Field(
        default_factory=list,
        description=(
            "Accomplishment bullets. Preserve any URL verbatim as markdown "
            "`[text](url)` inside the bullet string — never drop it."),
    )


class ExperienceList(BaseModel):
    experiences: List[ExperienceItem] = Field(default_factory=list)


class EducationItem(BaseModel):
    institution: Optional[str] = None
    degree: Optional[str] = Field(
        None, description="Full degree name including major/minor")
    location: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = Field(
        None, description="Graduation date, e.g. 'June 2025' or 'Expected June 2027'")
    gpa: Optional[str] = Field(None, description="GPA as a string, or null if not stated")


class EducationList(BaseModel):
    education: List[EducationItem] = Field(default_factory=list)


class AchievementItem(BaseModel):
    title: Optional[str] = Field(None, description="The award / honor name")
    description: Optional[str] = Field(None, description="Supporting detail, or null")
    issuer: Optional[str] = Field(
        None, description="Awarding organization or publication, or null")
    date: Optional[str] = Field(None, description="Year or date awarded, or null")


class AchievementList(BaseModel):
    achievements: List[AchievementItem] = Field(default_factory=list)


class ProjectItem(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    repo_url: Optional[str] = Field(None, description="Source-code/repository URL, if any")
    demo_url: Optional[str] = Field(
        None, description="Live/demo URL distinct from the repo link, if any")


class ProjectList(BaseModel):
    projects: List[ProjectItem] = Field(default_factory=list)


class SkillItem(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = Field(
        None,
        description=(
            "One of 'Language', 'Library', 'Framework', 'Tool', 'Technique', "
            "'Database', 'Cloud', 'Soft Skill'"),
    )
    proficiency: Optional[int] = Field(None, description="1-5 estimate based on context")


class SkillList(BaseModel):
    skills: List[SkillItem] = Field(default_factory=list)


# ── Job description analyzer extractors (agents/job_analyzer.py) ──────────────

class JobMetadata(BaseModel):
    title: Optional[str] = Field(None, description="The job title")
    company: Optional[str] = Field(None, description="The hiring company name")


class JobSkillItem(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = Field(None, description="e.g. Language, Framework, Tool")
    required: Optional[bool] = Field(
        True, description="True if required, False if merely preferred")
    weight: Optional[float] = Field(
        1.0, description="0.1-1.0 by how prominently the skill appears")


class JobSkillList(BaseModel):
    skills: List[JobSkillItem] = Field(default_factory=list)


# ── Chain-of-Note chat extraction (agents/knowledge_extractor.py, issue #21) ──
#
# Two passes over the same seam: the model first writes grounded *notes* about
# what the conversation claims, then reasons over those notes plus the facts
# already in the knowledge graph to a per-note decision. Splitting extraction
# from the add/supersede/no-op judgement is the Chain-of-Note shape the P0 arc
# (LongMemEval, #108) landed on — a single pass conflates "what was said" with
# "what should change", and reliably misses the contradiction case.

class ChatArtifactNote(BaseModel):
    """One candidate knowledge artifact observed in the conversation."""
    type: Optional[str] = Field(
        None, description="One of 'skill', 'project', 'experience'")
    name: Optional[str] = Field(
        None,
        description=(
            "Skill name, project name, or — for an experience — the job title"),
    )
    category: Optional[str] = Field(None, description="Skills only, e.g. 'Database'")
    company: Optional[str] = Field(None, description="Experiences only: the employer")
    description: Optional[str] = Field(None, description="Short supporting detail")
    proficiency: Optional[int] = Field(
        None, description="Skills only: 1-5 estimate, when the user states a level")
    evidence: Optional[str] = Field(
        None,
        description=(
            "Verbatim quote from the conversation supporting this note. Required "
            "— omit the note entirely rather than inventing a quote."),
    )
    note: Optional[str] = Field(
        None,
        description=(
            "One-line note on what the conversation claims about this artifact, "
            "including whether it revises something stated earlier"),
    )


class ChatArtifactNoteList(BaseModel):
    notes: List[ChatArtifactNote] = Field(default_factory=list)


class ArtifactDecision(BaseModel):
    """What to do with one note, judged against the existing knowledge graph."""
    note_index: Optional[int] = Field(
        None, description="0-based index of the note this decision is about")
    decision: Optional[str] = Field(
        None,
        description=(
            "'add' for a genuinely new fact, 'supersede' when it contradicts or "
            "updates a fact already in the profile, 'no_op' when already known"),
    )
    target: Optional[str] = Field(
        None,
        description=(
            "For 'supersede'/'no_op': the existing profile fact this refers to, "
            "quoted from the supplied list"),
    )
    rationale: Optional[str] = Field(
        None, description="One short sentence justifying the decision")


class ArtifactDecisionList(BaseModel):
    decisions: List[ArtifactDecision] = Field(default_factory=list)


# ── JobCard role classification (agents/job_card.py, issue #137) ──────────────
#
# The single LLM call the JobCard compile is allowed. Everything else in the
# compile is a deterministic projection of `UserJobResult`, which is what makes
# "two compiles of the same result produce identical cards" hold. A closed enum
# (rather than a free-form string) is what makes the classification usable as a
# selection key: `role_family` match is one of the ranking signals, and two
# spellings of the same family would silently never match.

class RoleFamily(str, Enum):
    """Closed label set for the role a job description is hiring for."""
    SOFTWARE_ENGINEERING = "software_engineering"
    MACHINE_LEARNING = "machine_learning"
    DATA_SCIENCE = "data_science"
    DATA_ENGINEERING = "data_engineering"
    RESEARCH = "research"
    PRODUCT_MANAGEMENT = "product_management"
    DESIGN = "design"
    DEVOPS_INFRASTRUCTURE = "devops_infrastructure"
    SECURITY = "security"
    HARDWARE = "hardware"
    OTHER = "other"


class RoleFamilyClassification(BaseModel):
    role_family: RoleFamily = Field(
        RoleFamily.OTHER,
        description=(
            "The single family this role belongs to. Use 'other' only when the "
            "role genuinely fits none of the listed families"),
    )
    rationale: Optional[str] = Field(
        None, description="One short sentence justifying the classification")
