export interface User {
  id: string;
  name: string;
  email: string;
  username: string | null;
}

export interface Job {
  id: string;
  title: string;
  company: string;
  status: "created" | "analyzed" | "tailored" | "exported";
  ats_score?: number;
  created_at: string;
}

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
