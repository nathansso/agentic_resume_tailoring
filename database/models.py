from typing import Optional, List, Dict
from datetime import datetime
from uuid import UUID, uuid4
from sqlmodel import Field, SQLModel, Relationship, JSON
from sqlalchemy import Column
import json

# JSON Type helper
def json_column():
    return Field(default={}, sa_column=Column(JSON))

class User(SQLModel, table=True):
    user_id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str
    email: str = Field(unique=True)
    username: Optional[str] = Field(default=None, unique=True, index=True)
    password_hash: Optional[str] = Field(default=None)
    supabase_uid: Optional[str] = Field(default=None, unique=True)
    linkedin_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    # LinkedIn ingestion lifecycle (issue 13: Bright Data)
    linkedin_ingested_url: Optional[str] = None       # last URL successfully scraped
    linkedin_ingest_status: Optional[str] = None       # None | "importing" | "done" | "failed"
    linkedin_ingest_error: Optional[str] = None        # last failure message, if any
    linkedin_ingested_at: Optional[datetime] = None
    # Raw Bright Data scrape record (JSON-encoded) from the last successful
    # LinkedIn import (issue #69). Persisted so mapping improvements can be
    # replayed against the stored scrape instead of paying for a new one.
    linkedin_raw_record: Optional[str] = None
    github_username: Optional[str] = None
    github_access_token: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    onboarding_complete: bool = Field(default=False)
    onboarding_steps: Dict = Field(default={}, sa_column=Column(JSON))
    resume_path: Optional[str] = None
    resume_markdown: Optional[str] = None
    resume_style: Optional[Dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    skills: List["UserSkill"] = Relationship(back_populates="user")
    experiences: List["Experience"] = Relationship(back_populates="user")
    projects: List["Project"] = Relationship(back_populates="user")
    job_results: List["UserJobResult"] = Relationship(back_populates="user")
    education_entries: List["Education"] = Relationship(back_populates="user")
    achievement_entries: List["Achievement"] = Relationship(back_populates="user")

class Skill(SQLModel, table=True):
    skill_id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(index=True) # Normalized name
    category: Optional[str] = None
    description: Optional[str] = None
    # Cached embedding of the canonical name (issue #54): JSON-encoded float list,
    # shared by the matcher and the skill scorer. embedding_model records which
    # model produced it so a model change invalidates the cache cleanly.
    embedding: Optional[str] = None
    embedding_model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user_links: List["UserSkill"] = Relationship(back_populates="skill")
    job_links: List["JobSkill"] = Relationship(back_populates="skill")

class UserSkill(SQLModel, table=True):
    user_skill_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id")
    skill_id: UUID = Field(foreign_key="skill.skill_id")
    proficiency: Optional[int] = None # 1-5
    evidence_source: Optional[str] = None # Resume, GitHub, etc.
    evidence_detail: Optional[str] = None # Specific bullet or repo
    confidence_score: float = 0.0
    # Pinned "core" skill (issue #54): always rendered in the tailored skills
    # section, bypassing the JD-relevance cap and ordering floor.
    is_core: bool = Field(default=False)
    # Where a chat-captured artifact came from, e.g. "chat:<job_id>" (issue #21).
    # Deliberately a free-form string and NOT a foreign key: deleting the job or
    # its chat history must never cascade into knowledge-graph rows, which belong
    # to the user profile.
    source_context: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="skills")
    skill: Skill = Relationship(back_populates="user_links")

class Experience(SQLModel, table=True):
    experience_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id")
    title: str
    company: str
    start_date: Optional[str] = None # Keeping as string for flexibility (YYYY-MM)
    end_date: Optional[str] = None
    description: Optional[str] = None # Raw description
    bullets: List[str] = Field(default=[], sa_column=Column(JSON)) # Parsed bullets
    # User manually edited this row via the Data Explorer (issue #92). Protects
    # the row's fields from being reverted/enriched by a later re-ingest.
    manually_edited: bool = Field(default=False)
    # Soft origin-chat back-reference (issue #21). See UserSkill.source_context.
    source_context: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="experiences")

