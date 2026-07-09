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
  /** Bottom of the rendered heading line — the full-width grab band. */
  headingBottom: number;
  /** Bottom of the last rendered text line in the band — the band minus its
   *  trailing whitespace (the last section's band runs to the page bottom). */
  contentBottom: number;
  /** Tex line index of the \section{...} heading (double-click jump target). */
  texLine: number;
}

export interface BulletRegion {
  kind: "bullet";
  groupIndex: number;
  bulletIndex: number;
  top: number;
  height: number;
  /** Tex line index of the \resumeItem line (double-click jump target). */
  texLine: number;
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
  const found: { key: string; index: number; lineIdx: number; texLine: number }[] = [];
  let cursor = 0;
  movable.forEach((sec, index) => {
    let headingNorm = "";
    let headingTexLine = sec.startLine;
    for (let i = sec.startLine; i < sec.endLine; i++) {
      const m = SECTION_HEADING.exec(texLines[i]);
      if (m) {
        headingNorm = normalizeForMatch(m[1]);
        headingTexLine = i;
        break;
      }
    }
    if (!headingNorm) return;
    for (let li = cursor; li < lines.length; li++) {
      if (normalizeForMatch(lines[li].text) === headingNorm) {
        found.push({ key: sec.key, index, lineIdx: li, texLine: headingTexLine });
        cursor = li + 1;
        return;
      }
    }
  });

  // Section band: heading top → next matched heading's top (or page bottom).
  const sections: SectionRegion[] = found.map((f, i) => {
    const top = lines[f.lineIdx].top;
    const nextTop = i + 1 < found.length ? lines[found[i + 1].lineIdx].top : pageHeight;
    // Trailing whitespace ends at the last text line inside the band (+2px
    // for the heading rule); reorderPatch repacks bands by content height.
    let contentBottom = lines[f.lineIdx].bottom;
    for (let li = f.lineIdx; li < lines.length && lines[li].top < nextTop; li++) {
      contentBottom = Math.max(contentBottom, lines[li].bottom);
    }
    return {
      kind: "section",
      key: f.key,
      index: f.index,
      top,
      height: Math.max(0, nextTop - top),
      headingBottom: lines[f.lineIdx].bottom,
      contentBottom: Math.min(contentBottom + 2, nextTop),
      texLine: f.texLine,
    };
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
    const anchors: { lineIdx: number; texLine: number }[] = [];
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
      anchors.push({ lineIdx: hit, texLine: bullet.line });
      li = hit + 1;
    }
    sectionCursors.set(group.sectionKey, li);
    const bullets: BulletRegion[] = anchors.map((a, i) => {
      const top = lines[a.lineIdx].top;
      const bottom =
        i + 1 < anchors.length
          ? lines[anchors[i + 1].lineIdx].top
          : lines[a.lineIdx].bottom + 4;
      return {
        kind: "bullet",
        groupIndex,
        bulletIndex: i,
        top,
        height: Math.max(0, bottom - top),
        texLine: a.texLine,
      };
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

export interface ReorderSlice {
  /** Top of the pixels to copy, CSS px (pre-reorder position). */
  srcTop: number;
  height: number;
  /** Where those pixels land after the reorder, CSS px. */
  destTop: number;
}

export interface ReorderPatch {
  /** Vertical span the reorder disturbs — cleared to white before drawing. */
  regionTop: number;
  regionBottom: number;
  /** Every band in its new position, top-down (unmoved bands included). */
  slices: ReorderSlice[];
}

/** Where each band's pixels land after moving band `from` to position `to` —
 *  lets the preview re-composite the already-rendered canvas instantly instead
 *  of waiting a full compile round-trip for a pure reorder. Bands tile the
 *  region by construction; each is repacked at its content height while the
 *  original slot's trailing gap stays with the slot (LaTeX inter-section
 *  spacing is positional, e.g. the page-bottom whitespace after the last
 *  section must stay at the bottom, not travel with the moved band). */
export function reorderPatch(
  bands: { top: number; height: number; contentBottom?: number }[],
  from: number,
  to: number,
): ReorderPatch | null {
  if (from === to || from < 0 || to < 0 || from >= bands.length || to >= bands.length) return null;
  const regionTop = bands[0].top;
  const last = bands[bands.length - 1];
  const regionBottom = last.top + last.height;
  const contentH = bands.map(b => Math.max(0, (b.contentBottom ?? b.top + b.height) - b.top));
  const slotGap = bands.map((b, i) => b.height - contentH[i]);
  const order = bands.map((_, i) => i);
  order.splice(to, 0, order.splice(from, 1)[0]);
  let cursor = regionTop;
  const slices: ReorderSlice[] = order.map((src, slot) => {
    const slice = { srcTop: bands[src].top, height: contentH[src], destTop: cursor };
    cursor += contentH[src] + slotGap[slot];
    return slice;
  });
  return { regionTop, regionBottom, slices };
}

/** Tex line a double-click at `y` should jump to (Overleaf-style source sync):
 *  the bullet band under the pointer wins, then the enclosing section's
 *  heading line; null outside any mapped region. */
export function texLineForPointer(y: number, model: OverlayModel): number | null {
  for (const g of model.bulletGroups) {
    for (const b of g.bullets) {
      if (y >= b.top && y < b.top + b.height) return b.texLine;
    }
  }
  for (const sec of model.sections) {
    if (y >= sec.top && y < sec.top + sec.height) return sec.texLine;
  }
  return null;
}
