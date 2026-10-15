/**
 * The evidence map's logic (issue #136), as pure functions of the server payload.
 *
 * Nothing here touches the DOM or randomness. The layout is a function of the
 * data and of three inputs (width, which areas are folded, the search text), so
 * it is the same on every visit and for any order the rows arrive in; nothing is
 * saved and a new item slots in without moving the rest. The component draws what
 * this returns and owns only selection, hover and the editor's drafts.
 *
 * Left column: experiences and education by end date, their projects nested
 * beneath, then standalone projects, then those with no text to link from. Right
 * column: skills grouped by area. An area's place is the weighted median of the
 * evidence rows its skills link to; a skill's place in its area is the weighted
 * median of its own (weight = citing bullets). That is the Eades-Wormald median
 * heuristic for few crossings, and a pure function of the data.
 */
import type {
  EvidenceEdge, EvidenceItem, EvidenceSkill, EvidenceView, JobSkillRow,
} from "../types";

// ── geometry ───────────────────────────────────────────────────────────────────

export const MIN_WIDTH = 688;
const SKILL_COLUMN = 330;
const TOP = 40;
const AREA_HEADER = 26;
const SKILL_GAP = 21;
const CHIP_ROW = 25;
const CHIP_GAP = 5;
const AREA_GAP = 12;
const FIX_GAP = 34;
const ROW_MIN = 50;
const ROW_MAX = 74;
const BOTTOM = 16;
const LEFT_MAX = 345;

/** The fixed area set, in the server's order (`knowledge_graph/areas.py::AREAS`); the tie-break for area order. */
export const AREA_ORDER: readonly string[] = [
  "Languages", "ML & AI", "Data", "Infrastructure & Cloud", "Web & Apps", "Tools", "Practices", "Other",
];

export const areaFoldKey = (area: string): string => `area:${area}`;

/** Circle radius for a skill: area grows with its citing bullets. */
export const skillRadius = (bullets: number): number => 3.2 + 2.3 * Math.sqrt(Math.max(0, bullets));

/** Edge width: 1 + 1.5 * log2(1 + citing bullets). A link with no bullet is the hairline. */
export const edgeWidth = (cites: number): number => 1 + 1.5 * Math.log2(1 + Math.max(0, cites));

/** A horizontal cubic link, the shape d3's `linkHorizontal` draws: `M x0 y0 C xm y0, xm y1, x1 y1`. */
export function linkPath(x0: number, y0: number, x1: number, y1: number): string {
  const xm = (x0 + x1) / 2;
  return `M${r2(x0)},${r2(y0)}C${r2(xm)},${r2(y0)},${r2(xm)},${r2(y1)},${r2(x1)},${r2(y1)}`;
}

/** An SVG arc path of radius `r` about (cx, cy), from angle `a0` to `a1` (radians, 0 = 12 o'clock, clockwise). */
export function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const pt = (a: number): [number, number] => [cx + r * Math.sin(a), cy - r * Math.cos(a)];
  const [x0, y0] = pt(a0);
  const [x1, y1] = pt(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  return `M${r2(x0)},${r2(y0)}A${r2(r)},${r2(r)},0,${large},1,${r2(x1)},${r2(y1)}`;
}

/** One arc per source around a skill, with a small gap between them. One source is a near-full circle. */
export function sourceArcs(cx: number, cy: number, r: number, sources: number): string[] {
  if (sources <= 0) return [];
  const full = 2 * Math.PI;
  if (sources === 1) return [arcPath(cx, cy, r, 0.0001, full - 0.0001)];
  const gap = 0.5;
  const out: string[] = [];
  for (let i = 0; i < sources; i++) {
    out.push(arcPath(cx, cy, r, (i * full) / sources + gap / 2, ((i + 1) * full) / sources - gap / 2));
  }
  return out;
}

/** Text width at a given size, estimated: a layout input must not depend on a font having loaded. */
export function estimateTextWidth(text: string, size = 12): number {
  return Math.round(text.length * size * 0.56 * 10) / 10;
}

/** `text` cut to fit `maxWidth` at `size`, with an ellipsis (SVG text does not clip itself). */
export function fitText(text: string, maxWidth: number, size = 13): string {
  if (estimateTextWidth(text, size) <= maxWidth) return text;
  const fit = Math.max(1, Math.floor(maxWidth / (size * 0.56)) - 1);
  return `${text.slice(0, fit).trimEnd()}…`;
}

