import type {
  ProfileData, SkillRow, ExpRow, ProjectRow, EducationRow, AchievementRow, GraphData, EvidenceView,
  DismissedSkill, ProjectContext, ProjectContextStatus,
} from "../types";
import { json } from "./http";

export async function getProfile(): Promise<ProfileData> {
  return json(await fetch("/api/profile/", { credentials: "include" }));
}

export async function updateProfile(data: Partial<ProfileData>): Promise<{ result: string }> {
  return json(await fetch("/api/profile/", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "include",
  }));
}

export async function getSkills(): Promise<SkillRow[]> {
  return json(await fetch("/api/profile/skills", { credentials: "include" }));
}

export async function setSkillCore(name: string, is_core: boolean): Promise<{ result: string }> {
  return json(await fetch("/api/profile/skills/core", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, is_core }),
    credentials: "include",
  }));
}

export async function getExperiences(): Promise<ExpRow[]> {
  return json(await fetch("/api/profile/experiences", { credentials: "include" }));
}

export async function getProjects(): Promise<ProjectRow[]> {
  return json(await fetch("/api/profile/projects", { credentials: "include" }));
}

export async function getEducation(): Promise<EducationRow[]> {
  return json(await fetch("/api/profile/education", { credentials: "include" }));
}

export async function getAchievements(): Promise<AchievementRow[]> {
  return json(await fetch("/api/profile/achievements", { credentials: "include" }));
}

export async function getGraph(): Promise<GraphData> {
  return json(await fetch("/api/profile/graph", { credentials: "include" }));
}

export async function getEvidenceView(): Promise<EvidenceView> {
  return json(await fetch("/api/profile/graph/view", { credentials: "include" }));
}

// ── Manual edit & delete of ingested rows (issue #92) ──────────────────────────
// Payload keys are the backend field names (start_date/end_date/repo_url/…);
// only the keys present are applied.

export interface ExperienceEdit {
  title?: string; company?: string; start_date?: string | null;
  end_date?: string | null; description?: string | null; bullets?: string[];
}
export interface EducationEdit {
  institution?: string; degree?: string; location?: string | null;
  start_date?: string | null; end_date?: string | null; gpa?: string | null;
}
export interface ProjectEdit {
  name?: string; description?: string | null; repo_url?: string | null;
  demo_url?: string | null; start_date?: string | null; end_date?: string | null;
}

async function patch<T>(url: string, body: unknown): Promise<T> {
  return json(await fetch(url, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  }));
}

async function del(url: string): Promise<{ result: string }> {
  return json(await fetch(url, { method: "DELETE", credentials: "include" }));
}

export const updateExperience = (id: string, body: ExperienceEdit): Promise<ExpRow> =>
  patch(`/api/profile/experiences/${id}`, body);
export const deleteExperience = (id: string): Promise<{ result: string }> =>
  del(`/api/profile/experiences/${id}`);

export const updateEducation = (id: string, body: EducationEdit): Promise<EducationRow> =>
  patch(`/api/profile/education/${id}`, body);
export const deleteEducation = (id: string): Promise<{ result: string }> =>
  del(`/api/profile/education/${id}`);

export const updateProject = (id: string, body: ProjectEdit): Promise<ProjectRow> =>
  patch(`/api/profile/projects/${id}`, body);
export const deleteProject = (id: string): Promise<{ result: string }> =>
  del(`/api/profile/projects/${id}`);

// ── Dismissing a skill claim nothing proves (issue #321) ───────────────────────

export const getDismissedSkills = async (): Promise<DismissedSkill[]> =>
  json(await fetch("/api/profile/skills/dismissed", { credentials: "include" }));

async function post<T>(url: string): Promise<T> {
  return json(await fetch(url, { method: "POST", credentials: "include" }));
}

/** Refused with 409 when a bullet cites the skill; the message says to edit the bullet. */
export const dismissSkill = (skillId: string): Promise<{ skill_id: string; name: string }> =>
  post(`/api/profile/skills/${skillId}/dismiss`);
export const restoreSkill = (skillId: string): Promise<{ skill_id: string; name: string }> =>
  post(`/api/profile/skills/${skillId}/restore`);

// ── Where a project was done (issue #322) ──────────────────────────────────────

/** Each project's place and the suggestions for it. Read-only: nothing is stored until a PUT. */
export const getProjectContexts = async (): Promise<ProjectContext[]> =>
  json(await fetch("/api/profile/projects/contexts", { credentials: "include" }));

/** Confirm where a project was done: `exp/<id>`, `edu/<id>`, `personal` or `unreviewed`. */
export const setProjectContext = async (
  projectId: string, context: string,
): Promise<{ project_id: string; status: ProjectContextStatus; parent: string | null }> =>
  json(await fetch(`/api/profile/projects/${projectId}/context`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context }),
    credentials: "include",
  }));

/** Edit the text of one project bullet. Content only: the bullet keeps its position (#327). */
export const updateProjectBlurb = (
  projectId: string, blurbId: string, content: string,
): Promise<{ id: string; project_id: string; content: string; seq: number | null; style: string }> =>
  patch(`/api/profile/projects/${projectId}/blurbs/${blurbId}`, { content });
