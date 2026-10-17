/**
 * The area map's layout (issue #320), as a pure function of the server payload.
 *
 * A global view of the knowledge graph clustered by skill area, where every position means
 * something. No physics library, no randomness, no clock: the picture is the same on every
 * visit and for any order the rows arrive in.
 *
 *  - Areas sit on a ring. Their order is the cyclic order that maximises the evidence
 *    neighbours share (all of them are scored: there are at most eight areas).
 *  - A skill is a circle sized by its citing bullets. An area's skills are placed
 *    largest-first on a phyllotaxis spiral, each at the first spiral point where it overlaps
 *    nothing placed, so a cluster is compact, round and free of overlaps.
 *  - An experience or project sits at the weighted mix of the areas its bullets cite, drawn
 *    toward the middle, then a fixed number of relaxation passes push items apart and out of
 *    the discs. Work in one area ends up on the inner side of that area's disc; work that
 *    spans areas sits nearer the middle. Items nothing links are listed, not placed.
 *  - Labels are placed greedily, each in the free slot (right, left, below, above) that
 *    overlaps least.
 *
 * Nothing in `AreaMapLayout` depends on the window, the search text or the job overlay, so
 * resizing, searching and comparing to a job never move a node.
 */
import type { EvidenceEdge, EvidenceItem, EvidenceSkill, EvidenceView } from "../types";
import {
  AREA_ORDER, areaFoldKey, estimateTextWidth, fitText, skillRadius, skillStats, type SkillStat,
} from "./evidenceMap";

// ── tuning ─────────────────────────────────────────────────────────────────────

/** A skill circle is never smaller than this, so an uncited skill can still be seen and hit. */
const SKILL_MIN_R = 5;
/** Clear space between two skill circles in one cluster. */
const PACK_GAP = 4.5;
/** Spiral spacing: the n-th point is `SPIRAL_STEP * sqrt(n)` from the middle. */
const SPIRAL_STEP = 1.5;
const SPIRAL_POINTS = 24000;
/** Space between a cluster's outermost circle and its disc. */
const DISC_PAD = 14;
const DISC_MIN_R = 24;
/** Clear space between two neighbouring discs on the ring. */
const DISC_GAP = 56;
/** How far out from the middle an item's target sits, as a share of the way to its areas. */
const PULL = 0.8;
/** Marker half-size and the room an item keeps from another item or a disc. */
const MARK_R = 8;
const ITEM_CLEAR = 22;
/** Semi-axes of the ellipse two items keep apart: a marker with its label, and a text line. */
const SEP_X = 66;
const SEP_Y = 21;
const RELAX_PASSES = 90;
const LABEL_H = 15;
const LABEL_FONT = 12;
const MARGIN = 22;
/** Longest item label, in px. */
const ITEM_LABEL_MAX = 150;
const AREA_LINE_H = 15;

// ── small helpers ──────────────────────────────────────────────────────────────

const cmpStr = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);   // not localeCompare: same on every machine
const r2 = (n: number): number => Math.round(n * 100) / 100;
const areaRank = (a: string): number => {
  const i = AREA_ORDER.indexOf(a);
  return i < 0 ? 99 : i;
};
const cmpArea = (a: string, b: string): number => areaRank(a) - areaRank(b) || cmpStr(a, b);

export interface Box { x0: number; y0: number; x1: number; y1: number }

function overlapArea(a: Box, b: Box): number {
  const w = Math.min(a.x1, b.x1) - Math.max(a.x0, b.x0);
  const h = Math.min(a.y1, b.y1) - Math.max(a.y0, b.y0);
  return w > 0 && h > 0 ? w * h : 0;
}

const circleBox = (x: number, y: number, r: number): Box => ({ x0: x - r, y0: y - r, x1: x + r, y1: y + r });

// ── packing circles on a phyllotaxis spiral ────────────────────────────────────

const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));

/**
 * Centres for circles of the given radii, placed in the order given (largest first packs
 * best). Each goes at the first point of a Vogel spiral where it keeps `gap` clear of every
 * circle already placed. The first sits at the origin.
 */
