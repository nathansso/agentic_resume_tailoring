import type { ProfileData, SkillRow, ExpRow, ProjectRow, EducationRow, AchievementRow, GraphData } from "../types";
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
