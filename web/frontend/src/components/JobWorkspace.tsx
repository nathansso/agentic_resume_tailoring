import { useEffect, useState, type CSSProperties } from "react";
import type { JobDetail, TailorResult } from "../types";
import { colors, font } from "../theme";
import { saveDescription, analyzeJob, tailorJob, getJob, exportUrl } from "../api/jobs";
import { ChatPanel } from "./ChatPanel";
import { JobInsights } from "./JobInsights";
import { ProgressBar } from "./ProgressBar";
import { ResumeSplit } from "./ResumeSplit";
import { jobWelcome } from "../lib/welcome";

interface Props {
  job: JobDetail;
  /** Created this session with a JD — kick off analyze + tailor automatically. */
  autoStart: boolean;
  onJobUpdate: (job: JobDetail) => void;
  onViewChange: (view: string) => void;
}

type Phase = "idle" | "analyzing" | "tailoring";

// Module-level so React StrictMode's double effect-fire (and remounts while a
// chain is still running) can't launch the pipeline twice for the same job.
const startedChains = new Set<string>();

function statusColor(status: string): string {
  if (status === "exported" || status === "tailored") return colors.accent;
  if (status === "analyzed") return "#d29922";
  return colors.textMuted;
}

export function JobWorkspace({ job, autoStart, onJobUpdate, onViewChange }: Props) {
  const [descInput, setDescInput] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);

  const tailored = job.status === "tailored" || job.status === "exported";
  const budgetUsed = job.retailor_count >= job.retailor_limit;

  async function runChain(startAt: "analyze" | "tailor") {
    setError(null);
    try {
      let detail = job;
      if (startAt === "analyze") {
        setPhase("analyzing");
        detail = await analyzeJob(job.job_id);
        onJobUpdate(detail);
      }
      setPhase("tailoring");
      const result: TailorResult = await tailorJob(job.job_id);
      onJobUpdate({ ...detail, ...result } as JobDetail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      setPhase("idle");
    }
  }

  // Auto-run analyze + tailor for jobs created with a pasted JD (issue #70).
  useEffect(() => {
    if (!autoStart || startedChains.has(job.job_id)) return;
    if (job.status === "created" && job.description) {
      startedChains.add(job.job_id);
      runChain("analyze");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.job_id, autoStart]);

  async function handleSaveDescAndRun() {
    if (!descInput.trim()) return;
    setError(null);
    setPhase("analyzing");
    try {
      const updated = await saveDescription(job.job_id, descInput);
      onJobUpdate(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save description");
      setPhase("idle");
      return;
    }
    startedChains.add(job.job_id);
    await runChain("analyze");
  }

  function refreshJob() {
    // Chat can change job state (re-tailor, analyze, JD paste) — resync.
    getJob(job.job_id).then(onJobUpdate).catch(() => {});
  }

  const phaseLabel =
    phase === "analyzing"
      ? "Analyzing job description… (~60s)"
      : "Tailoring resume… this may take 1–2 minutes";

  return (
    <div style={s.workspace}>
      {/* Header */}
      <div style={s.header}>
        <div style={s.headerLeft}>
          <h2 style={s.jobTitle}>{job.title}</h2>
          <span style={s.company}>{job.company}</span>
        </div>
        <div style={s.headerRight}>
          <span style={{ ...s.statusBadge, color: statusColor(job.status) }}>[{job.status}]</span>
          {job.ats_score !== null && (
            <span style={{ ...s.atsBadge, color: job.ats_score >= 70 ? colors.accent : job.ats_score >= 50 ? "#d29922" : colors.error }}>
              ATS: {Math.round(job.ats_score)}%
            </span>
          )}
          <span style={{ ...s.budget, color: budgetUsed ? colors.error : colors.textMuted }}>
            Tailor runs: {job.retailor_count}/{job.retailor_limit}
          </span>
          {tailored && (
            <span style={s.exportLinks}>
              <span style={s.exportLabel}>Export:</span>
              <a
                href={exportUrl(job.job_id, "pdf")}
                style={s.exportLink}
                download
                title={job.has_manual_edits ? "Includes your manual .tex edits" : undefined}
              >
                PDF
              </a>
              <a
                href={exportUrl(job.job_id, "tex")}
                style={s.exportLink}
                download
                title={job.has_manual_edits ? "Includes your manual .tex edits" : undefined}
              >
                LaTeX
              </a>
              <a
                href={exportUrl(job.job_id, "docx")}
                style={s.exportLink}
                download
                title={job.has_manual_edits ? "DOCX is generated from the AI-tailored content and ignores manual .tex edits" : undefined}
              >
                DOCX
              </a>
            </span>
          )}
        </div>
      </div>

      {/* Three panes: insights + chat | .tex editor | compiled preview */}
      <div style={s.columns}>
        <div style={s.chatCol}>
          <JobInsights job={job} />
          <div style={s.chatWrap}>
            <ChatPanel
              jobId={job.job_id}
              welcome={jobWelcome(job)}
              onViewChange={onViewChange}
              onAssistantReply={refreshJob}
            />
          </div>
        </div>

        <div style={s.resumeArea}>
          {error && (
            <div style={s.errorBox}>
              <p style={s.error}>{error}</p>
              <button
                style={s.actionBtn}
                onClick={() => {
                  if (job.has_manual_edits &&
                      !window.confirm("Re-tailoring will discard your manual .tex edits. Continue?")) return;
                  runChain(job.status === "created" ? "analyze" : "tailor");
                }}
              >
                Retry
              </button>
            </div>
          )}

          {phase !== "idle" && <ProgressBar label={phaseLabel} />}

          {tailored && (
            // Remount after each re-tailor so the editor reseeds from the fresh output
            <ResumeSplit key={`${job.job_id}:${job.retailor_count}`} jobId={job.job_id} onEditsChanged={refreshJob} />
          )}

          {/* No JD yet: paste panel */}
          {phase === "idle" && !job.description && (
            <>
              <p style={s.hint}>Paste the full job description to analyze and tailor automatically.</p>
              <textarea
                style={s.textarea}
                value={descInput}
                onChange={e => setDescInput(e.target.value)}
                rows={12}
                placeholder="Paste job description here…"
                autoFocus
              />
              <button
                style={{ ...s.actionBtn, background: colors.accent, color: colors.background }}
                onClick={handleSaveDescAndRun}
                disabled={!descInput.trim()}
              >
                Save & Run
              </button>
            </>
          )}

          {/* JD present but nothing tailored yet and no pipeline running */}
          {phase === "idle" && !tailored && job.description && !error && (
            <p style={s.hint}>No tailored resume yet — ask the chat to “tailor”.</p>
          )}
        </div>
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  workspace: { display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" },
  header: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    gap: "1rem", padding: "0.625rem 1rem", borderBottom: `1px solid ${colors.primary}`,
    background: colors.surface, flexShrink: 0,
  },
  headerLeft: { display: "flex", alignItems: "baseline", gap: "0.625rem", minWidth: 0 },
  jobTitle: {
    margin: 0, color: colors.accent, fontSize: font.size.lg, fontWeight: 700,
    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
  },
  company: { color: colors.textMuted, fontSize: font.size.sm, whiteSpace: "nowrap" },
  headerRight: { display: "flex", alignItems: "baseline", gap: "0.75rem", flexShrink: 0 },
  statusBadge: { fontSize: font.size.sm, fontWeight: 600 },
  atsBadge: { fontSize: font.size.base, fontWeight: 700 },
  budget: { fontSize: font.size.sm },
  exportLinks: { display: "flex", alignItems: "baseline", gap: "0.5rem" },
  exportLabel: { color: colors.textMuted, fontSize: font.size.sm },
  exportLink: {
    color: colors.accent, fontSize: font.size.sm, fontWeight: 700,
    textDecoration: "none", border: `1px solid ${colors.accent}`,
    padding: "0.125rem 0.5rem",
  },
  columns: { display: "flex", flex: 1, minHeight: 0 },
  chatCol: {
    flex: "0 0 320px", minWidth: 280, borderRight: `1px solid ${colors.primary}`,
    display: "flex", flexDirection: "column", minHeight: 0,
  },
  chatWrap: { flex: 1, minHeight: 0, display: "flex", flexDirection: "column" },
  resumeArea: {
    flex: 1, minWidth: 0, overflow: "hidden", padding: "0.75rem 1rem",
    display: "flex", flexDirection: "column", gap: "0.75rem",
  },
  errorBox: { display: "flex", flexDirection: "column", gap: "0.5rem", flexShrink: 0 },
  error: { margin: 0, color: colors.error, fontSize: font.size.sm },
  hint: { margin: 0, color: colors.textMuted, fontSize: font.size.sm },
  textarea: {
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.base, padding: "0.5rem 0.75rem",
    fontFamily: "inherit", outline: "none", borderRadius: 0, resize: "vertical", lineHeight: 1.5,
  },
  actionBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.375rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0, alignSelf: "flex-start",
  },
};
