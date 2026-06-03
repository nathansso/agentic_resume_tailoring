import type { ProfileData, SkillRow, ExpRow, ProjectRow, GraphData } from "../types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function getProfile(): Promise<ProfileData> {
  return json(await fetch("/api/profile", { credentials: "include" }));
}

export async function updateProfile(data: Partial<ProfileData>): Promise<{ result: string }> {
  return json(await fetch("/api/profile", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
    credentials: "include",
  }));
}

export async function getSkills(): Promise<SkillRow[]> {
  return json(await fetch("/api/profile/skills", { credentials: "include" }));
}

export async function getExperiences(): Promise<ExpRow[]> {
  return json(await fetch("/api/profile/experiences", { credentials: "include" }));
}

export async function getProjects(): Promise<ProjectRow[]> {
  return json(await fetch("/api/profile/projects", { credentials: "include" }));
}

export async function getGraph(): Promise<GraphData> {
  return json(await fetch("/api/profile/graph", { credentials: "include" }));
}