export function packCircles(radii: readonly number[], gap = PACK_GAP): { x: number; y: number }[] {
  const out: { x: number; y: number }[] = [];
  radii.forEach((r, i) => {
    for (let k = 0; k < SPIRAL_POINTS; k++) {
      const d = SPIRAL_STEP * Math.sqrt(k);
      const x = d * Math.cos(k * GOLDEN_ANGLE);
      const y = d * Math.sin(k * GOLDEN_ANGLE);
      if (out.every((p, j) => Math.hypot(p.x - x, p.y - y) >= radii[j] + r + gap)) {
        out.push({ x, y });
        return;
      }
    }
    out.push({ x: i * 1000, y: 0 });   // unreachable for any real cluster: the spiral outgrows it first
  });
  return out;
}

/** A circle that contains every given circle, near the smallest one (Badoiu-Clarkson, fixed iterations). */
export function enclose(circles: readonly { x: number; y: number; r: number }[]): { x: number; y: number; r: number } {
  if (circles.length === 0) return { x: 0, y: 0, r: 0 };
  let cx = circles.reduce((t, c) => t + c.x, 0) / circles.length;
  let cy = circles.reduce((t, c) => t + c.y, 0) / circles.length;
  for (let t = 1; t <= 160; t++) {
    let far = circles[0];
    let farD = -1;
    for (const c of circles) {
      const d = Math.hypot(c.x - cx, c.y - cy) + c.r;
      if (d > farD) { farD = d; far = c; }
    }
    cx += (far.x - cx) / (t + 1);
    cy += (far.y - cy) / (t + 1);
  }
  const r = Math.max(...circles.map((c) => Math.hypot(c.x - cx, c.y - cy) + c.r));
  return { x: cx, y: cy, r };
}

// ── the ring order ─────────────────────────────────────────────────────────────

/** Per area, the weight of each item's evidence there: citing bullets, with a GitHub-only link worth half a bullet. */
export function areaItemWeights(view: EvidenceView): Map<string, Map<string, number>> {
  const areaOf = new Map(view.skills.map((s) => [s.key, s.area]));
  const items = new Set(view.items.map((i) => i.key));
  const out = new Map<string, Map<string, number>>();
  for (const e of view.edges) {
    const area = areaOf.get(e.skill_key);
    if (area === undefined || !items.has(e.item_key)) continue;
    const m = out.get(area) ?? new Map<string, number>();
    m.set(e.item_key, (m.get(e.item_key) ?? 0) + edgeWeight(e));
    out.set(area, m);
  }
  return out;
}

const edgeWeight = (e: EvidenceEdge): number => Math.max(0.5, e.cites.length);

/** Evidence two areas share: for every item, the smaller of its weights into the two. */
export function sharedEvidence(weights: ReadonlyMap<string, ReadonlyMap<string, number>>, a: string, b: string): number {
  const wa = weights.get(a);
  const wb = weights.get(b);
  if (!wa || !wb) return 0;
  let t = 0;
  wa.forEach((v, k) => {
    const w = wb.get(k);
    if (w !== undefined) t += Math.min(v, w);
  });
  return t;
}

/** What a cyclic order is worth: the evidence its neighbours share, summed round the ring. */
export function ringScore(order: readonly string[], weights: ReadonlyMap<string, ReadonlyMap<string, number>>): number {
  if (order.length < 2) return 0;
  let t = 0;
  order.forEach((a, i) => { t += sharedEvidence(weights, a, order[(i + 1) % order.length]); });
  return t;
}

/** Most areas worth trying every order of (the fixed set has eight; 8! / 8 / 2 orders are distinct). */
const BRUTE_FORCE_MAX = 9;

/**
 * The cyclic order of `areas` that maximises the evidence neighbours share. The first area in
 * the fixed area order is held in place (a rotation is the same ring) and every order of the
 * rest is tried; ties go to the first found, which is the one nearest the fixed area order, so
 * the answer does not depend on the order the areas arrive in.
 */
