import { useEffect, useState, type CSSProperties } from "react";
import { colors, font } from "../theme";

// Inline styles can't declare @keyframes, so inject the sweep animation once.
let keyframesInjected = false;
function injectKeyframes() {
  if (keyframesInjected) return;
  keyframesInjected = true;
  const style = document.createElement("style");
  style.textContent =
    "@keyframes art-progress-sweep { 0% { left: -40%; } 100% { left: 100%; } }";
  document.head.appendChild(style);
}

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

interface Props {
  label: string;
  /** Show a running elapsed-time counter (for tasks started by the user just now). */
  showElapsed?: boolean;
}

/** Indeterminate progress bar for long-running server calls (ingest, analyze, tailor). */
export function ProgressBar({ label, showElapsed = true }: Props) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    injectKeyframes();
    if (!showElapsed) return;
    const id = setInterval(() => setElapsed(e => e + 1), 1000);
    return () => clearInterval(id);
  }, [showElapsed]);

  return (
    <div style={s.wrap} role="status" aria-live="polite">
      <div style={s.labelRow}>
        <span style={s.label}>{label}</span>
        {showElapsed && <span style={s.elapsed}>{formatElapsed(elapsed)}</span>}
      </div>
      <div style={s.track}>
        <div style={s.bar} />
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { display: "flex", flexDirection: "column", gap: "0.375rem", width: "100%" },
  labelRow: { display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "0.75rem" },
  label: { color: colors.accent, fontSize: font.size.sm, fontStyle: "italic" },
  elapsed: { color: colors.textMuted, fontSize: font.size.sm, fontVariantNumeric: "tabular-nums" },
  track: {
    position: "relative",
    height: "0.25rem",
    background: colors.boost,
    border: `1px solid ${colors.primary}`,
    overflow: "hidden",
  },
  bar: {
    position: "absolute",
    top: 0,
    bottom: 0,
    width: "40%",
    background: colors.accent,
    animation: "art-progress-sweep 1.4s ease-in-out infinite",
  },
};
