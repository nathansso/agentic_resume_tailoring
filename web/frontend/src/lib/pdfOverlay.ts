/** Maps the rendered PDF back onto the .tex structure so the preview can be
 *  dragged to reorder sections and bullets (issue #71 follow-up).
 *
 *  Pure module — no pdf.js import. PdfPreview adapts pdf.js text-content
 *  items into plain {str,x,y,height} records (CSS pixels, y = item top,
 *  page-relative, top-down) and this module matches them against the tex
 *  buffer via parseSections/parseBulletGroups. Matching is ordered-cursor and
 *  normalization-based, and degrades gracefully: unmatched sections lose their
 *  drag handle, one unmatched bullet drops its whole group, missing markers
 *  yield an empty model. */

import { movableSections, parseBulletGroups } from "./texStructure";

export interface PdfTextItem {
  str: string;
  x: number;
  /** Top of the glyph box, CSS px, top-down. */
  y: number;
  height: number;
}

export interface PdfTextLine {
  text: string;
  top: number;
  bottom: number;
}

export interface SectionRegion {
  kind: "section";
  key: string;
  /** Index among movable (non-header) sections in document order. */
  index: number;
  top: number;
  height: number;
}

export interface BulletRegion {
  kind: "bullet";
  groupIndex: number;
  bulletIndex: number;
  top: number;
  height: number;
}

export interface OverlayModel {
  sections: SectionRegion[];
  bulletGroups: { groupIndex: number; bullets: BulletRegion[] }[];
}

/** Fold LaTeX-ish or PDF-extracted text to a comparable token: NFKD (splits
 *  ligatures like ﬁ), drop inline math, unescape \% \& \# \$ \_, strip
 *  \commands and braces, lowercase, keep alphanumerics only. */