export function ringOrder(areas: readonly string[], weights: ReadonlyMap<string, ReadonlyMap<string, number>>): string[] {
  const sorted = [...new Set(areas)].sort(cmpArea);
  if (sorted.length <= 3 || sorted.length > BRUTE_FORCE_MAX) return sorted;   // three or fewer: one ring; more than we can try: the fixed order
  const [first, ...rest] = sorted;
  let best = sorted;
  let bestScore = ringScore(sorted, weights);
  const used = new Array<boolean>(rest.length).fill(false);
  const cur: string[] = [first];
  const walk = (): void => {
    if (cur.length === sorted.length) {
      const s = ringScore(cur, weights);
      if (s > bestScore) { bestScore = s; best = [...cur]; }
      return;
    }
    for (let i = 0; i < rest.length; i++) {
      if (used[i]) continue;
      used[i] = true;
      cur.push(rest[i]);
      walk();
      cur.pop();
      used[i] = false;
    }
  };
  walk();
  return best;
}

// ── label slots ────────────────────────────────────────────────────────────────

export type Anchor = "start" | "middle" | "end";

export interface Obstacle extends Box {
  /** 1 for something a label must not cover; less for something it may cross (a disc's empty space). */
  weight: number;
}

export interface PlacedLabel {
  text: string;
  /** Where the text is drawn (SVG `x`, baseline `y`) and how it is anchored there. */
  x: number;
  y: number;
  anchor: Anchor;
  box: Box;
  /** Weighted area of the obstacles it covers; 0 means a clear slot. */
  overlap: number;
}

/** Candidate slots around a marker at (x, y), in order of preference. `gap` is the clear distance from the marker's centre. */
export function labelSlots(x: number, y: number, w: number, gap: number, diagonals = false): Omit<PlacedLabel, "text" | "overlap">[] {
  const h = LABEL_H;
  const slot = (tx: number, ty: number, anchor: Anchor, x0: number, y0: number): Omit<PlacedLabel, "text" | "overlap"> => ({
    x: r2(tx), y: r2(ty), anchor, box: { x0: r2(x0), y0: r2(y0), x1: r2(x0 + w), y1: r2(y0 + h) },
  });
  const slots = [
    slot(x + gap, y + 4.5, "start", x + gap, y - h / 2),                         // right
    slot(x - gap, y + 4.5, "end", x - gap - w, y - h / 2),                       // left
    slot(x, y + gap + 11.5, "middle", x - w / 2, y + gap + 1),                   // below
    slot(x, y - gap - 4, "middle", x - w / 2, y - gap - h + 1),                  // above
  ];
  if (diagonals) {
    const d = gap * 0.72;
    slots.push(
      slot(x + d, y - d - 3, "start", x + d, y - d - h + 1),                     // up and right
      slot(x - d, y - d - 3, "end", x - d - w, y - d - h + 1),                   // up and left
      slot(x + d, y + d + 10, "start", x + d, y + d - 1),                        // down and right
      slot(x - d, y + d + 10, "end", x - d - w, y + d - 1),                      // down and left
    );
  }
  return slots;
}

/** The slot whose text covers the least of the obstacles; the first of equals, so the order above is the tie-break. */
export function chooseLabelSlot(
  x: number, y: number, text: string, width: number, gap: number, obstacles: readonly Obstacle[], diagonals = false,
): PlacedLabel {
  let pick: PlacedLabel | null = null;
  for (const s of labelSlots(x, y, width, gap, diagonals)) {
    const overlap = obstacles.reduce((t, o) => t + overlapArea(s.box, o) * o.weight, 0);
    if (pick === null || overlap < pick.overlap) pick = { ...s, text, overlap: r2(overlap) };
    if (overlap === 0) break;
  }
  return pick as PlacedLabel;
}

// ── the layout ─────────────────────────────────────────────────────────────────

export interface PackedSkill {
  skill: EvidenceSkill;
  stat: SkillStat;
  /** Centre, in the layout's coordinates (the ring's middle is the origin). */
  x: number;
  y: number;
  r: number;
  areaKey: string;
}

export interface AreaLabel {
  x: number;
  y: number;
  anchor: Anchor;
  /** The area's name, its "evidenced/total", and its most-cited skills, one per line. */
  title: string;
  count: string;
  top: string;
  box: Box;
}

export interface PlacedArea {
  key: string;
  /** Position on the ring, from 0. */
  index: number;
  x: number;
  y: number;
  r: number;
  /** Direction from the ring's middle, radians. */
  angle: number;
  skills: PackedSkill[];
  total: number;
  evidenced: number;
  label: AreaLabel;
}

export interface PlacedItem {
  item: EvidenceItem;
  x: number;
  y: number;
  /** The areas its bullets cite, with their weights. */
  areas: { area: string; weight: number }[];
  label: PlacedLabel;
}

