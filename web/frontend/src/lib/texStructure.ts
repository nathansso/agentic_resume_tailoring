/**
 * Structure helpers for the resume .tex editor (issue #71).
 *
 * The backend prefixes every section block with a "%% ART-SECTION: <key>"
 * comment marker, so section and bullet reordering are pure text-block moves
 * on the editor buffer — they keep working after the user hand-edits the
 * source, and degrade gracefully (return null / empty) when markers or
 * \resumeItem lines are edited away.
 */

const MARKER = /^%% ART-SECTION: ([A-Za-z0-9_-]+)[ \t]*$/;
const ITEM = /^\s*\\resumeItem\{/;
const LIST_START = /^\s*\\resumeItemListStart\b/;
const LIST_END = /^\s*\\resumeItemListEnd\b/;
const DOC_END = /^\s*\\end\{document\}/;

export interface TexSection {
  key: string;
  /** Line index of the marker */
  startLine: number;
  /** Exclusive end line */
  endLine: number;
}

export interface BulletInfo {
  /** Absolute line index of the \resumeItem line in the buffer */
  line: number;
  /** Cleaned display text (LaTeX commands stripped) */
  text: string;
}

export interface BulletGroup {
  sectionKey: string;
  /** Heading the bullet list belongs to (experience title / project name) */
  label: string;
  bullets: BulletInfo[];
}

export function parseSections(tex: string): TexSection[] {
  const lines = tex.split("\n");
  const marks: { key: string; line: number }[] = [];
  lines.forEach((ln, i) => {
    const m = MARKER.exec(ln);
    if (m) marks.push({ key: m[1], line: i });
  });
  if (marks.length === 0) return [];

  let lastEnd = lines.length;
  for (let i = marks[marks.length - 1].line; i < lines.length; i++) {
    if (DOC_END.test(lines[i])) { lastEnd = i; break; }
  }

  return marks.map((m, i) => ({
    key: m.key,
    startLine: m.line,
    endLine: i + 1 < marks.length ? marks[i + 1].line : lastEnd,
  }));
}

/** Movable (non-header) sections in document order. */
export function movableSections(tex: string): TexSection[] {
  return parseSections(tex).filter(sec => sec.key !== "header");
}

/** Move section `key` so it becomes the `targetIndex`-th movable section
 *  (document order, header excluded — it stays pinned). Returns the new
 *  buffer, the same buffer for a no-op move, or null when the move is
 *  impossible (unknown key, index out of bounds, or a hand-edited buffer
 *  whose section blocks no longer tile a contiguous region). */
export function moveSectionTo(tex: string, key: string, targetIndex: number): string | null {
  const movable = movableSections(tex);
  const from = movable.findIndex(sec => sec.key === key);
  if (from < 0 || targetIndex < 0 || targetIndex >= movable.length) return null;
  if (targetIndex === from) return tex;

  // The reorder splices whole blocks inside [first.start, last.end); that is
  // only safe when the movable blocks tile it exactly (no header in between).
  for (let i = 1; i < movable.length; i++) {
    if (movable[i].startLine !== movable[i - 1].endLine) return null;
  }

  const order = [...movable];
  const [moved] = order.splice(from, 1);
  order.splice(targetIndex, 0, moved);

  const lines = tex.split("\n");
  const rebuilt = [
    ...lines.slice(0, movable[0].startLine),
    ...order.flatMap(sec => lines.slice(sec.startLine, sec.endLine)),
    ...lines.slice(movable[movable.length - 1].endLine),
  ];
  return rebuilt.join("\n");
}

function displayText(line: string): string {
  let t = line.trim().replace(/^\\resumeItem\{/, "");
  if (t.endsWith("}")) t = t.slice(0, -1);
  t = t.replace(/\\href\{[^}]*\}\{([^}]*)\}/g, "$1");
  t = t.replace(/\\[a-zA-Z]+\*?\s*/g, "").replace(/[{}]/g, "");
  return t.trim();
}

function headingLabel(line: string): string | null {
  const sub = /\\resumeSubheading\{([^}]*)\}/.exec(line);
  if (sub) return sub[1];
  if (line.includes("\\resumeProjectHeading")) {
    const name = /\\textbf\{([^}]*)\}/.exec(line);
    return name ? name[1] : "Project";
  }
  return null;
}

/** All bullet lists in the buffer, grouped under their headings. */
export function parseBulletGroups(tex: string): BulletGroup[] {
  const lines = tex.split("\n");
  const groups: BulletGroup[] = [];
  let sectionKey = "";
  let label = "";
  let current: BulletGroup | null = null;

  lines.forEach((ln, i) => {
    const mark = MARKER.exec(ln);
    if (mark) { sectionKey = mark[1]; return; }
    const heading = headingLabel(ln);
    if (heading) { label = heading; return; }
    if (LIST_START.test(ln)) {
      current = { sectionKey, label: label || sectionKey, bullets: [] };
      return;
    }
    if (LIST_END.test(ln)) {
      if (current && current.bullets.length > 0) groups.push(current);
      current = null;
      return;
    }
    if (current && ITEM.test(ln)) {
      current.bullets.push({ line: i, text: displayText(ln) });
    }
  });
  return groups;
}

/** Reorder within the `groupIndex`-th bullet group from parseBulletGroups:
 *  the bullet at `fromIdx` moves to `toIdx` (both group-relative). Bullets are
 *  single `\resumeItem{...}` lines by construction, so the reorder writes the
 *  lines back into the same absolute slots — it cannot cross list or section
 *  boundaries. Null on any index mismatch. */
export function moveBulletTo(tex: string, groupIndex: number, fromIdx: number, toIdx: number): string | null {
  const groups = parseBulletGroups(tex);
  const group = groups[groupIndex];
  if (!group) return null;
  const n = group.bullets.length;
  if (fromIdx < 0 || fromIdx >= n || toIdx < 0 || toIdx >= n) return null;
  if (fromIdx === toIdx) return tex;

  const lines = tex.split("\n");
  const slots = group.bullets.map(b => b.line);
  const contents = slots.map(l => lines[l]);
  const [moved] = contents.splice(fromIdx, 1);
  contents.splice(toIdx, 0, moved);
  slots.forEach((slot, i) => {
    lines[slot] = contents[i];
  });
  return lines.join("\n");
}
