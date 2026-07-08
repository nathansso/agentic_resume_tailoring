import type { JobDetail, JobListItem, TailorResult } from "../types";
import { json } from "./http";

export async function listJobs(): Promise<JobListItem[]> {
  return json(await fetch("/api/jobs/", { credentials: "include" }));
}

export async function createJob(title: string, company: string, description = ""): Promise<JobListItem> {
  return json(await fetch("/api/jobs/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, company, description }),
    credentials: "include",
  }));
}

export async function deleteJob(jobId: string): Promise<void> {
  await json(await fetch(`/api/jobs/${jobId}`, { method: "DELETE", credentials: "include" }));
}

export async function getJob(jobId: string): Promise<JobDetail> {
  return json(await fetch(`/api/jobs/${jobId}`, { credentials: "include" }));
}

export async function saveDescription(jobId: string, description: string): Promise<JobDetail> {
  return json(await fetch(`/api/jobs/${jobId}/description`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ description }),
    credentials: "include",
  }));
}

export async function analyzeJob(jobId: string): Promise<JobDetail> {
  return json(await fetch(`/api/jobs/${jobId}/analyze`, {
    method: "POST",
    credentials: "include",
  }));
}

export async function tailorJob(jobId: string, revisionNotes = ""): Promise<TailorResult> {
  return json(await fetch(`/api/jobs/${jobId}/tailor`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ revision_notes: revisionNotes }),
    credentials: "include",
  }));
}

export function exportUrl(jobId: string, format: "pdf" | "tex" | "docx"): string {
  return `/api/jobs/${jobId}/export?format=${format}`;
}
