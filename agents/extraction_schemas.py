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


# ── JD profile extraction (agents/jd_profile.py, issue #121) ─────────────────
# The JD decomposed into atomic requirement statements, extracted **once** per
# job description and persisted. A closed enum for `type` for the same reason
# `RoleFamily` is closed: `type` is a downstream selection key (#125 weights by
# it, #151 splits skill coverage on it), and two spellings of "required" would
# silently never match.
#
# `criticality` is deliberately a plain int rather than a `conint(ge=1, le=5)`.
# A range-constrained field turns an out-of-range model answer into a validation
# error, which the extraction seam retries once and then re-raises — losing the
# whole profile over one bad integer. `jd_profile.compile_profile_payload`
# clamps instead, matching this module's existing tolerance for sloppy output.

class RequirementType(str, Enum):
    """How binding a requirement is, per the JD's own framing."""
    REQUIRED = "required"
    PREFERRED = "preferred"
    INCIDENTAL = "incidental"


class JDRequirementItem(BaseModel):
    text: Optional[str] = Field(
        None,
        description=(
            "The requirement rewritten as a standalone sentence, understandable "
            "without the surrounding JD. Downstream this is used verbatim as an "
            "NLI hypothesis, so it must not contain pronouns referring out of "
            "itself (write 'The candidate has 5 years of Python experience', "
            "not 'You have 5 years of it')"),
    )
    type: RequirementType = Field(
        RequirementType.REQUIRED,
        description=(
            "'required' if the JD frames it as a must-have, 'preferred' if it is "
            "a nice-to-have or bonus, 'incidental' if it is mentioned in passing "
            "and is not a qualification at all"),
    )
    criticality: Optional[int] = Field(
        3,
        description=(
            "How central this requirement is to the role, 1 (barely matters) to "
            "5 (the job is about this). Judge from section placement, repetition "
            "in the posting, and whether the job title names it"),
    )
    terms: List[str] = Field(
        default_factory=list,
        description=(
            "Concrete keywords from this requirement — technologies, tools, "
            "languages, methods. Not the full sentence, and not generic filler "
            "like 'experience' or 'team'"),
    )
    source_section: Optional[str] = Field(
        None,
        description=(
            "The posting's own heading this requirement came from, verbatim "
            "(e.g. 'Required Qualifications', 'Nice to Have'). Null when the "
            "requirement came from unheaded body prose"),
    )
    confidence: Optional[float] = Field(
        None,
        description=(
            "0.0-1.0 confidence in this requirement's type and criticality. Low "
            "confidence flags it for human review rather than discarding it"),
    )


class JDProfileExtraction(BaseModel):
    requirements: List[JDRequirementItem] = Field(
        default_factory=list,
        description=(
            "Every requirement in the posting, in the order it appears. Source "
            "order is load-bearing downstream — do not group or sort them"),
    )
    title_terms: List[str] = Field(
        default_factory=list,
        description="Meaningful terms from the job title itself, minus filler",
    )


# ── Preference extraction (agents/preferences.py, issue #129) ────────────────
#
# The candidate-side mirror of JDProfileExtraction: what the *user* wants,
# inferred from tailoring chat, against what the *job* wants.
#
# `polarity` is a closed enum for the reason #129 finding 1 makes central —
# suppression is the dominant case and the one every retrieval method fails at
# (ImplexConv opposed-case F1 14.8%), so negation has to be a typed field the
# arbitration can branch on, never free text a downstream prompt has to infer.
#
# `strength` is a plain int, clamped in the compile rather than constrained
# here, for the same reason `criticality` is: a range-constrained field turns
# one sloppy integer into a validation error that the seam retries once and then
# re-raises, losing every preference in the turn over a single bad number. It is
# deliberately on the same 1-5 scale as `JDRequirementItem.criticality`, because
# arbitration compares the two directly.

class PreferencePolarity(str, Enum):
    """What the user wants done with the target."""
    SUPPRESS = "suppress"
    EMPHASIZE = "emphasize"
    REFRAME = "reframe"


class PreferenceTargetType(str, Enum):
    """What kind of thing the preference is about."""
    EXPERIENCE = "experience"
    PROJECT = "project"
    SKILL = "skill"
    SECTION = "section"
    TOPIC = "topic"


class PreferenceScopeType(str, Enum):
    """How far the preference reaches.

    This is what keeps a cross-job profile from becoming global by accident:
    'I'm targeting infra roles' is a role_family, 'drop this bullet for this
    job' is a job, and only a genuinely standing constraint is global.
    """
    GLOBAL = "global"
    ROLE_FAMILY = "role_family"
    JOB = "job"


class PreferenceNote(BaseModel):
    """One standing preference observed in the conversation."""
    text: Optional[str] = Field(
        None,
        description=(
            "The preference rewritten as a standalone statement about how to "
            "build this candidate's resume, understandable with no access to "
            "the conversation (write 'Do not lead with the recipe project, it "
            "was coursework', not 'that one was just for class')"),
    )
    polarity: PreferencePolarity = Field(
        PreferencePolarity.SUPPRESS,
        description=(
            "'suppress' to leave something out or play it down, 'emphasize' to "
            "lead with it or give it more room, 'reframe' to keep it but "
            "present it differently"),
    )
    target_type: PreferenceTargetType = Field(
        PreferenceTargetType.TOPIC,
        description=(
            "What the preference is about. Use 'topic' only when it refers to "
            "no specific item on the resume"),
    )
    target_label: Optional[str] = Field(
        None,
        description=(
            "The item this is about, quoted verbatim from the supplied list of "
            "the candidate's known items when it names one of them. Otherwise "
            "the bare subject as the user described it"),
    )
    scope_type: PreferenceScopeType = Field(
        PreferenceScopeType.JOB,
        description=(
            "'job' when the user is talking about this application only, "
            "'role_family' when they describe the kind of role they are "
            "targeting, 'global' only for a standing rule about every resume "
            "they will ever send. Prefer the narrowest scope that fits"),
    )
    scope_value: Optional[str] = Field(
        None,
        description=(
            "For scope_type='role_family', the family named — one of: "
            "software_engineering, machine_learning, data_science, "
            "data_engineering, research, product_management, design, "
            "devops_infrastructure, security, hardware. Null otherwise"),
    )
    strength: Optional[int] = Field(
        3,
        description=(
            "How firmly the user holds this, 1 (a passing remark) to 5 (an "
            "absolute rule they stated as non-negotiable). Reserve 5 for "
            "language like 'never' or 'under no circumstances' — a 5 is "
            "honored even when the job explicitly asks for the opposite"),
    )
    evidence: Optional[str] = Field(
        None,
        description=(
            "Verbatim quote from the conversation stating this preference. "
            "Required — omit the note entirely rather than inventing a quote"),
    )
    confidence: Optional[float] = Field(
        None,
        description=(
            "0.0-1.0 confidence that this is a real standing preference rather "
            "than a passing comment. Low confidence flags it for review"),
    )


class PreferenceNoteList(BaseModel):
    notes: List[PreferenceNote] = Field(default_factory=list)
