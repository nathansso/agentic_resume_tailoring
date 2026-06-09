import type { CSSProperties } from "react";
import { colors, font } from "../theme";

interface Props {
  onViewChange: (view: string) => void;
}

export function WelcomePanel({ onViewChange }: Props) {
  const ctas = [
    { label: "Ingest Resume", view: "ingest", desc: "Upload a PDF, DOCX, or Markdown resume" },
    { label: "Connect GitHub", view: "ingest", desc: "Pull in repos and extract skills" },
    { label: "Browse Data", view: "data", desc: "View your skills, experiences, and projects" },
    { label: "Open Chat", view: "chat", desc: "Start chatting without a job selected" },
  ];

  return (
    <div style={s.panel}>
      <div style={s.brand}>ART</div>
      <p style={s.tagline}>Agentic Resume Tailoring</p>
      <p style={s.sub}>
        Create a job in the sidebar to get started, or use the actions below to build your profile.
      </p>
      <div style={s.ctas}>
        {ctas.map(cta => (
          <button key={cta.label} style={s.ctaBtn} onClick={() => onViewChange(cta.view)}>
            <span style={s.ctaLabel}>{cta.label}</span>
            <span style={s.ctaDesc}>{cta.desc}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  panel: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    padding: "2rem",
    gap: "0.75rem",
  },
  brand: {
    fontSize: "3rem",
    fontWeight: 700,
    color: colors.accent,
    letterSpacing: "0.15em",
  },
  tagline: {
    margin: 0,
    color: colors.textMuted,
    fontSize: font.size.base,
    letterSpacing: "0.05em",
  },
  sub: {
    margin: "0.5rem 0 1rem",
    color: colors.textMuted,
    fontSize: font.size.sm,
    textAlign: "center",
    maxWidth: "42ch",
  },
  ctas: {
    display: "flex",
    gap: "0.75rem",
    flexWrap: "wrap",
    justifyContent: "center",
  },
  ctaBtn: {
    display: "flex",
    flexDirection: "column",
    alignItems: "flex-start",
    gap: "0.25rem",
    background: colors.surface,
    border: `1px solid ${colors.primary}`,
    padding: "0.75rem 1rem",
    cursor: "pointer",
    fontFamily: "inherit",
    borderRadius: 0,
    minWidth: "16ch",
    transition: "border-color 0.1s",
  },
  ctaLabel: {
    color: colors.accent,
    fontWeight: 700,
    fontSize: font.size.sm,
  },
  ctaDesc: {
    color: colors.textMuted,
    fontSize: "0.75rem",
  },
};