class Education(SQLModel, table=True):
    """Per-user education entry (issue #73).

    Education was previously hardcoded in the resume formatter, leaking one
    user's schooling into every export. Rows are populated from resume/LinkedIn
    ingestion and rendered per-user; a user with no rows gets no education
    section rather than fabricated data.
    """
    education_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    institution: str
    degree: str  # e.g. "B.S. Mathematics & Economics, Minor in Data Science"
    location: Optional[str] = None
    start_date: Optional[str] = None  # Free-form, matching Experience (e.g. "Sep 2021")
    end_date: Optional[str] = None    # e.g. "June 2025" or "Expected June 2027"
    gpa: Optional[str] = None
    # User manually edited this row via the Data Explorer (issue #92).
    manually_edited: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="education_entries")

class Achievement(SQLModel, table=True):
    """Per-user achievement / honor / award entry.

    Populated from resume and LinkedIn (Bright Data `honors_and_awards`)
    ingestion with the same cross-source fuzzy dedup as experiences, and
    rendered per-user. Content is copied verbatim into tailored output (never
    LLM-rewritten or fabricated); the tailoring pipeline only decides where the
    section is placed. A user with no rows gets no achievements section.
    """
    achievement_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    title: str                          # e.g. "Dean's List", "1st Place, HackMIT"
    description: Optional[str] = None    # optional supporting line
    issuer: Optional[str] = None         # awarding org / publication
    date: Optional[str] = None           # free-form, matching Experience (e.g. "2023")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="achievement_entries")

class Project(SQLModel, table=True):
    project_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id")
    name: str
    description: Optional[str] = None
    repo_url: Optional[str] = None
    demo_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    metrics: Dict = Field(default={}, sa_column=Column(JSON)) # GitHub signals: stars, languages, readme_length (issue #46)
    # User manually edited this row via the Data Explorer (issue #92).
    manually_edited: bool = Field(default=False)
    # Soft origin-chat back-reference (issue #21). See UserSkill.source_context.
    source_context: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="projects")
    blurbs: List["ProjectBlurb"] = Relationship(back_populates="project")

class ProjectBlurb(SQLModel, table=True):
    """
    Stores pre-generated ATS variations for a project.
    e.g. style="metrics_heavy" -> "Increased efficiency by 50% using Python..."
    """
    blurb_id: UUID = Field(default_factory=uuid4, primary_key=True)
    project_id: UUID = Field(foreign_key="project.project_id")
    style: str # 'concise', 'detailed', 'metrics', 'technical'
    content: str # The actual generated text
    created_at: datetime = Field(default_factory=datetime.utcnow)

    project: Project = Relationship(back_populates="blurbs")

class JobDescription(SQLModel, table=True):
    job_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: Optional[UUID] = Field(default=None, foreign_key="user.user_id", index=True)
    title: str
    company: str
    description: str = Field(default="")  # Raw text
    source_url: Optional[str] = None
    status: str = Field(default="created")  # created, analyzed, tailored, exported
    chat_summary: Optional[str] = None
    # issue 70: lifetime count of tailor runs for this job (capped by JOB_TAILOR_LIMIT)
    retailor_count: int = Field(default=0)
    # Cached JD embedding centroid (issue #54): JSON-encoded float list of the
    # required-skill phrases, for the scorer's semantic component. Refreshed when
    # the description is re-ingested.
    embedding: Optional[str] = None
    embedding_model: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    skills_required: List["JobSkill"] = Relationship(back_populates="job")
    results: List["UserJobResult"] = Relationship(back_populates="job")

class JobSkill(SQLModel, table=True):
    job_skill_id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="jobdescription.job_id")
    skill_id: UUID = Field(foreign_key="skill.skill_id")
    required: bool = True # True = Required, False = Preferred
    weight: float = 1.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    job: JobDescription = Relationship(back_populates="skills_required")
    skill: Skill = Relationship(back_populates="job_links")

