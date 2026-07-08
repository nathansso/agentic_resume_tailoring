import { useState, type CSSProperties } from "react";
import { colors, font } from "../theme";
import { parseSections, parseBulletGroups, moveSection, moveBullet } from "../lib/texStructure";

interface Props {
  tex: string;
  onChange: (tex: string) => void;
}

/** Section and bullet reordering for the .tex editor (issue #71). All moves are
 *  text-block operations on the buffer via the %% ART-SECTION markers. */
export function ReorderPanel({ tex, onChange }: Props) {
  const [open, setOpen] = useState(false);

  const sections = parseSections(tex).filter(sec => sec.key !== "header");
  const groups = parseBulletGroups(tex);

  if (sections.length === 0) {
    return (
      <p style={s.unavailable}>
        Reordering unavailable — the “%% ART-SECTION:” markers were removed from the
        source. Editing, preview, and export still work.
      </p>
    );
  }

  function nudgeSection(key: string, dir: -1 | 1) {
    const next = moveSection(tex, key, dir);
    if (next !== null) onChange(next);
  }

  function nudgeBullet(line: number, dir: -1 | 1) {
    const next = moveBullet(tex, line, dir);
    if (next !== null) onChange(next);
  }

  return (
    <div style={s.wrap}>
      <button style={s.toggle} onClick={() => setOpen(o => !o)}>
        Reorder sections & bullets {open ? "▲" : "▼"}
      </button>
      {open && (
        <div style={s.body}>
          <div style={s.group}>
            <span style={s.groupLabel}>Sections</span>
            {sections.map((sec, i) => (
              <div key={sec.key} style={s.row}>
                <span style={s.rowText}>{sec.key.charAt(0).toUpperCase() + sec.key.slice(1)}</span>
                <span style={s.arrows}>
                  <button style={s.arrowBtn} disabled={i === 0} onClick={() => nudgeSection(sec.key, -1)} title="Move up">↑</button>
                  <button style={s.arrowBtn} disabled={i === sections.length - 1} onClick={() => nudgeSection(sec.key, 1)} title="Move down">↓</button>
                </span>
              </div>
            ))}
          </div>

          {groups.map((g, gi) => (
            <div key={`${g.label}-${gi}`} style={s.group}>
              <span style={s.groupLabel}>{g.label}</span>
              {g.bullets.map((b, bi) => (
                <div key={b.line} style={s.row}>
                  <span style={s.rowText} title={b.text}>
                    {b.text.length > 60 ? b.text.slice(0, 60) + "…" : b.text}
                  </span>
                  <span style={s.arrows}>
                    <button style={s.arrowBtn} disabled={bi === 0} onClick={() => nudgeBullet(b.line, -1)} title="Move up">↑</button>
                    <button style={s.arrowBtn} disabled={bi === g.bullets.length - 1} onClick={() => nudgeBullet(b.line, 1)} title="Move down">↓</button>
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  wrap: { display: "flex", flexDirection: "column", gap: "0.5rem" },
  toggle: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.textMuted, fontSize: font.size.sm, cursor: "pointer",
    fontFamily: "inherit", padding: "0.25rem 0.5rem", borderRadius: 0, alignSelf: "flex-start",
  },
  unavailable: { margin: 0, color: colors.textMuted, fontSize: "0.7rem", fontStyle: "italic" },
  body: {
    display: "flex", flexDirection: "column", gap: "0.75rem",
    border: `1px solid ${colors.primary}`, padding: "0.625rem 0.75rem",
    background: colors.surface, maxHeight: "18rem", overflowY: "auto",
  },
  group: { display: "flex", flexDirection: "column", gap: "0.25rem" },
  groupLabel: { color: colors.accent, fontSize: font.size.sm, fontWeight: 700 },
  row: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: "0.5rem" },
  rowText: {
    color: colors.text, fontSize: "0.7rem", overflow: "hidden",
    textOverflow: "ellipsis", whiteSpace: "nowrap", minWidth: 0,
  },
  arrows: { display: "flex", gap: "0.25rem", flexShrink: 0 },
  arrowBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: "0.7rem", cursor: "pointer",
    fontFamily: "inherit", padding: "0 0.375rem", borderRadius: 0, lineHeight: 1.4,
  },
};