function r2(n: number): number {
  return Math.round(n * 100) / 100;
}

// ── ordering primitives ────────────────────────────────────────────────────────

const cmpStr = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);   // not localeCompare: same on every machine
const cmpNum = (a: number, b: number): number => (a === b ? 0 : a < b ? -1 : 1);   // safe for Infinity

/** Weighted median of `y` (weight `w`); Infinity for no points. */
export function weightedMedian(points: readonly { y: number; w: number }[]): number {
  if (points.length === 0) return Infinity;
  const sorted = [...points].sort((a, b) => cmpNum(a.y, b.y) || cmpNum(a.w, b.w));
  const total = sorted.reduce((t, p) => t + p.w, 0);
  let acc = 0;
  for (const p of sorted) {
    acc += p.w;
    if (acc >= total / 2) return p.y;
  }
  return sorted[sorted.length - 1].y;
}

// ── dates ──────────────────────────────────────────────────────────────────────

export const PRESENT = 1_000_000;
const MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"];

/**
 * A sortable number (months since year 0) for the date strings resumes carry:
 * `2024-07`, `Jun 2023`, `June 2025`, `Expected June 2027`, `Summer 2024`, `2027`,
 * `Present`. Null when there is no date to read.
 */
export function dateKey(raw: string | null | undefined): number | null {
  const s = (raw ?? "").trim().toLowerCase().replace(/^expected\s+/, "");
  if (!s || s === "?" || s === "—") return null;
  if (/^(present|current|now|ongoing|today)$/.test(s)) return PRESENT;
  const iso = s.match(/^(\d{4})[-/](\d{1,2})/);
  if (iso) return Number(iso[1]) * 12 + Math.min(11, Math.max(0, Number(iso[2]) - 1));
  const year = s.match(/\b(19|20)\d{2}\b/);
  if (!year) return null;
  const y = Number(year[0]);
  const word = s.match(/([a-z]{3,})\.?\s+(?:\d{1,2},?\s+)?(?:19|20)\d{2}/);
  const month = word ? MONTHS.indexOf(word[1].slice(0, 3)) : -1;
  return y * 12 + (month >= 0 ? month : 6);
}

export const endKey = (i: EvidenceItem): number => dateKey(i.end) ?? dateKey(i.start) ?? -1;
const startKey = (i: EvidenceItem): number => dateKey(i.start) ?? -1;

/** Most recent first; a total order, so the same items always list the same way. */
export function byRecency(a: EvidenceItem, b: EvidenceItem): number {
  return cmpNum(endKey(b), endKey(a)) || cmpNum(startKey(b), startKey(a))
    || cmpStr(a.title, b.title) || cmpStr(a.key, b.key);
}

// ── rows (the left column) ─────────────────────────────────────────────────────

export interface MapRow {
  item: EvidenceItem;
  indent: 0 | 1;
  /** In the trailing "Needs a description" group. */
  fix: boolean;
}

/** An experience or project with no text for a skill to be linked from. Education never links, so never counts. */
export const needsText = (i: EvidenceItem): boolean => i.kind !== "edu" && i.text_source === "none";

export function evidenceRows(items: readonly EvidenceItem[]): { rows: MapRow[]; fixStart: number } {
  const main = items.filter((i) => !needsText(i));
  const roots = main.filter((i) => i.kind !== "proj").sort(byRecency);
  const rootKeys = new Set(roots.map((i) => i.key));
  const projects = main.filter((i) => i.kind === "proj");
  const rows: MapRow[] = [];
  for (const root of roots) {
    rows.push({ item: root, indent: 0, fix: false });
    projects.filter((p) => p.parent_key === root.key).sort(byRecency)
      .forEach((p) => rows.push({ item: p, indent: 1, fix: false }));
  }
  projects.filter((p) => !p.parent_key || !rootKeys.has(p.parent_key)).sort(byRecency)
    .forEach((p) => rows.push({ item: p, indent: 0, fix: false }));
  const fixStart = rows.length;
  items.filter(needsText).sort(byRecency).forEach((i) => rows.push({ item: i, indent: 0, fix: true }));
  return { rows, fixStart };
}

// ── skills ─────────────────────────────────────────────────────────────────────

export interface SkillStat {
  edges: EvidenceEdge[];
  /** Citing bullets, summed over items. */
  bullets: number;
  /** Cited by at least one bullet: the only thing the brand green marks. */
  cited: boolean;
  /** Linked only by a recorded GitHub source: no bullet says what was done with it. */
  githubOnly: boolean;
}