export function normalizeForMatch(s: string): string {
  let t = s.normalize("NFKD");
  t = t.replace(/\$[^$]*\$/g, " ");
  t = t.replace(/\\([%&#$_])/g, "$1");
  t = t.replace(/\\[a-zA-Z]+\*?/g, " ");
  t = t.replace(/[{}]/g, "");
  return t.toLowerCase().replace(/[^a-z0-9]+/g, "");
}

/** Cluster raw text items into visual lines by y (tolerance in CSS px),
 *  sorted top-down; items within a line join left-to-right. */
export function groupIntoLines(items: PdfTextItem[], tolerance = 2): PdfTextLine[] {
  const sorted = items
    .filter(it => it.str.trim() !== "")
    .sort((a, b) => a.y - b.y || a.x - b.x);
  const lines: { parts: PdfTextItem[]; top: number; bottom: number }[] = [];
  for (const it of sorted) {
    const last = lines[lines.length - 1];
    if (last && Math.abs(it.y - last.top) <= tolerance) {
      last.parts.push(it);
      last.top = Math.min(last.top, it.y);
      last.bottom = Math.max(last.bottom, it.y + it.height);
    } else {
      lines.push({ parts: [it], top: it.y, bottom: it.y + it.height });
    }
  }
  return lines.map(l => ({
    text: l.parts.sort((a, b) => a.x - b.x).map(p => p.str).join(" "),
    top: l.top,
    bottom: l.bottom,
  }));
}

const SECTION_HEADING = /\\section\{([^}]*)\}/;
/** How much of a bullet's normalized text must anchor its rendered line. */
const BULLET_PREFIX_CHARS = 25;

/** Map the tex structure onto rendered page-1 lines. Partial results allowed. */
export function buildOverlayModel(tex: string, lines: PdfTextLine[], pageHeight: number): OverlayModel {
  const empty: OverlayModel = { sections: [], bulletGroups: [] };
  const movable = movableSections(tex);
  if (movable.length === 0 || lines.length === 0) return empty;
  const texLines = tex.split("\n");

  // 1. Sections: match each block's own \section{...} text (headings are
  //    user-customizable — never hardcoded) against whole rendered lines,
  //    walking top-down so duplicate words elsewhere cannot confuse order.
  const found: { key: string; index: number; lineIdx: number }[] = [];
  let cursor = 0;
  movable.forEach((sec, index) => {
    let headingNorm = "";
    for (let i = sec.startLine; i < sec.endLine; i++) {
      const m = SECTION_HEADING.exec(texLines[i]);
      if (m) {
        headingNorm = normalizeForMatch(m[1]);
        break;
      }
    }
    if (!headingNorm) return;
    for (let li = cursor; li < lines.length; li++) {
      if (normalizeForMatch(lines[li].text) === headingNorm) {
        found.push({ key: sec.key, index, lineIdx: li });
        cursor = li + 1;
        return;
      }
    }
  });

  // Section band: heading top → next matched heading's top (or page bottom).
  const sections: SectionRegion[] = found.map((f, i) => {
    const top = lines[f.lineIdx].top;
    const nextTop = i + 1 < found.length ? lines[found[i + 1].lineIdx].top : pageHeight;
    return { kind: "section", key: f.key, index: f.index, top, height: Math.max(0, nextTop - top) };
  });

  // 2. Bullets: within the enclosing section band, continue an ordered line
  //    cursor per group; each bullet anchors to the first line whose
  //    normalized text starts with the bullet's normalized prefix. Wrapped
  //    continuation lines simply don't match the next bullet's prefix and are
  //    absorbed into the current bullet's band.
  const groups = parseBulletGroups(tex);
  const bulletGroups: OverlayModel["bulletGroups"] = [];
  // A section can hold several bullet groups (one per experience/project);
  // the cursor persists across them so later groups match past earlier ones.
  const sectionCursors = new Map<string, number>();
  groups.forEach((group, groupIndex) => {
    const band = sections.find(sec => sec.key === group.sectionKey);
    if (!band) return;
    const bandEnd = band.top + band.height;
    const anchors: { lineIdx: number }[] = [];
    let li = sectionCursors.get(group.sectionKey) ?? lines.findIndex(l => l.top >= band.top);
    if (li < 0) return;
    for (const bullet of group.bullets) {
      const prefix = normalizeForMatch(bullet.text).slice(0, BULLET_PREFIX_CHARS);
      if (!prefix) return; // unmatchable bullet → drop the group
      let hit = -1;
      for (let j = li; j < lines.length && lines[j].top < bandEnd; j++) {
        const lineNorm = normalizeForMatch(lines[j].text);
        // A wrapped bullet's first rendered line can be shorter than the
        // prefix — then the line must itself be a prefix of the bullet.
        const matches =
          lineNorm.length >= prefix.length
            ? lineNorm.startsWith(prefix)
            : lineNorm.length >= 4 && prefix.startsWith(lineNorm);
        if (matches) {
          hit = j;
          break;
        }
      }
      if (hit < 0) return; // one unmatched bullet → drop the whole group
      anchors.push({ lineIdx: hit });
      li = hit + 1;
    }
    sectionCursors.set(group.sectionKey, li);
    const bullets: BulletRegion[] = anchors.map((a, i) => {
      const top = lines[a.lineIdx].top;
      const bottom =
        i + 1 < anchors.length
          ? lines[anchors[i + 1].lineIdx].top
          : lines[a.lineIdx].bottom + 4;
      return { kind: "bullet", groupIndex, bulletIndex: i, top, height: Math.max(0, bottom - top) };
    });
    bulletGroups.push({ groupIndex, bullets });
  });

  return { sections, bulletGroups };
}

/** Insertion slot (0..bands.length) for a pointer at `y`, comparing against
 *  band midpoints. Convert to a final move index with `slotToIndex`. */
export function targetIndexForPointer(y: number, bands: { top: number; height: number }[]): number {
  let slot = 0;
  for (const b of bands) {
    if (y > b.top + b.height / 2) slot++;
  }
  return slot;
}

/** Splice-style target index for moving `from` to insertion `slot`. */
export function slotToIndex(slot: number, from: number): number {
  return slot > from ? slot - 1 : slot;
}