export interface AreaMapEdge {
  id: string;
  itemKey: string;
  skillKey: string;
  areaKey: string;
  d: string;
  width: number;
  /** No bullet cites it: a recorded GitHub source only. */
  dashed: boolean;
  cites: number;
}

export interface AreaMapLayout {
  /** The drawing's extent: the SVG `viewBox`. */
  viewBox: { x: number; y: number; w: number; h: number };
  ringR: number;
  areas: PlacedArea[];
  items: PlacedItem[];
  /** Experiences and projects no skill links to: listed under the map, not placed. */
  loose: EvidenceItem[];
  edges: AreaMapEdge[];
}

/** Marker for an item: an experience's company, otherwise the item's own name. */
export const itemLabelText = (i: EvidenceItem): string => (i.kind === "exp" ? i.org || i.title : i.title);

/** Link width: 0.7 plus a log of the citing bullets, thinner than the evidence map's since these cross each other. */
export const linkWidth = (cites: number): number => 0.7 + Math.log2(1 + Math.max(0, cites));

const unit = (dx: number, dy: number): { x: number; y: number } => {
  const d = Math.hypot(dx, dy);
  return d < 1e-9 ? { x: 0, y: -1 } : { x: dx / d, y: dy / d };
};

/** The ring radius at which discs of radii `rs`, in order, sit `DISC_GAP` apart and fill the circle. */
function solveRing(rs: readonly number[]): number {
  const n = rs.length;
  const need = rs.map((r, i) => r + rs[(i + 1) % n] + DISC_GAP);
  const turn = (R: number): number => need.reduce((t, d) => t + 2 * Math.asin(Math.min(1, d / (2 * R))), 0);
  let lo = Math.max(...need) / 2;
  let hi = need.reduce((t, d) => t + d, 0);
  for (let i = 0; i < 60; i++) {
    const mid = (lo + hi) / 2;
    if (turn(mid) > 2 * Math.PI) lo = mid; else hi = mid;
  }
  return hi;
}

