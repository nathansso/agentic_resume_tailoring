import type { JobDetail, JobListItem, TailorResult } from "../types";
import { errorMessage, json } from "./http";

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

// ── Manual .tex editing (issue #71) ─────────────────────────

export interface TexResponse {
  tex: string;
  source: "edited" | "generated";
  updated_at: string | null;
}

export async function getTex(jobId: string): Promise<TexResponse> {
  return json(await fetch(`/api/jobs/${jobId}/tex`, { credentials: "include" }));
}

export async function saveTex(jobId: string, tex: string): Promise<{ saved: boolean; updated_at: string }> {
  return json(await fetch(`/api/jobs/${jobId}/tex`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tex }),
    credentials: "include",
  }));
}

export async function discardTex(jobId: string): Promise<void> {
  await json(await fetch(`/api/jobs/${jobId}/tex`, { method: "DELETE", credentials: "include" }));
}

export async function previewPdf(jobId: string, tex: string): Promise<Blob> {
  const res = await fetch(`/api/jobs/${jobId}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tex }),
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(await errorMessage(res, `Compile failed (${res.status})`));
  }
  return res.blob();
}
