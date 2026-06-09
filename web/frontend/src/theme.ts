export const colors = {
  background: "#0d1117",
  surface:    "#161b22",
  boost:      "#1c2128",
  primary:    "#30363d",
  accent:     "#3fb950",
  accentDim:  "#1a3a22",  // accent at ~15% opacity blended over surface
  text:       "#e6edf3",
  textMuted:  "#a0aeb9",  // brighter than TUI $text-muted for better web contrast
  error:      "#f85149",
} as const;

export const font = {
  mono: "'JetBrains Mono', 'Fira Mono', 'Cascadia Code', monospace",
  size: {
    sm:   "0.8125rem",
    base: "0.9375rem",
    lg:   "1rem",
    xl:   "1.25rem",
    xxl:  "1.75rem",
  },
} as const;