class UserJobResult(SQLModel, table=True):
    result_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id")
    job_id: UUID = Field(foreign_key="jobdescription.job_id")
    ats_score: float = 0.0
    
    # JSON columns for detailed reporting
    matched_skills: Dict = Field(default={}, sa_column=Column(JSON))
    missing_skills: List[str] = Field(default=[], sa_column=Column(JSON))
    tailored_resume_content: Dict = Field(default={}, sa_column=Column(JSON)) # The JSON structure of the new resume
    score_breakdown: Dict = Field(default={}, sa_column=Column(JSON))
    tailored_score_breakdown: Dict = Field(default={}, sa_column=Column(JSON)) # Algorithmic score of tailored output (issue #12)
    revision_notes: Optional[str] = None
    export_path: Optional[str] = None
    # issues 91/51: per-run tailoring decision log — the planner's typed action
    # plan plus context features and achieved reward (ATS delta). Append-only;
    # the offline (context, action, reward) dataset for score-driven tuning.
    tailoring_decisions: List = Field(default=[], sa_column=Column(JSON))
    # issue 91: one-level undo for chat REVERT — {content, score_breakdown} of
    # the tailored resume the most recent run replaced.
    tailored_resume_previous: Dict = Field(default={}, sa_column=Column(JSON))
    # issue 71: user's manually edited .tex; NULL means "no manual edits" and
    # exports regenerate from tailored_resume_content. Cleared on re-tailor.
    edited_tex: Optional[str] = None
    edited_tex_updated_at: Optional[datetime] = None
    # issue 118: explicit user arrangement overrides — {section_order, skills,
    # bullets}, each key optional. NULL means "no override" and the ranker's
    # output is used. Set only by user action, never by the pipeline: if
    # tailoring could write this, the ranker would launder its own output into
    # a fake user override and the precedence would stop meaning anything.
    # Unlike edited_tex it encodes arrangement rather than content, so it stays
    # valid when the content beneath it changes and survives a re-tailor.
    layout_overrides: Optional[Dict] = Field(default=None, sa_column=Column(JSON))

    verification_status: str = "pending" # approved, rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="job_results")
    job: JobDescription = Relationship(back_populates="results")


class ChatMessage(SQLModel, table=True):
    message_id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: Optional[UUID] = Field(default=None, foreign_key="jobdescription.job_id")
    # Landing-context messages (job_id NULL) are only separable by owner; without
    # this every user shared one landing conversation (issue #73). Nullable for
    # pre-existing rows, which stay invisible to authenticated users.
    user_id: Optional[UUID] = Field(default=None, foreign_key="user.user_id", index=True)
    role: str        # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class InstitutionCanonical(SQLModel, table=True):
    """Cache: normalized institution name -> canonical dedup key (issue #95).

    Institution names arrive in many forms across resume and LinkedIn ('UC San
    Diego' vs 'University of California, San Diego'). Fuzzy string matching
    can't bridge an acronym like 'UC' to 'University of California', so those
    rows never deduplicated. Each distinct normalized form is resolved once via
    ROR's affiliation matcher and cached here, so dedup can collapse the variants
    onto a single canonical key (a ROR id) and the network lookup is paid only on
    first sighting. Names ROR cannot confidently match cache to their own
    normalized form.
    """
    raw_norm: str = Field(primary_key=True)   # normalized lookup key
    canonical_key: str                         # ROR id, or the normalized string
    display_name: Optional[str] = None         # ROR display name when resolved
    created_at: datetime = Field(default_factory=datetime.utcnow)


