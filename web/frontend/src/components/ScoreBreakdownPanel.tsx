import { useState, type CSSProperties } from "react";
import type { ScoreBreakdown } from "../types";
import { colors, font } from "../theme";

export function scoreColor(score: number): string {
  if (score >= 50) return colors.accent;
  if (score >= 30) return "#d29922";
  return colors.error;
}

type ComponentKey = "skill_coverage" | "keyword_coverage" | "section_presence" | "role_level";

export function ScoreBreakdownPanel({ bd, title = "Score Breakdown" }: { bd: ScoreBreakdown; title?: string }) {
  const [open, setOpen] = useState(false);
  const components: { key: ComponentKey; label: string }[] = [
    { key: "skill_coverage",  label: "Skill Coverage" },
    { key: "keyword_coverage", label: "Keyword Coverage" },
    { key: "section_presence", label: "Profile Completeness" },
    { key: "role_level",       label: "Seniority Match" },
  ];
  const hasData = components.some(c => bd[c.key] !== undefined);
  if (!hasData) return null;

  return (
    <div style={sb.container}>
      <button style={sb.toggle} onClick={() => setOpen(o => !o)}>
        {title} {open ? "▲" : "▼"}
      </button>
      {bd.delta !== undefined && (
        <span style={{ ...sb.detail, marginLeft: "0.5rem", color: bd.delta >= 0 ? colors.accent : colors.error }}>
          {bd.delta >= 0 ? "+" : ""}{bd.delta} vs. baseline {bd.baseline_composite}
        </span>
      )}
      {open && (
        <div style={sb.rows}>
          {components.map(({ key, label }) => {
            const comp = bd[key];
            if (!comp) return null;
            const score = Math.round(comp.score ?? 0);
            const color = scoreColor(score);
            return (
              <div key={key} style={sb.row}>
                <span style={sb.rowLabel}>{label}</span>
                <span style={{ ...sb.rowScore, color }}>{score}%</span>
                {key === "keyword_coverage" && bd.keyword_coverage?.missing_keywords && bd.keyword_coverage.missing_keywords.length > 0 && (
                  <span style={sb.detail}>
                    missing: {bd.keyword_coverage.missing_keywords.slice(0, 8).join(", ")}
                    {bd.keyword_coverage.missing_keywords.length > 8 ? "…" : ""}
                  </span>
                )}
                {key === "role_level" && bd.role_level?.jd_level && (
                  <span style={sb.detail}>
                    JD: {bd.role_level.jd_level} / you: {bd.role_level.resume_level ?? "mid"}
                  </span>
                )}
                {key === "section_presence" && bd.section_presence?.missing && bd.section_presence.missing.length > 0 && (
                  <span style={sb.detail}>
                    missing: {bd.section_presence.missing.join(", ")}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const sb: Record<string, CSSProperties> = {
  container: { marginTop: "0.75rem" },
  toggle: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.textMuted, fontSize: "0.7rem", cursor: "pointer",
    fontFamily: "inherit", padding: "0.2rem 0.5rem", borderRadius: 0,
  },
  rows: { display: "flex", flexDirection: "column", gap: "0.375rem", marginTop: "0.5rem" },
  row: { display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" },
  rowLabel: { color: colors.textMuted, fontSize: font.size.sm, minWidth: "10rem" },
  rowScore: { fontSize: font.size.sm, fontWeight: 700, minWidth: "3rem" },
  detail: { color: colors.textMuted, fontSize: "0.7rem", fontStyle: "italic" },
};
