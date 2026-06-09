import { useState, type CSSProperties } from "react";
import type { JobDetail, TailorResult } from "../types";
import { colors, font } from "../theme";
import { saveDescription, analyzeJob, tailorJob, exportUrl } from "../api/jobs";

interface Props {
  job: JobDetail;
  onJobUpdate: (job: JobDetail) => void;
  onViewChange: (view: string) => void;
}

type Step = 1 | 2 | 3 | 4;

function currentStep(job: JobDetail): Step {
  if (job.status === "exported" || job.status === "tailored") return 4;
  if (job.status === "analyzed") return 3;
  if (job.description) return 2;
  return 1;
}

function statusColor(status: string): string {
  if (status === "exported" || status === "tailored") return colors.accent;
  if (status === "analyzed") return "#d29922";
  return colors.textMuted;
}

export function JobDetailPanel({ job, onJobUpdate, onViewChange }: Props) {
  const [descInput, setDescInput] = useState(job.description || "");
  const [working, setWorking] = useState(false);
  const [workingLabel, setWorkingLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const step = currentStep(job);

  async function handleSaveDesc() {
    if (!descInput.trim()) return;
    setWorking(true); setError(null); setWorkingLabel("Saving…");
    try {
      const updated = await saveDescription(job.job_id, descInput);
      onJobUpdate(updated);
    } catch (e) { setError(e instanceof Error ? e.message : "Failed"); }
    finally { setWorking(false); setWorkingLabel(""); }
  }

  async function handleAnalyze() {
    setWorking(true); setError(null); setWorkingLabel("Analyzing job description… (~60s)");
    try {
      const updated = await analyzeJob(job.job_id);
      onJobUpdate(updated);
    } catch (e) { setError(e instanceof Error ? e.message : "Analysis failed"); }
    finally { setWorking(false); setWorkingLabel(""); }
  }

  async function handleTailor() {
    setWorking(true); setError(null); setWorkingLabel("Tailoring resume… this may take 1–2 minutes");
    try {
      const result: TailorResult = await tailorJob(job.job_id);
      onJobUpdate({ ...job, ...result } as JobDetail);
    } catch (e) { setError(e instanceof Error ? e.message : "Tailoring failed"); }
    finally { setWorking(false); setWorkingLabel(""); }
  }

  const steps = ["Paste JD", "Analyze", "Tailor", "Export"];

  return (
    <div style={s.panel}>
      {/* Job card */}
      <div style={s.card}>
        <div style={s.cardTop}>
          <div>
            <h2 style={s.jobTitle}>{job.title}</h2>
            <span style={s.company}>{job.company}</span>
          </div>
          <div style={s.cardMeta}>
            <span style={{ ...s.statusBadge, color: statusColor(job.status) }}>[{job.status}]</span>
            {job.ats_score !== null && (
              <span style={{ ...s.atsBadge, color: job.ats_score >= 70 ? colors.accent : job.ats_score >= 50 ? "#d29922" : colors.error }}>
                ATS: {Math.round(job.ats_score)}%
              </span>
            )}
          </div>
        </div>

        {(job.matched_skills.length > 0 || job.missing_skills.length > 0) && (
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
      </div>

      {/* Stepper */}
      <div style={s.stepper}>
        {steps.map((label, i) => {
          const n = (i + 1) as Step;
          const active = n === step;
          const done = n < step;
          return (
            <div key={label} style={s.stepItem}>
              <span style={{
                ...s.stepNum,
                background: done ? colors.accent : active ? colors.accentDim : "transparent",
                color: done || active ? colors.accent : colors.textMuted,
                border: `1px solid ${done || active ? colors.accent : colors.primary}`,
              }}>
                {done ? "✓" : n}
              </span>
              <span style={{ ...s.stepLabel, color: active ? colors.text : colors.textMuted }}>{label}</span>
              {i < steps.length - 1 && <span style={s.stepArrow}>→</span>}
            </div>
          );
        })}
      </div>

      {/* Active step content */}
      <div style={s.stepContent}>
        {error && <p style={s.error}>{error}</p>}
        {working && <p style={s.working}>{workingLabel}</p>}

        {!working && step === 1 && (
          <>
            <p style={s.hint}>Paste the full job description below.</p>
            <textarea
              style={s.textarea}
              value={descInput}
              onChange={e => setDescInput(e.target.value)}
              rows={10}
              placeholder="Paste job description here…"
            />
            <button style={s.actionBtn} onClick={handleSaveDesc} disabled={!descInput.trim()}>
              Save Description
            </button>
          </>
        )}

        {!working && step === 2 && (
          <>
            <p style={s.hint}>Job description saved. Run analysis to extract required skills and score your resume.</p>
            <textarea
              style={{ ...s.textarea, opacity: 0.7 }}
              value={descInput || job.description}
              onChange={e => setDescInput(e.target.value)}
              rows={6}
            />
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <button style={s.actionBtn} onClick={handleSaveDesc}>Update Description</button>
              <button style={{ ...s.actionBtn, background: colors.accent, color: colors.background }} onClick={handleAnalyze}>
                Analyze Job
              </button>
            </div>
          </>
        )}

        {!working && step === 3 && (
          <>
            <p style={s.hint}>Analysis complete. Tailor your resume to emphasize matched skills.</p>
            <button style={{ ...s.actionBtn, background: colors.accent, color: colors.background }} onClick={handleTailor}>
              Tailor Resume
            </button>
          </>
        )}

        {!working && step === 4 && (
          <>
            <p style={s.hint}>Tailoring complete! Download your tailored resume below.</p>
            <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
              <a href={exportUrl(job.job_id, "pdf")} style={s.downloadBtn} download>
                Download PDF
              </a>
              <a href={exportUrl(job.job_id, "tex")} style={{ ...s.downloadBtn, background: "transparent", color: colors.accent, border: `1px solid ${colors.accent}` }} download>
                Download LaTeX
              </a>
              <a href={exportUrl(job.job_id, "docx")} style={{ ...s.downloadBtn, background: "transparent", color: colors.accent, border: `1px solid ${colors.accent}` }} download>
                Download DOCX
              </a>
              <button style={s.actionBtn} onClick={handleTailor}>Re-tailor</button>
            </div>
          </>
        )}
      </div>

      <p style={s.chatLink}>
        <button style={s.chatLinkBtn} onClick={() => onViewChange("chat")}>
          Continue in Chat →
        </button>
      </p>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  panel: { padding: "1.5rem", display: "flex", flexDirection: "column", gap: "1.25rem", maxWidth: "72ch" },
  card: { border: `1px solid ${colors.primary}`, padding: "1rem", background: colors.surface },
  cardTop: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "0.75rem" },
  jobTitle: { margin: "0 0 0.25rem", color: colors.accent, fontSize: font.size.xl, fontWeight: 700 },
  company: { color: colors.textMuted, fontSize: font.size.base },
  cardMeta: { display: "flex", flexDirection: "column", alignItems: "flex-end", gap: "0.25rem" },
  statusBadge: { fontSize: font.size.sm, fontWeight: 600 },
  atsBadge: { fontSize: font.size.lg, fontWeight: 700 },
  skillsRow: { display: "flex", flexDirection: "column", gap: "0.5rem" },
  skillGroup: { display: "flex", alignItems: "flex-start", gap: "0.5rem" },
  skillGroupLabel: { color: colors.textMuted, fontSize: font.size.sm, flexShrink: 0, paddingTop: "0.125rem" },
  chips: { display: "flex", flexWrap: "wrap", gap: "0.375rem" },
  chip: { fontSize: "0.7rem", border: "1px solid", padding: "0.1rem 0.375rem" },
  stepper: { display: "flex", alignItems: "center", gap: "0.375rem", flexWrap: "wrap" },
  stepItem: { display: "flex", alignItems: "center", gap: "0.375rem" },
  stepNum: { width: "1.5rem", height: "1.5rem", display: "flex", alignItems: "center", justifyContent: "center", fontSize: font.size.sm, fontWeight: 700 },
  stepLabel: { fontSize: font.size.sm },
  stepArrow: { color: colors.textMuted, fontSize: font.size.sm },
  stepContent: { display: "flex", flexDirection: "column", gap: "0.75rem" },
  hint: { margin: 0, color: colors.textMuted, fontSize: font.size.sm },
  error: { margin: 0, color: colors.error, fontSize: font.size.sm },
  working: { margin: 0, color: colors.accent, fontSize: font.size.sm, fontStyle: "italic" },
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
  downloadBtn: {
    background: colors.accent, color: colors.background, fontWeight: 700,
    fontSize: font.size.sm, padding: "0.375rem 0.75rem", textDecoration: "none",
    fontFamily: "inherit", display: "inline-block",
  },
  chatLink: { margin: 0 },
  chatLinkBtn: {
    background: "transparent", border: "none", color: colors.accent,
    fontSize: font.size.sm, cursor: "pointer", fontFamily: "inherit", padding: 0,
  },
};