export function layoutAreaMap(view: EvidenceView): AreaMapLayout {
  const stats = skillStats(view);
  const skillsOf = new Map<string, EvidenceSkill[]>();
  for (const s of view.skills) skillsOf.set(s.area, [...(skillsOf.get(s.area) ?? []), s]);

  // 1. The ring's order, and each area's cluster of skills.
  const weights = areaItemWeights(view);
  const order = ringOrder([...skillsOf.keys()], weights);
  const clusters = order.map((key) => {
    const ss = (skillsOf.get(key) as EvidenceSkill[]).map((skill) => {
      const stat = stats.get(skill.key) as SkillStat;
      return { skill, stat, r: Math.max(SKILL_MIN_R, 1.2 * skillRadius(stat.bullets)) };
    }).sort((a, b) => b.r - a.r || b.stat.bullets - a.stat.bullets
      || cmpStr(a.skill.name, b.skill.name) || cmpStr(a.skill.key, b.skill.key));
    const at = packCircles(ss.map((s) => s.r));
    const circles = ss.map((s, i) => ({ ...s, x: at[i].x, y: at[i].y }));
    const box = enclose(circles);
    return {
      key, r: Math.max(DISC_MIN_R, box.r + DISC_PAD),
      circles: circles.map((c) => ({ ...c, x: c.x - box.x, y: c.y - box.y })),   // about the disc's own centre
    };
  });

  // 2. Item targets, so the ring can leave room for them in the middle.
  const areaIdx = new Map(order.map((a, i) => [a, i]));
  const skillArea = new Map(view.skills.map((s) => [s.key, s.area]));
  const mix = new Map<string, Map<string, number>>();
  for (const e of view.edges) {
    const area = skillArea.get(e.skill_key);
    if (area === undefined) continue;
    const m = mix.get(e.item_key) ?? new Map<string, number>();
    m.set(area, (m.get(area) ?? 0) + edgeWeight(e));
    mix.set(e.item_key, m);
  }
  const itemByKey = new Map(view.items.map((i) => [i.key, i]));
  const placeable = [...mix.keys()].filter((k) => itemByKey.has(k)).sort(cmpStr).map((k) => itemByKey.get(k) as EvidenceItem);
  const placeableKeys = new Set(placeable.map((i) => i.key));
  const loose = view.items.filter((i) => i.kind !== "edu" && !placeableKeys.has(i.key))
    .sort((a, b) => cmpStr(a.title, b.title) || cmpStr(a.key, b.key));

  // 3. The ring: discs a fixed gap apart, then widened to leave a hollow for the items.
  const n = clusters.length;
  const maxR = n ? Math.max(...clusters.map((c) => c.r)) : 0;
  const hollow = 96 + 20 * Math.sqrt(placeable.length);
  let ringR = 0;
  if (n > 1) ringR = Math.max(solveRing(clusters.map((c) => c.r)), maxR + hollow);
  const angles: number[] = [];
  if (n > 1) {
    const need = clusters.map((c, i) => c.r + clusters[(i + 1) % n].r + DISC_GAP);
    const steps = need.map((d) => 2 * Math.asin(Math.min(1, d / (2 * ringR))));
    const scale = (2 * Math.PI) / steps.reduce((t, s) => t + s, 0);
    let a = -Math.PI / 2;
    clusters.forEach((_, i) => {
      angles.push(a);
      a += steps[i] * scale;
    });
  } else if (n === 1) {
    angles.push(-Math.PI / 2);
  }

  const areas: PlacedArea[] = clusters.map((c, i) => {
    const x = ringR * Math.cos(angles[i]);
    const y = ringR * Math.sin(angles[i]);
    const skills = c.circles.map((s): PackedSkill => ({
      skill: s.skill, stat: s.stat, x: r2(x + s.x), y: r2(y + s.y), r: s.r, areaKey: c.key,
    }));
    const evidenced = skills.filter((s) => s.stat.cited).length;
    const top = skills.filter((s) => s.stat.cited)
      .sort((a, b) => b.stat.bullets - a.stat.bullets || cmpStr(a.skill.name, b.skill.name))
      .slice(0, 3).map((s) => s.skill.name);
    while (top.length > 1 && top.join(" · ").length > 30) top.pop();
    return {
      key: c.key, index: i, x: r2(x), y: r2(y), r: r2(c.r), angle: angles[i], skills,
      total: skills.length, evidenced,
      label: areaLabel(c.key, x, y, c.r, angles[i], evidenced, skills.length, top.join(" · "), n === 1),
    };
  });

  // 4. Items: the weighted mix of their areas' centres, pulled in, then relaxed.
  const pts = placeable.map((item) => {
    const m = mix.get(item.key) as Map<string, number>;
    const total = [...m.values()].reduce((t, v) => t + v, 0);
    let tx = 0;
    let ty = 0;
    m.forEach((v, a) => {
      const area = areas[areaIdx.get(a) as number];
      tx += (area.x * v) / total;
      ty += (area.y * v) / total;
    });
    return { item, tx: tx * PULL, ty: ty * PULL, x: tx * PULL, y: ty * PULL };
  });
  relax(pts, areas);

  // 5. Item labels, greedily, around everything already on the page.
  const fixed: Obstacle[] = [
    ...areas.map((a) => ({ ...circleBox(a.x, a.y, a.r), weight: 0.25 })),
    ...areas.map((a) => ({ ...a.label.box, weight: 1 })),
    ...areas.flatMap((a) => a.skills.map((s) => ({ ...circleBox(s.x, s.y, s.r + 1.5), weight: 1 }))),
    ...pts.map((p) => ({ ...circleBox(p.x, p.y, MARK_R + 1), weight: 1 })),
  ];
  const taken: Obstacle[] = [];
  const items: PlacedItem[] = pts.map((p) => {
    const text = fitText(itemLabelText(p.item), ITEM_LABEL_MAX, LABEL_FONT);
    const label = chooseLabelSlot(p.x, p.y, text, estimateTextWidth(text, LABEL_FONT) + 2, MARK_R + 2, [...fixed, ...taken], true);
    taken.push({ ...label.box, weight: 1 });
    const m = mix.get(p.item.key) as Map<string, number>;
    return {
      item: p.item, x: r2(p.x), y: r2(p.y), label,
      areas: [...m.entries()].map(([area, weight]) => ({ area, weight })).sort((a, b) => cmpArea(a.area, b.area)),
    };
  });

  // 6. Edges: a quadratic curve per (item, skill), bent a little toward the middle.
  const skillAt = new Map(areas.flatMap((a) => a.skills).map((s) => [s.skill.key, s]));
  const itemAt = new Map(items.map((i) => [i.item.key, i]));
  const edges: AreaMapEdge[] = [];
  for (const e of [...view.edges].sort((a, b) => cmpStr(a.item_key, b.item_key) || cmpStr(a.skill_key, b.skill_key))) {
    const it = itemAt.get(e.item_key);
    const sk = skillAt.get(e.skill_key);
    if (!it || !sk) continue;
    const mx = (it.x + sk.x) / 2;
    const my = (it.y + sk.y) / 2;
    const cx = mx - mx * 0.18;
    const cy = my - my * 0.18;
    const a = unit(cx - it.x, cy - it.y);
    const b = unit(cx - sk.x, cy - sk.y);
    const x0 = it.x + a.x * (MARK_R + 1);
    const y0 = it.y + a.y * (MARK_R + 1);
    const x1 = sk.x + b.x * (sk.r + 1.5);
    const y1 = sk.y + b.y * (sk.r + 1.5);
    edges.push({
      id: `${e.item_key}|${e.skill_key}`, itemKey: e.item_key, skillKey: e.skill_key, areaKey: sk.areaKey,
      d: `M${r2(x0)},${r2(y0)}Q${r2(cx)},${r2(cy)},${r2(x1)},${r2(y1)}`,
      width: r2(linkWidth(e.cites.length)), dashed: e.cites.length === 0, cites: e.cites.length,
    });
  }

  // 7. The extent of everything drawn.
  const boxes: Box[] = [
    ...areas.flatMap((a) => [circleBox(a.x, a.y, a.r), a.label.box]),
    ...items.flatMap((i) => [circleBox(i.x, i.y, MARK_R + 2), i.label.box]),
  ];
  const x0 = Math.min(0, ...boxes.map((b) => b.x0)) - MARGIN;
  const y0 = Math.min(0, ...boxes.map((b) => b.y0)) - MARGIN;
  const x1 = Math.max(0, ...boxes.map((b) => b.x1)) + MARGIN;
  const y1 = Math.max(0, ...boxes.map((b) => b.y1)) + MARGIN;
  return {
    viewBox: { x: r2(x0), y: r2(y0), w: r2(x1 - x0), h: r2(y1 - y0) },
    ringR: r2(ringR), areas, items, loose, edges,
  };
}