class DeletedEntry(SQLModel, table=True):
    """Tombstone for a user-deleted knowledge-graph row (issue #92).

    Ingestion save paths are additive/merge-only, so a re-ingest would
    otherwise resurrect a row the user deliberately deleted. When a user deletes
    an Experience/Education/Project via the Data Explorer, the row is removed and
    a tombstone recorded here. Each save path checks tombstones before creating a
    new row and skips a match, so the deletion sticks across re-ingests. The two
    key fields hold the same values the fuzzy-dedup match functions compare, so a
    tombstone matches the same variants the deduper would (spacing, containment,
    ROR-canonical institutions, shared repo URL).
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    entity_type: str  # "experience" | "education" | "project"
    key_a: str        # experience: title | education: institution | project: name
    key_b: Optional[str] = None  # experience: company | education: degree | project: repo_url
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AIUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    date: str  # YYYY-MM-DD UTC
    # Usage category: "ai" for LLM calls, "linkedin" for paid Bright Data scrapes.
    # Tracked separately so each kind gets its own daily cap.
    kind: str = Field(default="ai")
    call_count: int = Field(default=0)


class JobCard(SQLModel, table=True):
    """Distilled sufficient-statistics record of one completed job (issue #137).

    The episodic memory tier: the knowledge graph holds durable facts and the
    active job chat holds the live transcript, but nothing carried *what
    happened last time you tailored to a similar role*. A JobCard is a
    **deterministic projection** of a finished `UserJobResult` — never an LLM
    summary of the transcript — so two compiles of the same result produce the
    same `payload_hash`. The one exception is `role_family`, a cached, versioned
    classify (see `agents/job_card.py`) so a rebuild costs no LLM call.

    Cards live *beside* the knowledge graph, never inside it, and are rebuilt
    event-driven when the job's result changes. Per the #109 amortization
    amendment that is what keeps the compile off the next turn's critical path;
    compiling lazily at next-tailoring time would put the prefill cost back
    inline and break the condition that justifies distilling at all.

    Absent card ⇒ today's behavior: nothing reads a row that does not exist.
    """
    card_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    job_id: UUID = Field(foreign_key="jobdescription.job_id", index=True)
    # The result this card was compiled from. Not an FK: a card must survive
    # result churn, and a dangling id is preferable to a cascade.
    result_id: Optional[UUID] = Field(default=None)

    title: str = Field(default="")
    company: str = Field(default="")

    # Cached, versioned role classification — the single LLM call in the compile.
    # role_family_key is a digest of the classify inputs; role_family_version
    # tracks the prompt/label set. Recompute only when either changes.
    role_family: Optional[str] = Field(default=None, index=True)
    role_family_version: Optional[int] = Field(default=None)
    role_family_key: Optional[str] = Field(default=None)

    # The deterministic card itself, plus its digest so determinism is directly
    # assertable and an unchanged rebuild can skip the write.
    payload: Dict = Field(default={}, sa_column=Column(JSON))
    payload_hash: Optional[str] = Field(default=None)
    # Fact-level multi-key index (LongMemEval #108 finding 1): the card is
    # indexed under its emphasized items / matched skills / role_family, not
    # just one embedding of the whole card. Expanded at compile time.
    index_keys: List = Field(default=[], sa_column=Column(JSON))

    # When the source result last changed — the recency signal for selection.
    source_updated_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class JDProfile(SQLModel, table=True):
    """Structured, persisted decomposition of one job description (issue #121).

    The system has a codified, editable profile of the *candidate* (the
    knowledge graph) and had nothing equivalent for the *job*: every JD-derived
    quantity was recomputed inline from raw text on every run, in
    `ats_scorer._keyword_coverage`, `ats_scorer._role_level` and
    `keyword_planner._jd_token_counts`. Extracting once and persisting buys two
    things the inline path cannot:

    1. **A stationary reward.** Re-parsing the JD per run lets the reward
       function itself drift between runs, which is fatal for learning a policy
       against it (#51 Phase 2).
    2. **Amortization.** #113's controller scores ~6 prefixes per run, so
       anything JD-derived is otherwise paid 6x for the same answer.

    Determinism here comes from *persistence*, not from the model: an LLM call
    is not byte-stable, so "two runs produce the same profile" is enforced by
    `extraction_key` short-circuiting the second run, never by re-rolling the
    extraction and hoping. Re-extraction is explicit and versioned; it never
    happens silently, and it preserves requirements the user has edited.

    A new table, so `SQLModel.metadata.create_all` picks it up and no ALTER is
    needed. Absent profile => today's behavior: nothing reads a row that does
    not exist.
    """
    profile_id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: UUID = Field(foreign_key="jobdescription.job_id", index=True)
    # Scoped to the owner as well as the job (issue #73): a profile is derived
    # from user-supplied JD text and is read back through a user-scoped API.
    user_id: Optional[UUID] = Field(default=None, foreign_key="user.user_id", index=True)

    # The decomposed profile: requirements[] (in source order) + title_terms[].
    payload: Dict = Field(default={}, sa_column=Column(JSON))
    payload_hash: Optional[str] = Field(default=None)

    # Digest of (description, PROFILE_VERSION). Equal key => the stored profile
    # already describes this exact JD text, so extraction is skipped entirely.
    # This is what makes "a second tailoring run performs no re-extraction" a
    # property of the code rather than a hope about model temperature.
    extraction_key: Optional[str] = Field(default=None, index=True)
    extraction_version: int = Field(default=1)

    # Seniority tier, from the existing ats_scorer._detect_level - deterministic
    # and already tested, so it is not worth an LLM field.
    role_level: Optional[str] = Field(default=None)

    # The computed w(t) map. This issue owns the slot only; #125 populates it.
    weights: Dict = Field(default={}, sa_column=Column(JSON))

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
