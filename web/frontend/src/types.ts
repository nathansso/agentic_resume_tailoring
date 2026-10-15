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

/** Tailoring explainability written by the chat re-tailor flow
    (UserJobResult.matched_skills["_explainability"]). */
export interface Explainability {
  matched: string[];
  emphasized: string[];
  inferred: string[];
  missing: string[];
  ats_score: number;
}

export interface JobDetail extends JobListItem {
  description: string;
  matched_skills: string[];
  missing_skills: string[];
  score_breakdown: ScoreBreakdown;
  tailored_score_breakdown: ScoreBreakdown;
  retailor_count: number;
  retailor_limit: number;
  has_manual_edits: boolean;
  explainability: Explainability | null;
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
  id: string;
  title: string;
  company: string;
  start: string;
  end: string;
  description: string;
  bullets: string[];
  incomplete: boolean;   // missing info — surfaced for review (issue #85)
  missing: string[];     // which parts are missing (title/company/dates/details)
}

export interface ProjectRow {
  id: string;
  name: string;
  url: string;
  desc: string;
  description: string;
  repo_url: string;
  demo_url: string;
  start: string;
  end: string;
}

export interface EducationRow {
  id: string;
  institution: string;
  degree: string;
  location: string;
  start: string;   // free-form, verbatim from the resume (e.g. "Sep 2021")
  end: string;     // e.g. "June 2025", "Expected June 2027", or "2027"
  gpa: string;
}

export interface AchievementRow {
  title: string;
  description: string;
  issuer: string;
  date: string;
}

export interface GraphData {
  top_skills: { name: string; connections: number }[];
  by_category: Record<string, number>;
  evidence: Record<string, string[]>;
}

// ── Evidence map (issue #136): GET /api/profile/graph/view ───────────────────────

/** One entry of an item's text: a bullet, a project blurb, or a project's description. */
export interface EvidenceText {
  raw_index: number;        // position in the stored list; what an editor writes back
  index: number | null;     // the harness's `#b<n>`, counted over non-empty entries; null when empty
  text: string;
  blurb_id: string | null;  // set for project blurbs, which are edited through their own route
}

export type EvidenceKind = "exp" | "proj" | "edu";
export type EvidenceTextSource = "bullets" | "blurbs" | "description" | "none" | "fields";

export interface EvidenceItem {
  key: string;
  id: string;
  kind: EvidenceKind;
  title: string;
  org: string;
  start: string;
  end: string;
  description: string;
  text: EvidenceText[];
  text_source: EvidenceTextSource;
  cite_key: string;
  parent_key: string | null;
  achievements: { id: string; title: string; issuer: string; date: string }[];
  repo_url: string;
  /** Projects only (#322): has the user said where it was done? Absent on experiences and education. */
  context_status?: ProjectContextStatus;
  fields: Record<string, string>;   // the editable columns, under their API names
}

export type ProjectContextStatus = "unreviewed" | "linked" | "personal";

/** A suggested place for a project, in the view's item keys (#322). */
export interface ProjectCandidate {
  key: string;                      // `exp/<id>` or `edu/<id>`
  title: string;
  reason: string;
}

/** One project's place and the rule-based suggestions for it, from /api/profile/projects/contexts. */
export interface ProjectContext {
  project_id: string;
  status: ProjectContextStatus;
  parent: string | null;            // the view key of the role or degree it is linked to
  candidates: ProjectCandidate[];   // best first; never "personal"
}

export interface EvidenceSkill {
  key: string;
  name: string;
  category: string;
  area: string;
  proficiency: number | null;       // 1-5, highest across sources
  sources: string[];                // resume | github | linkedin | manual | chat | host
  is_core: boolean;
}

export interface EvidenceEdge {
  item_key: string;
  skill_key: string;
  cites: number[];                  // `#b<n>` ordinals of the bullets that name the skill
  github: boolean;                  // a recorded GitHub source also ties the project to the skill
  marks: [number, number][][];      // per cite: [start, end) offsets of the words that matched
}

export interface EvidenceStats {
  items: number;
  skills: number;
  evidenced: number;                // cited by at least one bullet
  unevidenced: number;              // no bullet and no GitHub source
  github_only: number;
  needs_text: number;               // experiences and projects with no text to link from
}

export interface EvidenceView {
  items: EvidenceItem[];
  skills: EvidenceSkill[];
  edges: EvidenceEdge[];
  stats: EvidenceStats;
}

/** A skill the user dismissed from the evidence map and can restore (#321). */
export interface DismissedSkill {
  skill_id: string;
  name: string;
  dismissed_at: string;             // ISO timestamp
  sources: string[];                // the kinds of source that had claimed it
}

export interface JobSkillRow {
  name: string;
  required: boolean;                // false = preferred
  weight: number;
  area: string;
  /** Every keyword the requirement names (a job opened through the harness); a profile skill matches on any. */
  terms?: string[];
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
