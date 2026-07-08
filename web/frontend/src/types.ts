export interface User {
  id: string;
  name: string;
  email: string;
  username: string | null;
}

export interface JobListItem {
  job_id: string;
  title: string;
  company: string;
  status: "created" | "analyzed" | "tailored" | "exported";
  ats_score: number | null;
}

export interface ScoreComponent {
  score: number;
  weight: number;
  [key: string]: unknown;
}

export interface ScoreBreakdown {
  composite?: number;
  baseline_composite?: number;
  delta?: number;
  skill_coverage?: ScoreComponent;
  keyword_coverage?: ScoreComponent & {
    matched_keywords?: string[];
    missing_keywords?: string[];
    total?: number;
  };
  section_presence?: ScoreComponent & {
    present?: string[];
    missing?: string[];
  };
  role_level?: ScoreComponent & {
    jd_level?: string;
    resume_level?: string;
  };
}

export interface JobDetail extends JobListItem {
  description: string;
  matched_skills: string[];
  missing_skills: string[];
  score_breakdown: ScoreBreakdown;
  tailored_score_breakdown: ScoreBreakdown;
  retailor_count: number;
  retailor_limit: number;
}

export interface TailorResult {
  ats_score: number;
  matched_skills: string[];
  missing_skills: string[];
  status: string;
  retailor_count: number;
  retailor_limit: number;
}

export interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface SkillRow {
  name: string;
  category: string;
  source: string;
  proficiency: string;
  confidence: string;
  is_core: boolean;
}

export interface ExpRow {
  title: string;
  company: string;
  start: string;
  end: string;
}

export interface ProjectRow {
  name: string;
  url: string;
  desc: string;
}

export interface EducationRow {
  institution: string;
  degree: string;
  location: string;
  start: string;   // free-form, verbatim from the resume (e.g. "Sep 2021")
  end: string;     // e.g. "June 2025", "Expected June 2027", or "2027"
  gpa: string;
}

export interface GraphData {
  top_skills: { name: string; connections: number }[];
  by_category: Record<string, number>;
  evidence: Record<string, string[]>;
}

export interface ProfileData {
  user_id: string;
  name: string;
  email: string;
  phone: string;
  location: string;
  github_username: string;
  linkedin_url: string;
  linkedin_ingest_status: string | null;   // null | "importing" | "done" | "failed"
  linkedin_ingest_error: string | null;
  linkedin_ingested_at: string | null;
  skills: number;
  experiences: number;
  projects: number;
  sources: string[];
}

// Legacy aliases kept for backward compat
export type Job = JobListItem;
export interface Skill {
  name: string;
  category: string | null;
  proficiency: number | null;
  evidence_source: string | null;
  confidence_score: number;
}
export interface Experience {
  id: string;
  title: string;
  company: string;
  start_date: string | null;
  end_date: string | null;
  bullets: string[];
}
export interface Project {
  id: string;
  name: string;
  description: string | null;
  repo_url: string | null;
}
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
