import type { KeyboardEvent } from "react";
import type { EvidenceKind } from "../types";
import type { JobOverlay } from "../lib/evidenceMap";
import type { JobCompare } from "../hooks/useJobCompare";
import { cn } from "../lib/utils";

/*
 * Pieces the evidence map (#136) and the area map (#320) draw the same way: the glyph
 * that marks an item's kind, the keyboard rule for a node, and the "compare to a job"
 * controls and counts. Colour and type come from the `graph-*` tokens in index.css.
 */

export const KIND_LABEL: Record<EvidenceKind, string> = { exp: "Experience", proj: "Project", edu: "Education" };
export const KIND_FILL: Record<EvidenceKind, string> = {
  exp: "fill-graph-experience", proj: "fill-graph-project", edu: "fill-graph-education",
};

/** Run `fn` on Enter or Space, the keys a button answers to. */
export function onActivate(fn: () => void) {
  return (e: KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      fn();
    }
  };
}

/** Square for an experience, diamond for a project, triangle for education: kind never rests on hue alone. */
export function Glyph({ kind, size }: { kind: EvidenceKind; size: number }) {
  const cls = KIND_FILL[kind];
  if (kind === "exp") return <rect x={-size} y={-size} width={2 * size} height={2 * size} rx={1.6} className={cls} />;
  if (kind === "proj") {
    const s = size * 1.3;
    return <path d={`M0,${-s}L${s},0L0,${s}L${-s},0Z`} className={cls} />;
  }
  return <path d={`M0,${-size * 1.25}L${size * 1.2},${size * 0.9}L${-size * 1.2},${size * 0.9}Z`} className={cls} />;
}

export function Count({ n, label, tone }: { n: number; label: string; tone?: "proven" | "gap" }) {
  return (
    <span className="inline-flex items-baseline gap-1.5">
      <span
        className={cn(
          "font-mono text-[12px] font-medium tabular-nums",
          tone === "proven" && "text-accent",
          tone === "gap" && n > 0 && "text-graph-gap",
        )}
      >
        {n}
      </span>
      <span>{label}</span>
    </span>
  );
}

/** Required and preferred, each as three separate counts. Never one number, and nothing from the ATS composite. */
export function JobLine({ overlay }: { overlay: JobOverlay }) {
  const group = (name: string, c: JobOverlay["required"]) => (
    <span className="inline-flex flex-wrap items-baseline gap-x-3">
      <span className="font-medium text-foreground">{name}</span>
      <Count n={c.evidenced} label="backed by a bullet" tone="proven" />
      <Count n={c.claimed} label="claimed without evidence" tone="gap" />
      <Count n={c.missing} label="missing" tone="gap" />
    </span>
  );
  return (
    <p className="flex flex-wrap items-baseline gap-x-6 gap-y-1 text-[13px] text-muted-foreground" aria-label="Job coverage">
      {group("Required", overlay.required)}
      {group("Preferred", overlay.preferred)}
    </p>
  );
}

/** The "Compare to a job" switch and, once it is on, the picker over saved jobs. */
export function JobToggle({ job }: { job: JobCompare }) {
  return (
    <>
      <button
        type="button" role="switch" aria-checked={job.jobOn}
        onClick={() => job.setJobOn(!job.jobOn)}
        className={cn(
          "inline-flex items-center gap-2 rounded-md border px-2.5 py-1.5 text-[13px] transition-colors",
          job.jobOn ? "border-foreground/40 bg-muted text-foreground" : "border-border text-muted-foreground hover:bg-muted",
        )}
      >
        <span
          aria-hidden="true"
          className={cn("relative h-3.5 w-6 rounded-full transition-colors", job.jobOn ? "bg-foreground/70" : "bg-muted-foreground/30")}
        >
          <span
            className={cn(
              "absolute top-0.5 h-2.5 w-2.5 rounded-full bg-background transition-all motion-reduce:transition-none",
              job.jobOn ? "left-3" : "left-0.5",
            )}
          />
        </span>
        Compare to a job
      </button>
      {job.jobOn && job.jobs && job.jobs.length > 0 && (
        <label className="inline-flex items-center gap-2 text-[13px] text-muted-foreground">
          <span className="sr-only">Job to compare with</span>
          <select
            value={job.jobId ?? ""} onChange={(e) => job.setJobId(e.target.value)}
            className="max-w-[16rem] rounded-md border border-border bg-background px-2 py-1.5 text-[13px] text-foreground"
          >
            {job.jobs.map((j) => <option key={j.job_id} value={j.job_id}>{j.title} · {j.company}</option>)}
          </select>
        </label>
      )}
    </>
  );
}

/** What the job comparison has to say: no jobs, an error, an unanalyzed job, or the counts. */
export function JobStatus({ job, overlay }: { job: JobCompare; overlay: JobOverlay | null }) {
  const noJobs = job.jobOn && job.jobs !== null && job.jobs.length === 0;
  return (
    // A fixed slot, so turning the job on adds a line without pushing the map down.
    <div className="min-h-[2.75rem] space-y-1 sm:min-h-[1.5rem]">
      {noJobs && <p className="text-[13px] text-muted-foreground">No saved jobs yet. Add one from the sidebar to compare.</p>}
      {job.jobError && <p className="text-[13px] text-destructive">{job.jobError}</p>}
      {job.jobOn && job.jobId && job.jobSkills && job.jobSkills.length === 0 && !job.jobError && (
        <p className="text-[13px] text-muted-foreground">
          This job has no analyzed skills yet. Analyze it from its page and they will appear here.
        </p>
      )}
      {overlay && <JobLine overlay={overlay} />}
    </div>
  );
}