export function skillStats(view: EvidenceView): Map<string, SkillStat> {
  const out = new Map<string, SkillStat>();
  for (const s of view.skills) out.set(s.key, { edges: [], bullets: 0, cited: false, githubOnly: false });
  for (const e of view.edges) {
    const st = out.get(e.skill_key);
    if (!st) continue;
    st.edges.push(e);
    st.bullets += e.cites.length;
  }
  for (const st of out.values()) {
    st.edges.sort((a, b) => cmpStr(a.item_key, b.item_key));   // not the order the payload happened to list them
    st.cited = st.bullets > 0;
    st.githubOnly = !st.cited && st.edges.some((e) => e.github);
  }
  return out;
}

/** Skills whose name contains `query` (case-insensitive); null for an empty query. */
export function searchSkills(view: EvidenceView, query: string): Set<string> | null {
  const q = query.trim().toLowerCase();
  if (!q) return null;
  return new Set(view.skills.filter((s) => s.name.toLowerCase().includes(q)).map((s) => s.key));
}

// ── layout ─────────────────────────────────────────────────────────────────────

export interface LayoutOptions {
  width: number;
  /** `areaFoldKey(area)` of every area the user folded. */
  folded: ReadonlySet<string>;
  query: string;
}

export interface PlacedRow extends MapRow {
  y: number;
}

export interface PlacedSkill {
  skill: EvidenceSkill;
  stat: SkillStat;
  y: number;
  r: number;
}

export interface PlacedChip {
  skill: EvidenceSkill;
  /** Relative to the skill column's left edge. */
  x: number;
  y: number;
  w: number;
}

export interface PlacedArea {
  key: string;
  y: number;
  /** Folded by the user and not forced open by a search match. */
  collapsed: boolean;
  total: number;
  evidenced: number;
  skills: PlacedSkill[];
  chips: PlacedChip[];
  /** Where a folded area's aggregated edges land. */
  hubY: number;
  /** Citing bullets over the area's skills, for the folded marker's size. */
  bullets: number;
}

export interface PlacedEdge {
  id: string;
  itemKey: string;
  /** Null when the edge is aggregated onto a folded area. */
  skillKey: string | null;
  areaKey: string | null;
  d: string;
  width: number;
  /** No bullet cites it: a recorded GitHub source only. */
  dashed: boolean;
  cites: number;
}

export interface MapLayout {
  width: number;
  height: number;
  leftW: number;
  skillX: number;
  colW: number;
  rowH: number;
  rows: PlacedRow[];
  fixStart: number;
  /** Where the "Needs a description" heading sits; null when there is none. */
  fixLabelY: number | null;
  areas: PlacedArea[];
  edges: PlacedEdge[];
  /** Skill keys in reading order (areas top to bottom, rows then chips), for Enter-to-select and tab order. */
  skillOrder: string[];
  matches: Set<string> | null;
}

const pointsOf = (edges: readonly EvidenceEdge[], rowIndex: ReadonlyMap<string, number>) =>
  edges.filter((e) => rowIndex.has(e.item_key))
    .map((e) => ({ y: rowIndex.get(e.item_key) as number, w: Math.max(1, e.cites.length) }));

