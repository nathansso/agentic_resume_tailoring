import type { JobDetail, ScoreBreakdown } from "../types";

/** Job insights rendered as assistant chat bubbles (replaces the old
 *  JobInsights dashboard card): skills match, what the last tailoring run
 *  changed, and the score breakdown. Derived from live job state, so the
 *  briefing updates as the pipeline progresses. */
export function jobInsightMessages(job: JobDetail): string[] {
  const msgs: string[] = [];

  if (job.matched_skills.length > 0 || job.missing_skills.length > 0) {
    const parts = ["Skills match for this job:"];
    if (job.matched_skills.length > 0) {
      parts.push(`  ✓ Matched: ${job.matched_skills.join(", ")}`);
    }
    if (job.missing_skills.length > 0) {
      parts.push(`  ✗ Missing: ${job.missing_skills.join(", ")}`);
    }
    msgs.push(parts.join("\n"));
  }

  if (job.explainability) {
    const e = job.explainability;
    const parts = ["Changes made by the last tailoring run:"];
    if (e.emphasized.length > 0) parts.push(`  • Emphasized: ${e.emphasized.join(", ")}`);
    if (e.inferred.length > 0) parts.push(`  • Inferred from your work: ${e.inferred.join(", ")}`);
    if (e.missing.length > 0) parts.push(`  • Still missing: ${e.missing.join(", ")}`);
    if (parts.length > 1) msgs.push(parts.join("\n"));
  }

  const tailoredBd = formatBreakdown(job.tailored_score_breakdown, "Tailored score breakdown");
  const baselineBd = formatBreakdown(job.score_breakdown, "Score breakdown");
  // One score message: the tailored breakdown (with its delta line) once it
  // exists, otherwise the baseline from analysis.
  if (tailoredBd) msgs.push(tailoredBd);
  else if (baselineBd) msgs.push(baselineBd);

  return msgs;
}

const COMPONENTS: { key: keyof ScoreBreakdown; label: string }[] = [
  { key: "skill_coverage", label: "Skill coverage" },
  { key: "keyword_coverage", label: "Keyword coverage" },
  { key: "section_presence", label: "Profile completeness" },
  { key: "role_level", label: "Seniority match" },
];

function formatBreakdown(bd: ScoreBreakdown | undefined | null, title: string): string | null {
  if (!bd) return null;
  const rows: string[] = [];
  for (const { key, label } of COMPONENTS) {
    const comp = bd[key];
    if (!comp || typeof comp !== "object" || !("score" in comp)) continue;
    let row = `  ${label}: ${Math.round((comp as { score: number }).score)}%`;
    if (key === "keyword_coverage") {
      const missing = bd.keyword_coverage?.missing_keywords;
      if (missing && missing.length > 0) {
        row += ` (missing: ${missing.slice(0, 8).join(", ")}${missing.length > 8 ? "…" : ""})`;
      }
    }
    if (key === "role_level" && bd.role_level?.jd_level) {
      row += ` (JD: ${bd.role_level.jd_level} / you: ${bd.role_level.resume_level ?? "mid"})`;
    }
    rows.push(row);
  }
  if (rows.length === 0) return null;
  let heading = `${title}:`;
  if (bd.delta !== undefined) {
    heading = `${title} (${bd.delta >= 0 ? "+" : ""}${bd.delta} vs. baseline ${bd.baseline_composite}):`;
  }
  return [heading, ...rows].join("\n");
}