/** Where an area's name, count and best skills go: outside its disc, along the line from the ring's middle. */
function areaLabel(
  key: string, x: number, y: number, r: number, angle: number, evidenced: number, total: number, top: string, alone: boolean,
): AreaLabel {
  const count = `${evidenced}/${total} evidenced`;
  const lines = top ? 3 : 2;
  const w = Math.max(
    estimateTextWidth(key.toUpperCase(), 11) * 1.25,
    estimateTextWidth(`${count} · 00/00 required`, 12),
    estimateTextWidth(top, 11.5),
  );
  const h = lines * AREA_LINE_H;
  const ux = Math.cos(angle);
  const uy = Math.sin(angle);
  const px = x + ux * (r + 12);
  const py = y + uy * (r + 12);
  const anchor: Anchor = alone ? "middle" : ux > 0.35 ? "start" : ux < -0.35 ? "end" : "middle";
  const x0 = anchor === "start" ? px : anchor === "end" ? px - w : px - w / 2;
  const y0 = uy < -0.35 ? py - h : uy > 0.35 ? py : py - h / 2;
  return {
    x: r2(anchor === "start" ? x0 : anchor === "end" ? x0 + w : x0 + w / 2), y: r2(y0 + 11), anchor,
    title: key, count, top, box: { x0: r2(x0), y0: r2(y0), x1: r2(x0 + w), y1: r2(y0 + h) },
  };
}

/**
 * Settle the items: a fixed number of passes, each pulling an item toward its target, pushing
 * overlapping items apart, and pushing items out of the discs. The last pass is the discs', so
 * an item never ends inside one. Items are visited in key order, so the result does not
 * depend on the order they arrived in.
 */