export function layoutMap(view: EvidenceView, opts: LayoutOptions): MapLayout {
  const width = Math.max(MIN_WIDTH, Math.round(opts.width));
  const leftW = Math.min(LEFT_MAX, Math.round(width * 0.42));
  const skillX = width - SKILL_COLUMN;
  const colW = width - skillX - 4;

  const { rows, fixStart } = evidenceRows(view.items);
  const rowIndex = new Map(rows.map((r, i) => [r.item.key, i]));
  const stats = skillStats(view);
  const matches = searchSkills(view, opts.query);

  // Skills by area; an area's place is the weighted median of the rows its skills link to.
  const byArea = new Map<string, EvidenceSkill[]>();
  for (const s of view.skills) byArea.set(s.area, [...(byArea.get(s.area) ?? []), s]);
  const groups = [...byArea.entries()].map(([key, skills]) => {
    const linked = skills.filter((s) => (stats.get(s.key) as SkillStat).edges.length > 0)
      .map((s) => {
        const st = stats.get(s.key) as SkillStat;
        return { s, st, med: weightedMedian(pointsOf(st.edges, rowIndex)) };
      })
      .sort((a, b) => cmpNum(a.med, b.med) || cmpNum(b.st.bullets, a.st.bullets)
        || cmpStr(a.s.name, b.s.name) || cmpStr(a.s.key, b.s.key));
    const bare = skills.filter((s) => (stats.get(s.key) as SkillStat).edges.length === 0)
      .sort((a, b) => cmpStr(a.name, b.name) || cmpStr(a.key, b.key));
    const med = weightedMedian(linked.flatMap((d) => pointsOf(d.st.edges, rowIndex)));
    const forced = matches !== null && skills.some((s) => matches.has(s.key));
    return { key, skills, linked, bare, med, collapsed: opts.folded.has(areaFoldKey(key)) && !forced };
  }).sort((a, b) => {
    const ia = AREA_ORDER.indexOf(a.key);
    const ib = AREA_ORDER.indexOf(b.key);
    return cmpNum(a.med, b.med) || cmpNum(ia < 0 ? 99 : ia, ib < 0 ? 99 : ib) || cmpStr(a.key, b.key);
  });

  let y = TOP;
  const areas: PlacedArea[] = [];
  const skillOrder: string[] = [];
  for (const g of groups) {
    const area: PlacedArea = {
      key: g.key, y, collapsed: g.collapsed, total: g.skills.length,
      evidenced: g.skills.filter((s) => (stats.get(s.key) as SkillStat).cited).length,
      skills: [], chips: [], hubY: y + 10,
      bullets: g.linked.reduce((t, d) => t + d.st.bullets, 0),
    };
    y += AREA_HEADER;
    if (!g.collapsed) {
      for (const d of g.linked) {
        area.skills.push({ skill: d.s, stat: d.st, y: y + 6, r: skillRadius(d.st.bullets) });
        skillOrder.push(d.s.key);
        y += SKILL_GAP;
      }
      let cx = 0;
      let cy = y + 10;
      for (const s of g.bare) {
        const w = estimateTextWidth(s.name, 12) + 18;
        if (cx > 0 && cx + w > colW) {
          cx = 0;
          cy += CHIP_ROW;
        }
        area.chips.push({ skill: s, x: cx, y: cy, w });
        skillOrder.push(s.key);
        cx += w + CHIP_GAP;
      }
      if (area.chips.length) y = cy + 16;
    }
    y += AREA_GAP;
    areas.push(area);
  }
  const skillBottom = y;
  const rowH = Math.max(ROW_MIN, Math.min(ROW_MAX, (skillBottom - TOP) / Math.max(rows.length, 1)));
  const placedRows: PlacedRow[] = rows.map((r, i) => ({
    ...r, y: r2(TOP + i * rowH + rowH / 2 + (r.fix ? FIX_GAP : 0)),
  }));
  const rowY = new Map(placedRows.map((r) => [r.item.key, r.y]));
  const fixLabelY = rows.length > fixStart ? r2(placedRows[fixStart].y - rowH / 2 - 6) : null;
  const lastRow = placedRows[placedRows.length - 1];
  const height = Math.max(skillBottom, lastRow ? lastRow.y + rowH / 2 : 0) + BOTTOM;

  // Edges: straight to a visible skill, aggregated per item onto a folded area's header.
  const skillPos = new Map<string, PlacedSkill>();
  const areaOf = new Map<string, PlacedArea>();
  for (const a of areas) {
    for (const s of a.skills) skillPos.set(s.skill.key, s);
  }
  for (const s of view.skills) areaOf.set(s.key, areas.find((a) => a.key === s.area) as PlacedArea);
  const x0 = leftW + 6;
  const edges: PlacedEdge[] = [];
  const folded = new Map<string, PlacedEdge>();
  for (const e of [...view.edges].sort((a, b) => cmpStr(a.item_key, b.item_key) || cmpStr(a.skill_key, b.skill_key))) {
    const y0 = rowY.get(e.item_key);
    const skill = skillPos.get(e.skill_key);
    const area = areaOf.get(e.skill_key);
    if (y0 === undefined || !area) continue;
    if (skill) {
      edges.push({
        id: `${e.item_key}|${e.skill_key}`, itemKey: e.item_key, skillKey: e.skill_key, areaKey: null,
        d: linkPath(x0, y0, skillX - skill.r - 6, skill.y), width: r2(edgeWidth(e.cites.length)),
        dashed: e.cites.length === 0, cites: e.cites.length,
      });
    } else if (area.collapsed) {
      const id = `${e.item_key}|${areaFoldKey(area.key)}`;
      const prior = folded.get(id);
      const cites = (prior?.cites ?? 0) + e.cites.length;
      folded.set(id, {
        id, itemKey: e.item_key, skillKey: null, areaKey: area.key,
        d: linkPath(x0, y0, skillX - 10, area.hubY), width: r2(edgeWidth(cites)),
        dashed: cites === 0, cites,
      });
    }
  }
  edges.push(...[...folded.values()].sort((a, b) => cmpStr(a.id, b.id)));

  return {
    width, height: r2(height), leftW, skillX, colW, rowH: r2(rowH), rows: placedRows, fixStart, fixLabelY,
    areas, edges: edges.sort((a, b) => cmpStr(a.id, b.id)), skillOrder, matches,
  };
}

