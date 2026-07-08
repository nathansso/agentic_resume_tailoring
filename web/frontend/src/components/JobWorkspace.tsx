import { useEffect, useState, type CSSProperties } from "react";
import type { JobDetail, TailorResult } from "../types";
import { colors, font } from "../theme";
import { saveDescription, analyzeJob, tailorJob, getJob, exportUrl } from "../api/jobs";
import { ChatPanel } from "./ChatPanel";
import { ProgressBar } from "./ProgressBar";
import { ResumeEditor } from "./ResumeEditor";
import { ScoreBreakdownPanel } from "./ScoreBreakdownPanel";

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
  const [tab, setTab] = useState<"resume" | "overview">("overview");

  const tailored = job.status === "tailored" || job.status === "exported";
  const budgetUsed = job.retailor_count >= job.retailor_limit;

  // Land on the editor once a tailored resume exists (issue #71).
  useEffect(() => {
    if (tailored) setTab("resume");
  }, [tailored]);

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
        </div>
      </div>

      {/* Two columns: job chat | resume pane */}
      <div style={s.columns}>
        <div style={s.chatCol}>
          <ChatPanel jobId={job.job_id} onViewChange={onViewChange} onAssistantReply={refreshJob} />
        </div>

        <div style={s.resumeCol}>
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

          {/* Resume / Overview tabs once tailoring has produced a resume */}
          {tailored && (
            <div style={s.tabs}>
              <button
                style={{ ...s.tabBtn, ...(tab === "resume" ? s.tabBtnActive : {}) }}
                onClick={() => setTab("resume")}
              >
                Resume
              </button>
              <button
                style={{ ...s.tabBtn, ...(tab === "overview" ? s.tabBtnActive : {}) }}
                onClick={() => setTab("overview")}
              >
                Overview
              </button>
            </div>
          )}

          {tailored && tab === "resume" && (
            // Remount after each re-tailor so the editor reseeds from the fresh output
            <ResumeEditor key={`${job.job_id}:${job.retailor_count}`} jobId={job.job_id} onEditsChanged={refreshJob} />
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

          {/* Skills + scores */}
          {(!tailored || tab === "overview") && (job.matched_skills.length > 0 || job.missing_skills.length > 0) && (
            <div style={s.skillsRow}>
              {job.matched_skills.length > 0 && (
                <div style={s.skillGroup}>
                  <span style={s.skillGroupLabel}>Matched:</span>
                  <div style={s.chips}>
                    {job.matched_skills.map(sk => (
                      <span key={sk} style={{ ...s.chip, color: colors.accent, borderColor: colors.accent }}>{sk}</span>
                    ))}
                  </div>
                </div>
              )}
              {job.missing_skills.length > 0 && (
                <div style={s.skillGroup}>
                  <span style={s.skillGroupLabel}>Missing:</span>
                  <div style={s.chips}>
                    {job.missing_skills.map(sk => (
                      <span key={sk} style={{ ...s.chip, color: colors.error, borderColor: colors.error }}>{sk}</span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {(!tailored || tab === "overview") && job.score_breakdown && Object.keys(job.score_breakdown).length > 0 && (
            <ScoreBreakdownPanel bd={job.score_breakdown} />
          )}
          {(!tailored || tab === "overview") && job.tailored_score_breakdown && Object.keys(job.tailored_score_breakdown).length > 0 && (
            <ScoreBreakdownPanel bd={job.tailored_score_breakdown} title="Tailored Score Breakdown" />
          )}

          {/* Downloads */}
          <div style={s.exportSection}>
            <span style={s.sectionLabel}>Export</span>
            {tailored ? (
              <div style={s.downloadRow}>
                <a href={exportUrl(job.job_id, "pdf")} style={s.downloadBtn} download>
                  Download PDF
                </a>
                <a href={exportUrl(job.job_id, "tex")} style={s.downloadBtnAlt} download>
                  Download LaTeX
                </a>
                <a
                  href={exportUrl(job.job_id, "docx")}
                  style={s.downloadBtnAlt}
                  download
                  title={job.has_manual_edits ? "DOCX is generated from the AI-tailored content and ignores manual .tex edits" : undefined}
                >
                  Download DOCX
                </a>
              </div>
            ) : (
              <p style={s.hint}>
                Downloads unlock once the resume is tailored
                {job.description ? " — ask the chat to “tailor” or wait for the pipeline to finish." : " — paste a job description above to start."}
              </p>
            )}
            {tailored && job.has_manual_edits && (
              <p style={s.hint}>PDF and LaTeX exports include your manual edits.</p>
            )}
            {tailored && !budgetUsed && (
              <p style={s.hint}>
                Want changes? Tell the chat, e.g. “tailor emphasize Python more” ({job.retailor_limit - job.retailor_count} run{job.retailor_limit - job.retailor_count === 1 ? "" : "s"} left).
              </p>
            )}
            {budgetUsed && (
              <p style={{ ...s.hint, color: colors.error }}>
                Re-tailor budget used ({job.retailor_count}/{job.retailor_limit}).
              </p>
            )}
          </div>
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
  columns: { display: "flex", flex: 1, minHeight: 0 },
  chatCol: {
    flex: 1, minWidth: 0, borderRight: `1px solid ${colors.primary}`,
    display: "flex", flexDirection: "column",
  },
  resumeCol: {
    flex: 1, minWidth: 0, overflowY: "auto", padding: "1rem",
    display: "flex", flexDirection: "column", gap: "0.875rem",
  },
  tabs: { display: "flex", gap: "0.25rem", borderBottom: `1px solid ${colors.primary}` },
  tabBtn: {
    background: "transparent", border: "none", color: colors.textMuted,
    fontSize: font.size.sm, padding: "0.25rem 0.625rem", cursor: "pointer",
    fontFamily: "inherit", borderRadius: 0,
  },
  tabBtnActive: { color: colors.accent, borderBottom: `2px solid ${colors.accent}` },
  errorBox: { display: "flex", flexDirection: "column", gap: "0.5rem" },
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
  skillsRow: { display: "flex", flexDirection: "column", gap: "0.5rem" },
  skillGroup: { display: "flex", alignItems: "flex-start", gap: "0.5rem" },
  skillGroupLabel: { color: colors.textMuted, fontSize: font.size.sm, flexShrink: 0, paddingTop: "0.125rem" },
  chips: { display: "flex", flexWrap: "wrap", gap: "0.375rem" },
  chip: { fontSize: "0.7rem", border: "1px solid", padding: "0.1rem 0.375rem" },
  exportSection: {
    display: "flex", flexDirection: "column", gap: "0.5rem",
    borderTop: `1px solid ${colors.primary}`, paddingTop: "0.875rem", marginTop: "auto",
  },
  sectionLabel: { color: colors.textMuted, fontSize: font.size.sm, fontWeight: 700, letterSpacing: "0.05em" },
  downloadRow: { display: "flex", gap: "0.75rem", flexWrap: "wrap" },
  downloadBtn: {
    background: colors.accent, color: colors.background, fontWeight: 700,
    fontSize: font.size.sm, padding: "0.375rem 0.75rem", textDecoration: "none",
    fontFamily: "inherit", display: "inline-block",
  },
  downloadBtnAlt: {
    background: "transparent", color: colors.accent, border: `1px solid ${colors.accent}`,
    fontWeight: 700, fontSize: font.size.sm, padding: "0.375rem 0.75rem",
    textDecoration: "none", fontFamily: "inherit", display: "inline-block",
  },
};
