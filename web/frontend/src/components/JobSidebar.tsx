import { useState } from "react";
import { X } from "lucide-react";
import type { JobListItem } from "../types";
import { cn } from "../lib/utils";

interface Props {
  jobs: JobListItem[];
  selectedJobId: string | null;
  onSelect: (jobId: string) => void;
  onCreate: (title: string, company: string, description: string) => void;
  onDelete: (jobId: string) => void;
  loading: boolean;
}

function atsColor(score: number | null): string {
  if (score === null) return "text-muted-foreground";
  if (score >= 70) return "text-success";
  if (score >= 50) return "text-warning";
  return "text-destructive";
}

function statusLabel(status: string): string {
  return status === "created" ? "" : ` [${status}]`;
}

const inputClass =
  "rounded-md border border-input bg-background px-2 py-1.5 text-sm outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary";

export function JobSidebar({ jobs, selectedJobId, onSelect, onCreate, onDelete, loading }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [description, setDescription] = useState("");

  function handleCreate() {
    if (!title.trim() || !company.trim()) return;
    onCreate(title.trim(), company.trim(), description.trim());
    setTitle("");
    setCompany("");
    setDescription("");
    setShowForm(false);
  }

  // Collapsed rail that expands on hover (overlaying the content so panes
  // don't reflow). Stays pinned open while the create form is in use.
  return (
    <div
      className="relative w-[3.75rem] flex-shrink-0"
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => {
        if (!showForm) setExpanded(false);
      }}
    >
      <div className="flex h-full w-[3.75rem] cursor-pointer flex-col items-center gap-2 border-r border-border bg-card pt-3">
        <span className="text-[0.7rem] font-bold tracking-[0.15em] text-accent">
          JOBS
        </span>
        {jobs.length > 0 && (
          <span className="rounded border border-border px-1 text-[0.7rem] text-muted-foreground">
            {jobs.length}
          </span>
        )}
      </div>

      {expanded && (
        <div className="absolute inset-y-0 left-0 z-20 flex w-[calc(32ch+1.5rem)] flex-col overflow-hidden border-r border-border bg-card shadow-[4px_0_16px_rgba(0,0,0,0.45)]">
          {sidebarContent()}
        </div>
      )}
    </div>
  );

  function sidebarContent() {
    return (
      <>
        <div className="flex flex-shrink-0 items-center justify-between border-b border-border px-3 py-2.5">
          <span className="font-semibold">Jobs</span>
          <button
            className="rounded-md border border-border px-2 py-1 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
            onClick={() => setShowForm(v => !v)}
          >
            {showForm ? "Cancel" : "+ New"}
          </button>
        </div>

        {showForm && (
          <div className="flex flex-shrink-0 flex-col gap-1.5 border-b border-border px-3 py-2.5">
            <input
              className={inputClass}
              placeholder="Job title"
              value={title}
              onChange={e => setTitle(e.target.value)}
              autoFocus
            />
            <input
              className={inputClass}
              placeholder="Company"
              value={company}
              onChange={e => setCompany(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleCreate()}
            />
            <textarea
              className={cn(inputClass, "resize-y leading-relaxed")}
              placeholder="Paste job description (analyzes + tailors automatically)"
              value={description}
              onChange={e => setDescription(e.target.value)}
              rows={5}
            />
            <button
              className="rounded-md bg-primary px-2 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90"
              onClick={handleCreate}
            >
              Save
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto py-1.5">
          {loading && <p className="px-3 py-2 text-sm text-muted-foreground">Loading…</p>}
          {!loading && jobs.length === 0 && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              No jobs yet — create one above
            </p>
          )}
          {jobs.map(job => {
            const selected = job.job_id === selectedJobId;
            return (
              <div
                key={job.job_id}
                className={cn(
                  "flex cursor-pointer items-center justify-between gap-2 border-l-2 px-3 py-2 transition-colors",
                  selected
                    ? "border-primary bg-primary/10"
                    : "border-transparent hover:bg-secondary"
                )}
                onClick={() => onSelect(job.job_id)}
              >
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm font-semibold">{job.title}</span>
                  <span className="truncate text-sm text-muted-foreground">
                    {job.company}
                    <span className="text-muted-foreground">{statusLabel(job.status)}</span>
                  </span>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1.5">
                  {job.ats_score !== null && (
                    <span className={cn("text-[0.7rem] font-bold", atsColor(job.ats_score))}>
                      {Math.round(job.ats_score)}%
                    </span>
                  )}
                  <button
                    className="rounded p-0.5 text-muted-foreground transition-colors hover:bg-destructive/15 hover:text-destructive"
                    onClick={e => { e.stopPropagation(); onDelete(job.job_id); }}
                    title="Delete job"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      </>
    );
  }
}