/** The first search match in reading order, for Enter-to-select. */
export function firstMatch(layout: MapLayout): string | null {
  return layout.matches ? (layout.skillOrder.find((k) => layout.matches?.has(k)) ?? null) : null;
}

/**
 * What stays lit when `id` is hovered or selected: the node, and what it links.
 * `id` is an item key, a skill key, or `areaFoldKey(area)` (a folded or hovered area header).
 */
export function neighborhood(view: EvidenceView, id: string): Set<string> {
  const out = new Set<string>([id]);
  if (id.startsWith("area:")) {
    const inArea = new Set(view.skills.filter((s) => areaFoldKey(s.area) === id).map((s) => s.key));
    inArea.forEach((k) => out.add(k));
    for (const e of view.edges) if (inArea.has(e.skill_key)) out.add(e.item_key);
    return out;
  }
  for (const e of view.edges) {
    if (e.item_key === id) out.add(e.skill_key);
    else if (e.skill_key === id) out.add(e.item_key);
  }
  return out;
}

// ── the side panel ─────────────────────────────────────────────────────────────

export interface CitingBullet {
  /** The harness ordinal: the `#b<n>` of this bullet. */
  index: number;
  text: string;
  /** `[start, end)` offsets of the words that name the skill. */
  marks: [number, number][];
}

export interface SkillEvidence {
  item: EvidenceItem;
  edge: EvidenceEdge;
  bullets: CitingBullet[];
}

/** Every item that backs a skill, in the map's reading order, with the bullets that cite it. */
export function skillEvidence(view: EvidenceView, skillKey: string): SkillEvidence[] {
  const { rows } = evidenceRows(view.items);
  const order = new Map(rows.map((r, i) => [r.item.key, i]));
  const byKey = new Map(view.items.map((i) => [i.key, i]));
  const out: SkillEvidence[] = [];
  for (const edge of view.edges) {
    const item = byKey.get(edge.skill_key === skillKey ? edge.item_key : "");
    if (!item) continue;
    const bullets = edge.cites.map((index, n): CitingBullet | null => {
      const entry = item.text.find((t) => t.index === index);
      return entry ? { index, text: entry.text, marks: edge.marks?.[n] ?? [] } : null;
    }).filter((b): b is CitingBullet => b !== null);
    out.push({ item, edge, bullets });
  }
  return out.sort((a, b) => cmpNum(order.get(a.item.key) ?? 1e9, order.get(b.item.key) ?? 1e9)
    || cmpStr(a.item.key, b.item.key));
}

/** The selection that opens the list of dismissed skills (#321). Not a node of the map. */
export const DISMISSED_KEY = "panel:dismissed";

/** A selection that names a panel, not a node: `ghost:<name>` or `DISMISSED_KEY`. Item and skill keys have no colon. */
export function isPanelKey(key: string): boolean {
  return key.includes(":");
}

/**
 * Whether a skill may be dismissed (#321): no bullet cites it. A GitHub-only link or no link at
 * all is a claim nothing proves; a cited skill is fixed by editing the bullet. The server decides
 * the same way and refuses (409) otherwise.
 */
export function canDismiss(view: EvidenceView, skillKey: string): boolean {
  return view.skills.some((s) => s.key === skillKey)
    && !view.edges.some((e) => e.skill_key === skillKey && e.cites.length > 0);
}

/** The server id behind a skill node key (`skill/<id>`). */
export function skillIdOf(skillKey: string): string {
  return skillKey.startsWith("skill/") ? skillKey.slice("skill/".length) : skillKey;
}

