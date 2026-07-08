import { useEffect, useState, type CSSProperties } from "react";
import type { JobDetail } from "../types";
import { colors, font } from "../theme";
import { ScoreBreakdownPanel } from "./ScoreBreakdownPanel";

/** Collapsible insights card at the top of the job chat column: matched/missing
 *  skills, score breakdowns, and what the last tailoring run changed. */
export function JobInsights({ job }: { job: JobDetail }) {
  const tailored = job.status === "tailored" || job.status === "exported";
  const [open, setOpen] = useState(!tailored);

  // The skills gap is the actionable info pre-tailoring; once a resume exists
  // it becomes the focus, so the card folds away.
  useEffect(() => {
    if (tailored) setOpen(false);
  }, [tailored]);

  const hasSkills = job.matched_skills.length > 0 || job.missing_skills.length > 0;
  const hasScores =
    (job.score_breakdown && Object.keys(job.score_breakdown).length > 0) ||
    (job.tailored_score_breakdown && Object.keys(job.tailored_score_breakdown).length > 0);
  if (!hasSkills && !hasScores && !job.explainability) return null;

  return (
    <div style={s.card}>
      <button style={s.toggle} onClick={() => setOpen(o => !o)}>
        Job insights {open ? "▲" : "▼"}
      </button>

      {open && (
        <div style={s.body}>
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

          {job.explainability && (
            <div style={s.changes}>
              <span style={s.changesLabel}>Changes made by tailoring</span>
              {job.explainability.emphasized.length > 0 && (
                <p style={s.changesRow}>
                  <span style={s.changesKey}>Emphasized:</span> {job.explainability.emphasized.join(", ")}
                </p>
              )}
              {job.explainability.inferred.length > 0 && (
                <p style={s.changesRow}>
                  <span style={s.changesKey}>Inferred:</span> {job.explainability.inferred.join(", ")}
                </p>
              )}
              {job.explainability.missing.length > 0 && (
                <p style={s.changesRow}>
                  <span style={s.changesKey}>Still missing:</span> {job.explainability.missing.join(", ")}
                </p>
              )}
            </div>
          )}

          {job.score_breakdown && Object.keys(job.score_breakdown).length > 0 && (
            <ScoreBreakdownPanel bd={job.score_breakdown} />
          )}
          {job.tailored_score_breakdown && Object.keys(job.tailored_score_breakdown).length > 0 && (
            <ScoreBreakdownPanel bd={job.tailored_score_breakdown} title="Tailored Score Breakdown" />
          )}
        </div>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  card: {
    borderBottom: `1px solid ${colors.primary}`,
    padding: "0.5rem 0.75rem",
    flexShrink: 0,
    maxHeight: "45%",
    overflowY: "auto",
    background: colors.surface,
  },
  toggle: {
    background: "transparent", border: "none", color: colors.textMuted,
    fontSize: font.size.sm, cursor: "pointer", fontFamily: "inherit",
    padding: 0, fontWeight: 700, letterSpacing: "0.05em",
  },
  body: { display: "flex", flexDirection: "column", gap: "0.5rem", marginTop: "0.5rem" },
  skillGroup: { display: "flex", alignItems: "flex-start", gap: "0.5rem" },
  skillGroupLabel: { color: colors.textMuted, fontSize: font.size.sm, flexShrink: 0, paddingTop: "0.125rem" },
  chips: { display: "flex", flexWrap: "wrap", gap: "0.375rem" },
  chip: { fontSize: "0.7rem", border: "1px solid", padding: "0.1rem 0.375rem" },
  changes: { display: "flex", flexDirection: "column", gap: "0.25rem" },
  changesLabel: { color: colors.textMuted, fontSize: font.size.sm, fontWeight: 700 },
  changesRow: { margin: 0, color: colors.text, fontSize: font.size.sm, lineHeight: 1.5 },
  changesKey: { color: colors.textMuted },
};
