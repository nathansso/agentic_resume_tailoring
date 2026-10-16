import { describe, expect, it } from "vitest";
import type { EvidenceEdge, EvidenceItem, EvidenceKind, EvidenceSkill, EvidenceView } from "../types";
import { areaFoldKey } from "./evidenceMap";
import {
  areaItemWeights, chooseLabelSlot, enclose, foldAllBut, labelSlots, layoutAreaMap, packCircles, placeSkillNames,
  ringOrder, ringScore, sharedEvidence, skillPositions, type Box, type Obstacle,
} from "./areaMap";

// ── fixtures ───────────────────────────────────────────────────────────────────

function item(key: string, kind: EvidenceKind, o: Partial<EvidenceItem> = {}): EvidenceItem {
  return {
    key, id: key.split("/")[1] ?? key, kind, title: key, org: "", start: "", end: "", description: "",
    text: [{ raw_index: 0, index: 0, text: "did a thing", blurb_id: null }], text_source: kind === "edu" ? "fields" : "bullets",
    cite_key: "", parent_key: null, achievements: [], repo_url: "", fields: {}, ...o,
  };
}

function skill(name: string, area: string): EvidenceSkill {
  return { key: `skill/${name.toLowerCase()}`, name, category: "", area, proficiency: 3, sources: ["resume"], is_core: false };
}

function edge(itemKey: string, s: EvidenceSkill, cites: number[] = [0], github = false): EvidenceEdge {
  return { item_key: itemKey, skill_key: s.key, cites, github, marks: cites.map(() => [[0, 3]]) };
}

function view(items: EvidenceItem[], skills: EvidenceSkill[], edges: EvidenceEdge[]): EvidenceView {
  const cited = new Set(edges.filter((e) => e.cites.length).map((e) => e.skill_key));
  return {
    items, skills, edges,
    stats: {
      items: items.length, skills: skills.length, evidenced: cited.size, github_only: 0,
      unevidenced: skills.length - cited.size, needs_text: 0,
    },
  };
}

const AREAS = ["Languages", "ML & AI", "Data", "Infrastructure & Cloud", "Web & Apps", "Tools"];

/**
 * A profile with six areas of five to eight skills each, an item per area that cites only that
 * area, three that span two or three, one that cites nothing, and an education row.
 */
function profile(): EvidenceView {
  const skills: EvidenceSkill[] = [];
  AREAS.forEach((area, a) => {
    for (let i = 0; i < 5 + ((a * 2) % 4); i++) skills.push(skill(`${area.split(" ")[0]}${i}`, area));
  });
  const byArea = (area: string) => skills.filter((s) => s.area === area);
  const items: EvidenceItem[] = [];
  const edges: EvidenceEdge[] = [];
  AREAS.forEach((area, a) => {
    for (let k = 0; k < 2; k++) {
      const it = item(`proj/${area.split(" ")[0].toLowerCase()}${k}`, "proj", { title: `${area} project ${k}` });
      items.push(it);
      byArea(area).slice(k, k + 3).forEach((s, n) => edges.push(edge(it.key, s, Array.from({ length: 1 + ((a + n) % 3) }, (_, c) => c))));
    }
  });
  const exp = item("exp/acme", "exp", { title: "Engineer", org: "Acme" });
  const span = item("proj/span", "proj", { title: "Spanning project" });
  const wide = item("exp/wide", "exp", { title: "Generalist", org: "Wide Co" });
  items.push(exp, span, wide, item("proj/empty", "proj", { title: "Empty", text: [], text_source: "none" }),
    item("edu/ucsd", "edu", { title: "B.S.", org: "UCSD" }));
  edges.push(edge("exp/acme", byArea("Languages")[0]), edge("exp/acme", byArea("Data")[1], [0, 1]));
  edges.push(edge("proj/span", byArea("ML & AI")[0]), edge("proj/span", byArea("Infrastructure & Cloud")[0], [], true));
  for (const area of AREAS) edges.push(edge("exp/wide", byArea(area)[2] ?? byArea(area)[0]));
  return view(items, skills, edges);
}