/** The most recent experiences and projects that have text, for "edit one of these to back this skill". */
export function recentEditable(view: EvidenceView, n = 3): EvidenceItem[] {
  return view.items.filter((i) => i.kind !== "edu").sort(byRecency).slice(0, n);
}

/** Per bullet ordinal, the skills an item's text links: what the editor shows under each bullet. */
export function linksByBullet(view: EvidenceView, itemKey: string): Map<number, EvidenceSkill[]> {
  const skills = new Map(view.skills.map((s) => [s.key, s]));
  const out = new Map<number, EvidenceSkill[]>();
  for (const e of view.edges) {
    if (e.item_key !== itemKey) continue;
    const skill = skills.get(e.skill_key);
    if (!skill) continue;
    for (const n of e.cites) out.set(n, [...(out.get(n) ?? []), skill]);
  }
  out.forEach((list, n) => out.set(n, list.sort((a, b) => cmpStr(a.name, b.name) || cmpStr(a.key, b.key))));
  return out;
}

/** Skills that tie an item to GitHub but are named by no bullet of it. */
export function githubOnlyLinks(view: EvidenceView, itemKey: string): EvidenceSkill[] {
  const skills = new Map(view.skills.map((s) => [s.key, s]));
  return view.edges.filter((e) => e.item_key === itemKey && e.github && e.cites.length === 0)
    .map((e) => skills.get(e.skill_key)).filter((s): s is EvidenceSkill => !!s)
    .sort((a, b) => cmpStr(a.name, b.name));
}

/** Split `text` at the marked ranges: `[{text, hit}]`, in order, covering all of it. */
export function highlightSegments(text: string, marks: readonly [number, number][]): { text: string; hit: boolean }[] {
  const out: { text: string; hit: boolean }[] = [];
  let at = 0;
  for (const [s, e] of [...marks].sort((a, b) => a[0] - b[0])) {
    const start = Math.max(at, Math.min(s, text.length));
    const end = Math.min(e, text.length);
    if (end <= start) continue;
    if (start > at) out.push({ text: text.slice(at, start), hit: false });
    out.push({ text: text.slice(start, end), hit: true });
    at = end;
  }
  if (at < text.length) out.push({ text: text.slice(at), hit: false });
  return out;
}

/** "Jun 2023 – Present" from the two date strings, whichever exist. */
export function dateRange(start: string, end: string): string {
  const a = (start ?? "").trim();
  const b = (end ?? "").trim();
  if (a && b) return `${a} – ${b}`;
  return a || b || "";
}

// ── compare to a job ───────────────────────────────────────────────────────────

/** Two spellings of one skill compare equal: case, spaces, hyphens, dots and underscores are ignored. */
export const skillMatchKey = (name: string): string => name.toLowerCase().replace(/[\s._-]+/g, "");

export type JobClass = "required-evidenced" | "required-claimed" | "preferred-evidenced" | "preferred-claimed";

export interface JobCounts {
  total: number;
  /** On the profile and cited by a bullet. */
  evidenced: number;
  /** On the profile, but nothing cites it. */
  claimed: number;
  /** Not on the profile. */
  missing: number;
}

export interface JobOverlay {
  /** Profile skills the job names, by skill key. Every other skill is "not in this job" and fades. */
  classBySkill: Map<string, JobClass>;
  weightBySkill: Map<string, number>;
  /** Required skills the profile lacks: warning chips. */
  missing: JobSkillRow[];
  /** Separate counts, never combined into one number and never mixed with the ATS composite. */
  required: JobCounts;
  preferred: JobCounts;
  /** Per area: required skills the job names there, and how many of them a bullet backs. */
  areaRequired: Map<string, { total: number; evidenced: number }>;
}

/** One requirement of a job, with the profile skills it resolves to (none when the profile lacks it). */
export interface JobMatch {
  /** Stable id (`skillMatchKey` of the requirement's name), the key coverage is tracked by. */
  id: string;
  row: JobSkillRow;
  /** Profile skills any of the requirement's terms names, in skill-key order. */
  skills: EvidenceSkill[];
}

/**
 * The job's requirements, required first then by name, each resolved to the profile
 * skills it names. A requirement matches a skill when its name or any of its `terms`
 * does (`skillMatchKey`), so a harness-opened job's "docker, kubernetes" requirement
 * finds either. A name the job lists twice counts once (required wins: sorted first).
 */
