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
    linkedin_url: Optional[str] = None
    github_username: Optional[str] = None
    onboarding_complete: bool = Field(default=False)
    onboarding_steps: Dict = Field(default={}, sa_column=Column(JSON))
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
    title: str
    company: str
    description: str # Raw text
    source_url: Optional[str] = None
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
    
    verification_status: str = "pending" # approved, rejected
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    user: User = Relationship(back_populates="job_results")
    job: JobDescription = Relationship(back_populates="results")