/** A fixed, repeatable shuffle (a linear congruential generator), so the test needs no randomness. */
function shuffled<T>(xs: readonly T[], seed: number): T[] {
  const out = [...xs];
  let s = seed;
  for (let i = out.length - 1; i > 0; i--) {
    s = (s * 1664525 + 1013904223) % 4294967296;
    const j = s % (i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

const dist = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
const overlaps = (a: Box, b: Box) => Math.min(a.x1, b.x1) > Math.max(a.x0, b.x0) && Math.min(a.y1, b.y1) > Math.max(a.y0, b.y0);

// ── packing ────────────────────────────────────────────────────────────────────

describe("packCircles", () => {
  const radii = [14, 11, 11, 9, 8, 8, 6, 6, 5, 5, 5, 4.2, 4.2, 4.2, 4.2];

  it("puts the first circle at the origin and overlaps none", () => {
    const at = packCircles(radii);
    expect(at[0]).toEqual({ x: 0, y: 0 });
    for (let i = 0; i < radii.length; i++) {
      for (let j = i + 1; j < radii.length; j++) {
        expect(dist(at[i], at[j])).toBeGreaterThanOrEqual(radii[i] + radii[j]);
      }
    }
  });

  it("is a pure function of the radii", () => {
    expect(packCircles(radii)).toEqual(packCircles(radii));
  });

  it("is compact: the cluster is not much wider than its circles' total area allows", () => {
    const at = packCircles(radii);
    const box = enclose(radii.map((r, i) => ({ ...at[i], r })));
    const area = radii.reduce((t, r) => t + Math.PI * r * r, 0);
    expect(Math.PI * box.r * box.r).toBeLessThan(area * 3.2);
  });
});

describe("enclose", () => {
  it("contains every circle", () => {
    const cs = [{ x: 0, y: 0, r: 10 }, { x: 30, y: 4, r: 6 }, { x: -12, y: 25, r: 8 }, { x: 5, y: -22, r: 5 }];
    const e = enclose(cs);
    for (const c of cs) expect(dist(e, c) + c.r).toBeLessThanOrEqual(e.r + 1e-9);
  });

  it("is empty for nothing", () => {
    expect(enclose([])).toEqual({ x: 0, y: 0, r: 0 });
  });
});

// ── the ring ───────────────────────────────────────────────────────────────────

describe("ringOrder", () => {
  const w = (rows: Record<string, Record<string, number>>) =>
    new Map(Object.entries(rows).map(([a, m]) => [a, new Map(Object.entries(m))]));
  // Languages and Data share an item (5), ML & AI and Infrastructure one (4), Web and Tools one (3).
  const weights = w({
    Languages: { x: 5 }, Data: { x: 5 }, "ML & AI": { y: 4 }, "Infrastructure & Cloud": { y: 4 },
    "Web & Apps": { z: 3 }, Tools: { z: 3 },
  });

  it("scores shared evidence as the smaller weight per item", () => {
    expect(sharedEvidence(weights, "Languages", "Data")).toBe(5);
    expect(sharedEvidence(weights, "Languages", "Tools")).toBe(0);
    expect(sharedEvidence(w({ A: { i: 2, j: 1 }, B: { i: 5, j: 4 } }), "A", "B")).toBe(3);
  });

  it("puts the areas that share the most next to each other, and no other order scores higher", () => {
    const order = ringOrder(AREAS, weights);
    expect(ringScore(order, weights)).toBe(12);
    const next = (a: string, b: string) => {
      const d = Math.abs(order.indexOf(a) - order.indexOf(b));
      return d === 1 || d === order.length - 1;
    };
    expect(next("Languages", "Data")).toBe(true);
    expect(next("ML & AI", "Infrastructure & Cloud")).toBe(true);
    expect(next("Web & Apps", "Tools")).toBe(true);
    // The fixed order would have scored 3.
    expect(ringScore([...AREAS], weights)).toBe(3);
  });

  it("is the same whatever order the areas arrive in", () => {
    const want = ringOrder(AREAS, weights);
    for (let seed = 1; seed <= 6; seed++) expect(ringOrder(shuffled(AREAS, seed), weights)).toEqual(want);
  });

  it("keeps the fixed order when nothing is shared", () => {
    expect(ringOrder(shuffled(AREAS, 3), new Map())).toEqual(AREAS);
  });

  it("derives the weights from the edges: a GitHub-only link counts half a bullet", () => {
    const a = skill("A", "Data");
    const b = skill("B", "Tools");
    const v = view([item("proj/p", "proj")], [a, b], [edge("proj/p", a, [0, 1, 2]), edge("proj/p", b, [], true)]);
    const m = areaItemWeights(v);
    expect(m.get("Data")?.get("proj/p")).toBe(3);
    expect(m.get("Tools")?.get("proj/p")).toBe(0.5);
  });
});

// ── the layout ─────────────────────────────────────────────────────────────────

describe("layoutAreaMap", () => {
  const v = profile();
  const layout = layoutAreaMap(v);

  it("places one disc per area that has a skill, on a ring", () => {
    expect(layout.areas.map((a) => a.key).sort()).toEqual([...AREAS].sort());
    for (const a of layout.areas) expect(Math.hypot(a.x, a.y)).toBeCloseTo(layout.ringR, 0);
  });

  it("is deterministic, whatever order the rows arrive in", () => {
    const want = JSON.stringify(layout);
    expect(JSON.stringify(layoutAreaMap(v))).toBe(want);
    for (let seed = 1; seed <= 4; seed++) {
      const mixed = view(shuffled(v.items, seed), shuffled(v.skills, seed + 10), shuffled(v.edges, seed + 20));
      expect(JSON.stringify(layoutAreaMap(mixed))).toBe(want);
    }
  });

  it("never overlaps two skill circles in an area, and keeps every one inside its disc", () => {
    for (const a of layout.areas) {
      expect(a.skills.length).toBe(a.total);
      for (const s of a.skills) expect(dist(s, a) + s.r).toBeLessThanOrEqual(a.r + 1e-6);
      for (let i = 0; i < a.skills.length; i++) {
        for (let j = i + 1; j < a.skills.length; j++) {
          expect(dist(a.skills[i], a.skills[j])).toBeGreaterThanOrEqual(a.skills[i].r + a.skills[j].r);
        }
      }
    }
  });

  it("keeps the discs apart and the items out of them", () => {
    for (let i = 0; i < layout.areas.length; i++) {
      for (let j = i + 1; j < layout.areas.length; j++) {
        expect(dist(layout.areas[i], layout.areas[j])).toBeGreaterThan(layout.areas[i].r + layout.areas[j].r);
      }
      for (const it of layout.items) expect(dist(it, layout.areas[i])).toBeGreaterThanOrEqual(layout.areas[i].r);
    }
    for (let i = 0; i < layout.items.length; i++) {
      for (let j = i + 1; j < layout.items.length; j++) expect(dist(layout.items[i], layout.items[j])).toBeGreaterThan(16);
    }
  });

  it("sizes a skill by its citing bullets and gives an uncited one a visible circle", () => {
    const circles = layout.areas.flatMap((a) => a.skills);
    const most = circles.reduce((m, s) => (s.stat.bullets > m.stat.bullets ? s : m));
    const none = circles.find((s) => s.stat.bullets === 0);
    expect(most.r).toBeGreaterThan(5.5);
    expect(none?.r).toBeGreaterThanOrEqual(4);
    for (const s of circles.filter((c) => c.stat.bullets === 0)) expect(s.stat.cited).toBe(false);
  });

  it("puts an item linked to one area closer to that area than to any other", () => {
    const single = layout.items.filter((i) => i.areas.length === 1);
    expect(single.length).toBeGreaterThanOrEqual(AREAS.length * 2);
    for (const it of single) {
      const own = layout.areas.find((a) => a.key === it.areas[0].area) as (typeof layout.areas)[number];
      for (const other of layout.areas.filter((a) => a !== own)) expect(dist(it, own)).toBeLessThan(dist(it, other));
    }
  });

  it("puts work that spans areas nearer the middle than work in one", () => {
    const wide = layout.items.find((i) => i.item.key === "exp/wide") as (typeof layout.items)[number];
    const one = layout.items.filter((i) => i.areas.length === 1);
    expect(Math.hypot(wide.x, wide.y)).toBeLessThan(Math.min(...one.map((i) => Math.hypot(i.x, i.y))));
  });

  it("puts an item between the two areas it cites, in proportion to its bullets", () => {
    const acme = layout.items.find((i) => i.item.key === "exp/acme") as (typeof layout.items)[number];
    const [lang, data] = ["Languages", "Data"].map((k) => layout.areas.find((a) => a.key === k) as (typeof layout.areas)[number]);
    expect(dist(acme, data)).toBeLessThan(dist(acme, lang));          // 2 bullets in Data, 1 in Languages
    expect(dist(acme, { x: 0, y: 0 })).toBeLessThan(layout.ringR);
  });

  it("lists items nothing links, and leaves education out", () => {
    expect(layout.loose.map((i) => i.key)).toEqual(["proj/empty"]);
    expect(layout.items.some((i) => i.item.key === "proj/empty" || i.item.kind === "edu")).toBe(false);
  });

  it("draws one edge per placed link, dashed when no bullet cites it", () => {
    expect(layout.edges.length).toBe(v.edges.length);
    expect(layout.edges.find((e) => e.itemKey === "proj/span" && e.dashed)?.cites).toBe(0);
    expect(layout.edges.every((e) => e.d.startsWith("M") && e.width > 0)).toBe(true);
  });

  it("fits everything inside its view box", () => {
    const { viewBox: b } = layout;
    for (const a of layout.areas) {
      expect(a.x - a.r).toBeGreaterThanOrEqual(b.x);
      expect(a.x + a.r).toBeLessThanOrEqual(b.x + b.w);
      expect(a.label.box.x0).toBeGreaterThanOrEqual(b.x);
      expect(a.label.box.x1).toBeLessThanOrEqual(b.x + b.w);
      expect(a.label.box.y0).toBeGreaterThanOrEqual(b.y);
      expect(a.label.box.y1).toBeLessThanOrEqual(b.y + b.h);
    }
    for (const i of layout.items) {
      expect(i.label.box.x0).toBeGreaterThanOrEqual(b.x);
      expect(i.label.box.x1).toBeLessThanOrEqual(b.x + b.w);
    }
  });

  it("keeps item labels off each other, off the skill circles and off the area labels", () => {
    const boxes = layout.items.map((i) => i.label.box);
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) expect(overlaps(boxes[i], boxes[j])).toBe(false);
      for (const a of layout.areas) expect(overlaps(boxes[i], a.label.box)).toBe(false);
      for (const s of layout.areas.flatMap((a) => a.skills)) {
        expect(overlaps(boxes[i], { x0: s.x - s.r, y0: s.y - s.r, x1: s.x + s.r, y1: s.y + s.r })).toBe(false);
      }
    }
  });

  it("handles a single area, and nothing at all", () => {
    const a = skill("Python", "Languages");
    const one = layoutAreaMap(view([item("proj/p", "proj")], [a], [edge("proj/p", a)]));
    expect(one.areas).toHaveLength(1);
    expect(one.areas[0].label.anchor).toBe("middle");
    expect(one.items).toHaveLength(1);
    const empty = layoutAreaMap(view([], [], []));
    expect(empty.areas).toEqual([]);
    expect(empty.viewBox.w).toBeGreaterThan(0);
  });

  it("names an area's best cited skills, most-cited first", () => {
    const lang = layout.areas.find((a) => a.key === "Languages") as (typeof layout.areas)[number];
    const cited = lang.skills.filter((s) => s.stat.cited).sort((a, b) => b.stat.bullets - a.stat.bullets);
    expect(lang.label.top.split(" · ")[0]).toBe(cited[0].skill.name);
    expect(lang.label.count).toBe(`${lang.evidenced}/${lang.total} evidenced`);
  });
});

// ── labels ─────────────────────────────────────────────────────────────────────

describe("chooseLabelSlot", () => {
  const block = (b: Box): Obstacle => ({ ...b, weight: 1 });

  it("takes the right slot when it is clear", () => {
    const got = chooseLabelSlot(100, 100, "Acme", 40, 10, []);
    expect(got.anchor).toBe("start");
    expect(got.x).toBe(110);
    expect(got.overlap).toBe(0);
  });

  it("moves to a slot that is clear when the preferred one is covered", () => {
    const [right, left] = labelSlots(100, 100, 40, 10);
    const got = chooseLabelSlot(100, 100, "Acme", 40, 10, [block(right.box)]);
    expect(got.anchor).toBe("end");
    expect(got.box).toEqual(left.box);
    expect(got.overlap).toBe(0);
  });

  it("goes below, then above, as the sides fill", () => {
    const slots = labelSlots(100, 100, 40, 10);
    const two = chooseLabelSlot(100, 100, "Acme", 40, 10, slots.slice(0, 2).map((s) => block(s.box)));
    expect(two.box).toEqual(slots[2].box);
    const three = chooseLabelSlot(100, 100, "Acme", 40, 10, slots.slice(0, 3).map((s) => block(s.box)));
    expect(three.box).toEqual(slots[3].box);
  });

  it("when every slot is covered, takes the one that covers least and says how much", () => {
    const slots = labelSlots(100, 100, 40, 10);
    const walls = slots.map((s, i) => block(i === 2 ? { ...s.box, x1: s.box.x0 + 5 } : s.box));
    const got = chooseLabelSlot(100, 100, "Acme", 40, 10, walls);
    expect(got.box).toEqual(slots[2].box);
    expect(got.overlap).toBeGreaterThan(0);
  });

  it("weighs a soft obstacle less than a hard one", () => {
    const slots = labelSlots(100, 100, 40, 10);
    const walls = slots.map((s, i) => ({ ...s.box, weight: i === 0 ? 0.25 : 1 }));
    const got = chooseLabelSlot(100, 100, "Acme", 40, 10, walls);
    expect(got.box).toEqual(slots[0].box);
  });
});

describe("placeSkillNames", () => {
  const v = profile();
  const layout = layoutAreaMap(v);

  it("places a name for each skill asked for, and none for the rest", () => {
    const keys = new Set(layout.areas[0].skills.slice(0, 3).map((s) => s.skill.key));
    const names = placeSkillNames(layout, keys);
    expect(names.map((n) => n.key).sort()).toEqual([...keys].sort());
    expect(placeSkillNames(layout, new Set())).toEqual([]);
  });

  it("keeps names in different areas off each other", () => {
    // One skill from each area: they are far apart, so their names have no reason to meet.
    const keys = new Set(v.edges.filter((e) => e.item_key === "exp/wide").map((e) => e.skill_key));
    const names = placeSkillNames(layout, keys);
    expect(names).toHaveLength(AREAS.length);
    for (let i = 0; i < names.length; i++) {
      for (let j = i + 1; j < names.length; j++) expect(overlaps(names[i].box, names[j].box)).toBe(false);
    }
  });

  it("is the same on every call", () => {
    const keys = new Set(layout.areas[1].skills.map((s) => s.skill.key));
    expect(placeSkillNames(layout, keys)).toEqual(placeSkillNames(layout, keys));
  });
});

describe("foldAllBut and skillPositions", () => {
  const v = profile();

  it("folds every area but the one opened", () => {
    const folded = foldAllBut(v, "Data");
    expect(folded.has(areaFoldKey("Data"))).toBe(false);
    expect(folded.size).toBe(AREAS.length - 1);
    expect(AREAS.filter((a) => a !== "Data").every((a) => folded.has(areaFoldKey(a)))).toBe(true);
  });

  it("finds a skill's circle by key", () => {
    const layout = layoutAreaMap(v);
    const at = skillPositions(layout);
    expect(at.size).toBe(v.skills.length);
    expect(at.get(v.skills[0].key)?.skill.name).toBe(v.skills[0].name);
  });
});