export function matchJobSkills(view: EvidenceView, jobSkills: readonly JobSkillRow[]): JobMatch[] {
  const byKey = new Map<string, EvidenceSkill[]>();
  for (const s of [...view.skills].sort((a, b) => cmpStr(a.key, b.key))) {
    const k = skillMatchKey(s.name);
    byKey.set(k, [...(byKey.get(k) ?? []), s]);
  }
  const rows = [...jobSkills].sort((a, b) => Number(b.required) - Number(a.required) || cmpStr(a.name, b.name));
  const seen = new Set<string>();
  const out: JobMatch[] = [];
  for (const row of rows) {
    const id = skillMatchKey(row.name);
    if (!id || seen.has(id)) continue;
    seen.add(id);
    const skills = new Map<string, EvidenceSkill>();
    for (const term of [row.name, ...(row.terms ?? [])]) {
      for (const s of byKey.get(skillMatchKey(term)) ?? []) skills.set(s.key, s);
    }
    out.push({ id, row, skills: [...skills.values()].sort((a, b) => cmpStr(a.key, b.key)) });
  }
  return out;
}

export function jobOverlay(view: EvidenceView, jobSkills: readonly JobSkillRow[]): JobOverlay {
  const stats = skillStats(view);
  const classBySkill = new Map<string, JobClass>();
  const weightBySkill = new Map<string, number>();
  const missing: JobSkillRow[] = [];
  const required: JobCounts = { total: 0, evidenced: 0, claimed: 0, missing: 0 };
  const preferred: JobCounts = { total: 0, evidenced: 0, claimed: 0, missing: 0 };
  const areaRequired = new Map<string, { total: number; evidenced: number }>();
  const isCited = (s: EvidenceSkill): boolean => (stats.get(s.key) as SkillStat).cited;
  for (const { row: js, skills } of matchJobSkills(view, jobSkills)) {
    const counts = js.required ? required : preferred;
    counts.total += 1;
    // The area of the profile skill it resolves to (a cited one first), else the job's own.
    const lead = skills.find(isCited) ?? skills[0];
    const area = lead?.area ?? js.area;
    if (js.required) {
      const a = areaRequired.get(area) ?? { total: 0, evidenced: 0 };
      a.total += 1;
      areaRequired.set(area, a);
    }
    if (skills.length === 0) {
      counts.missing += 1;
      if (js.required) missing.push(js);
      continue;
    }
    const cited = skills.some(isCited);          // one requirement counts once, however many skills name it
    counts[cited ? "evidenced" : "claimed"] += 1;
    if (js.required && cited) (areaRequired.get(area) as { total: number; evidenced: number }).evidenced += 1;
    for (const skill of skills) {
      if (classBySkill.has(skill.key)) continue;   // the first requirement to name a skill (required sorts first) classes it
      classBySkill.set(skill.key, `${js.required ? "required" : "preferred"}-${isCited(skill) ? "evidenced" : "claimed"}` as JobClass);
      weightBySkill.set(skill.key, js.weight);
    }
  }
  return { classBySkill, weightBySkill, missing, required, preferred, areaRequired };
}

export interface GhostChip {
  skill: JobSkillRow;
  x: number;
  y: number;
  w: number;
}

/**
 * Where the missing required skills go. They are a block below everything the map
 * already draws, labelled with their area, so turning the job on adds elements and
 * moves none: every row, skill and edge keeps the position `layoutMap` gave it.
 */
export function ghostBlock(layout: MapLayout, missing: readonly JobSkillRow[]): { chips: GhostChip[]; top: number; height: number } {
  const sorted = [...missing].sort((a, b) => {
    const ia = AREA_ORDER.indexOf(a.area);
    const ib = AREA_ORDER.indexOf(b.area);
    return cmpNum(ia < 0 ? 99 : ia, ib < 0 ? 99 : ib) || cmpStr(a.name, b.name);
  });
  const top = layout.height;
  if (sorted.length === 0) return { chips: [], top, height: 0 };
  let cx = 0;
  let cy = top + 34;
  const chips: GhostChip[] = [];
  for (const skill of sorted) {
    const w = estimateTextWidth(`${skill.name} · ${skill.area}`, 12) + 18;
    if (cx > 0 && cx + w > layout.colW) {
      cx = 0;
      cy += CHIP_ROW;
    }
    chips.push({ skill, x: cx, y: cy, w });
    cx += w + CHIP_GAP;
  }
  return { chips, top, height: cy + 16 - top };
}