function relax(pts: { x: number; y: number; tx: number; ty: number }[], areas: readonly PlacedArea[]): void {
  const apart = (): void => {
    for (let i = 0; i < pts.length; i++) {
      for (let j = i + 1; j < pts.length; j++) {
        // Distance in an ellipse as wide as a marker with its label and as tall as a text line,
        // so items stack closely but sit a label's width apart side by side.
        let dx = pts[j].x - pts[i].x;
        let dy = pts[j].y - pts[i].y;
        if (Math.hypot(dx, dy) < 1e-6) {   // coincident items part along a direction fixed by their order
          dx = Math.cos(i + 2 * j) * 1e-3;
          dy = Math.sin(i + 2 * j) * 1e-3;
        }
        const q = Math.hypot(dx / SEP_X, dy / SEP_Y);
        if (q >= 1) continue;
        const px = (dx / q - dx) / 2;
        const py = (dy / q - dy) / 2;
        pts[i].x -= px;
        pts[i].y -= py;
        pts[j].x += px;
        pts[j].y += py;
      }
    }
  };
  const outOfDiscs = (): void => {
    for (const p of pts) {
      for (const a of areas) {
        const need = a.r + ITEM_CLEAR * 0.7;
        const d = Math.hypot(p.x - a.x, p.y - a.y);
        if (d >= need) continue;
        const u = d < 1e-6 ? unit(-a.x, -a.y) : { x: (p.x - a.x) / d, y: (p.y - a.y) / d };
        p.x = a.x + u.x * need;
        p.y = a.y + u.y * need;
      }
    }
  };
  for (let pass = 0; pass < RELAX_PASSES; pass++) {
    for (const p of pts) {
      p.x += (p.tx - p.x) * 0.1;
      p.y += (p.ty - p.y) * 0.1;
    }
    apart();
    outOfDiscs();
  }
  for (let i = 0; i < 6; i++) {
    apart();
    outOfDiscs();
  }
}

// ── names shown on hover ───────────────────────────────────────────────────────

export interface SkillName extends PlacedLabel {
  key: string;
  /** Share of the name's box that lands on something else; the caller may drop a very crowded one. */
  crowded: number;
}

/**
 * Where to write the names of the given skills (those an item links, an area holds, or a
 * search found), each in the free slot of eight that covers least, biggest circle first so
 * the most-cited names win the clear space. Pure and quick: it runs on every hover.
 */
export function placeSkillNames(layout: AreaMapLayout, keys: ReadonlySet<string>): SkillName[] {
  const all = layout.areas.flatMap((a) => a.skills);
  const obstacles: Obstacle[] = [
    ...layout.areas.map((a) => ({ ...circleBox(a.x, a.y, a.r), weight: 0.05 })),
    ...layout.areas.map((a) => ({ ...a.label.box, weight: 1 })),
    ...all.map((s) => ({ ...circleBox(s.x, s.y, s.r + 1), weight: 1 })),
    ...layout.items.flatMap((i) => [{ ...circleBox(i.x, i.y, MARK_R + 1), weight: 1 }, { ...i.label.box, weight: 1 }]),
  ];
  const out: SkillName[] = [];
  const todo = all.filter((s) => keys.has(s.skill.key))
    .sort((a, b) => b.r - a.r || cmpStr(a.skill.key, b.skill.key));
  for (const s of todo) {
    const w = estimateTextWidth(s.skill.name, 11.5) + 2;
    const slot = chooseLabelSlot(s.x, s.y, s.skill.name, w, s.r + 3, obstacles, true);
    obstacles.push({ ...slot.box, weight: 1 });
    out.push({ ...slot, key: s.skill.key, crowded: r2(slot.overlap / (w * LABEL_H)) });
  }
  return out;
}

// ── jumping to the evidence map ────────────────────────────────────────────────

/**
 * The fold keys that leave only `area` open in the evidence map: every other area on the
 * profile folded. This is what clicking an area on the map opens.
 */
export function foldAllBut(view: EvidenceView, area: string): Set<string> {
  return new Set(view.skills.filter((s) => s.area !== area).map((s) => areaFoldKey(s.area)));
}

/** Skill circles by key, for the component's lookups. */
export function skillPositions(layout: AreaMapLayout): Map<string, PackedSkill> {
  return new Map(layout.areas.flatMap((a) => a.skills).map((s) => [s.skill.key, s]));
}
