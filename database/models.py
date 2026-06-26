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
    # LinkedIn ingestion lifecycle (issue 13: Bright Data)
    linkedin_ingested_url: Optional[str] = None       # last URL successfully scraped
    linkedin_ingest_status: Optional[str] = None       # None | "importing" | "done" | "failed"
    linkedin_ingest_error: Optional[str] = None        # last failure message, if any
    linkedin_ingested_at: Optional[datetime] = None
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
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="experiences")

class Project(SQLModel, table=True):
    project_id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id")
    name: str
    description: Optional[str] = None
    repo_url: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    metrics: Dict = Field(default={}, sa_column=Column(JSON)) # GitHub signals: stars, languages, readme_length (issue #46)
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

    verification_status: str = "pending" # approved, rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="job_results")
    job: JobDescription = Relationship(back_populates="results")


class ChatMessage(SQLModel, table=True):
    message_id: UUID = Field(default_factory=uuid4, primary_key=True)
    job_id: Optional[UUID] = Field(default=None, foreign_key="jobdescription.job_id")
    role: str        # "user" | "assistant"
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AIUsage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(foreign_key="user.user_id", index=True)
    date: str  # YYYY-MM-DD UTC
    # Usage category: "ai" for LLM calls, "linkedin" for paid Bright Data scrapes.
    # Tracked separately so each kind gets its own daily cap.
    kind: str = Field(default="ai")
    call_count: int = Field(default=0)