// ── the editor ─────────────────────────────────────────────────────────────────

export interface FieldDef {
  key: string;
  label: string;
  multiline?: boolean;
}

/** The columns an item's header form edits, under their API names. */
export function editableFields(item: EvidenceItem): FieldDef[] {
  if (item.kind === "exp") {
    return [
      { key: "title", label: "Title" }, { key: "company", label: "Company" },
      { key: "start_date", label: "Start" }, { key: "end_date", label: "End" },
      { key: "description", label: "Description", multiline: true },
    ];
  }
  if (item.kind === "edu") {
    return [
      { key: "institution", label: "Institution" }, { key: "degree", label: "Degree" },
      { key: "location", label: "Location" }, { key: "gpa", label: "GPA" },
      { key: "start_date", label: "Start" }, { key: "end_date", label: "End" },
    ];
  }
  const fields: FieldDef[] = [
    { key: "name", label: "Name" }, { key: "repo_url", label: "Repo URL" },
    { key: "start_date", label: "Start" }, { key: "end_date", label: "End" },
  ];
  // With blurbs the description is not what links skills; otherwise it is the text itself.
  if (item.text_source === "blurbs") fields.push({ key: "description", label: "Description", multiline: true });
  return fields;
}

/** Whether the text list is a single description (one textarea, no add-a-bullet). */
export const textIsDescription = (item: EvidenceItem): boolean =>
  item.kind === "proj" && (item.text_source === "description" || item.text_source === "none");

export interface Draft {
  fields: Record<string, string>;
  /** Parallel to `item.text`, then any bullets added in the editor. */
  texts: string[];
}

export function initialDraft(item: EvidenceItem): Draft {
  const fields: Record<string, string> = {};
  for (const f of editableFields(item)) fields[f.key] = item.fields[f.key] ?? "";
  const texts = item.text.map((t) => t.text);
  if (textIsDescription(item) && texts.length === 0) texts.push("");
  return { fields, texts };
}

export type SaveOp =
  | { kind: "experience" | "education" | "project"; id: string; body: Record<string, string | string[]> }
  | { kind: "blurb"; projectId: string; blurbId: string; content: string };

export interface SavePlan {
  ops: SaveOp[];
  errors: string[];
}

const REQUIRED: Record<string, { key: string; label: string }[]> = {
  exp: [{ key: "title", label: "Title" }, { key: "company", label: "Company" }],
  edu: [{ key: "institution", label: "Institution" }],
  proj: [{ key: "name", label: "Name" }],
};

/** What saving a draft means in API calls: the changed columns in one PATCH, plus one call per edited blurb. */
export function planSave(item: EvidenceItem, draft: Draft): SavePlan {
  const body: Record<string, string | string[]> = {};
  const errors: string[] = [];
  for (const f of editableFields(item)) {
    if ((draft.fields[f.key] ?? "") !== (item.fields[f.key] ?? "")) body[f.key] = draft.fields[f.key] ?? "";
  }
  for (const r of REQUIRED[item.kind] ?? []) {
    if (r.key in body && !String(body[r.key]).trim()) errors.push(`${r.label} can't be empty.`);
  }
  const ops: SaveOp[] = [];
  const original = item.text.map((t) => t.text);
  if (item.kind === "exp") {
    // Written back by raw position; the server drops empty entries, which renumbers `#b<n>` only past a gap.
    if (draft.texts.length !== original.length || draft.texts.some((t, i) => t !== original[i])) {
      body.bullets = [...draft.texts];
    }
  } else if (textIsDescription(item)) {
    if ((draft.texts[0] ?? "") !== (item.fields.description ?? "")) body.description = draft.texts[0] ?? "";
  } else if (item.text_source === "blurbs") {
    item.text.forEach((t, i) => {
      const next = draft.texts[i] ?? t.text;
      if (next === t.text || !t.blurb_id) return;
      if (!next.trim()) errors.push(`Bullet ${t.index !== null ? t.index + 1 : i + 1} can't be empty.`);
      else ops.push({ kind: "blurb", projectId: item.id, blurbId: t.blurb_id, content: next });
    });
  }
  if (Object.keys(body).length > 0) {
    const kind = item.kind === "exp" ? "experience" : item.kind === "edu" ? "education" : "project";
    ops.unshift({ kind, id: item.id, body });
  }
  return { ops: errors.length ? [] : ops, errors };
}
