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

/** Swap the section `key` with its neighbor in `dir`. Returns the new buffer,
 *  or null when the move is impossible (edge, header, or markers missing). */
export function moveSection(tex: string, key: string, dir: -1 | 1): string | null {
  const sections = parseSections(tex);
  const a = sections.findIndex(sec => sec.key === key);
  if (a < 0 || sections[a].key === "header") return null;
  const b = a + dir;
  if (b < 0 || b >= sections.length || sections[b].key === "header") return null;

  const lines = tex.split("\n");
  const first = dir === 1 ? sections[a] : sections[b];
  const second = dir === 1 ? sections[b] : sections[a];
  const rebuilt = [
    ...lines.slice(0, first.startLine),
    ...lines.slice(second.startLine, second.endLine),
    ...lines.slice(first.endLine, second.startLine),
    ...lines.slice(first.startLine, first.endLine),
    ...lines.slice(second.endLine),
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

/** Swap the \resumeItem at `line` with the adjacent bullet in `dir`, staying
 *  inside the same \resumeItemListStart/End container. Null when impossible. */
export function moveBullet(tex: string, line: number, dir: -1 | 1): string | null {
  const lines = tex.split("\n");
  if (line < 0 || line >= lines.length || !ITEM.test(lines[line])) return null;

  let j = line + dir;
  while (j >= 0 && j < lines.length) {
    const ln = lines[j];
    if (ITEM.test(ln)) break;
    // Stop at container/section boundaries — never move a bullet between lists
    if (LIST_START.test(ln) || LIST_END.test(ln) || MARKER.test(ln) || DOC_END.test(ln)) return null;
    j += dir;
  }
  if (j < 0 || j >= lines.length || !ITEM.test(lines[j])) return null;

  [lines[line], lines[j]] = [lines[j], lines[line]];
  return lines.join("\n");
}
