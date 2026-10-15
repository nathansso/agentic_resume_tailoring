/**
 * The bullet picker's logic (issue #319), as pure functions of the evidence view and a
 * job's skills. Nothing here touches the DOM, randomness or the clock, so the ranking is
 * the same on every visit and for any order the rows arrive in.
 *
 * A bullet is *relevant* to a job when it cites a profile skill that one of the job's
 * requirements names (`matchJobSkills`: by name or by any of the requirement's terms).
 * Relevant bullets are ranked greedily, each pick being the one that adds the most
 * required weight not yet covered, then the most weight of any kind not yet covered,
 * then the most job weight overall, then the most recent work, then a fixed
 * item-key/ordinal tie-break. Preferred requirements count at half their weight.
 *
 * Coverage counts are kept apart on purpose: required covered, weighted required
 * covered, and preferred covered. They are never added into one score and have nothing
 * to do with the ATS composite, which is monotone in text and reported only (repo
 * invariant, docs/harness.md § 5).
 */
import type { EvidenceItem, EvidenceSkill, EvidenceView, JobSkillRow } from "../types";
import { byRecency, endKey, matchJobSkills, type JobMatch } from "./evidenceMap";

/** How many bullets the page holds, until the user says otherwise. A bullet count, not a measure of a page. */
export const DEFAULT_BUDGET = 8;
export const MIN_BUDGET = 1;
export const MAX_BUDGET = 30;

/** A preferred requirement is worth this share of its weight when picking. */
export const PREFERRED_FACTOR = 0.5;

const cmpStr = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
const cmpNum = (a: number, b: number): number => (a < b ? -1 : a > b ? 1 : 0);
/** Weights are sums of a few short decimals; compare them without caring about float dust. */
const round = (n: number): number => Math.round(n * 1e6) / 1e6;

export const clampBudget = (n: number): number => Math.max(MIN_BUDGET, Math.min(MAX_BUDGET, Math.round(n)));

export interface PickerBullet {
  /** `<item key>#b<n>`: stable across re-ranking, and across a refetch while the bullet's place holds. */
  id: string;
  item: EvidenceItem;
  /** The harness ordinal (`#b<n>`). */
  ordinal: number;
  text: string;
  /** Profile skills this bullet cites, by name. */
  skills: EvidenceSkill[];
  /** Ids of the job requirements (`JobMatch.id`) those skills satisfy, required first. */
  covers: string[];
  /** `[start, end)` offsets of the words in `text` that name a skill the job asks for. */
  marks: [number, number][];
}

export interface RankedBullet extends PickerBullet {
  /** 1-based place in the greedy order. */
  rank: number;
  /** Requirement ids this pick covers that no earlier pick did: what "adds ..." lists. */
  gain: string[];
}

export interface PickerModel {
  matches: JobMatch[];
  /** Relevant bullets in greedy order. */
  ranked: RankedBullet[];
  /** Bullets that cite no skill the job names, in résumé order. They can still be kept. */
  rest: PickerBullet[];
}

/** What a requirement is worth when picking: its weight, halved when only preferred. */
export const pickWeight = (m: JobMatch): number => (m.row.required ? m.row.weight : m.row.weight * PREFERRED_FACTOR);

/** Every item bullet that has text, with the skills it cites and the requirements those satisfy. */
function collectBullets(view: EvidenceView, matches: readonly JobMatch[]): PickerBullet[] {
  const skillByKey = new Map(view.skills.map((s) => [s.key, s]));
  const reqsBySkill = new Map<string, string[]>();
  for (const m of matches) for (const s of m.skills) reqsBySkill.set(s.key, [...(reqsBySkill.get(s.key) ?? []), m.id]);

  const order = new Map(matches.map((m, i) => [m.id, i]));
  const cites = new Map<string, { skill: EvidenceSkill; marks: [number, number][] }[]>();
  for (const e of view.edges) {
    const skill = skillByKey.get(e.skill_key);
    if (!skill) continue;
    e.cites.forEach((n, i) => {
      const k = `${e.item_key}#b${n}`;
      cites.set(k, [...(cites.get(k) ?? []), { skill, marks: e.marks?.[i] ?? [] }]);
    });
  }

  const out: PickerBullet[] = [];
  for (const item of view.items) {
    if (item.kind === "edu") continue;
    for (const t of item.text) {
      if (t.index === null || !t.text.trim()) continue;
      const id = `${item.key}#b${t.index}`;
      const cited = (cites.get(id) ?? []).sort((a, b) => cmpStr(a.skill.name, b.skill.name) || cmpStr(a.skill.key, b.skill.key));
      const covers = new Set<string>();
      const marks: [number, number][] = [];
      for (const c of cited) {
        const reqs = reqsBySkill.get(c.skill.key);
        if (!reqs) continue;
        reqs.forEach((r) => covers.add(r));
        marks.push(...c.marks);
      }
      out.push({
        id, item, ordinal: t.index, text: t.text, skills: cited.map((c) => c.skill),
        covers: [...covers].sort((a, b) => cmpNum(order.get(a) ?? 0, order.get(b) ?? 0)), marks,
      });
    }
  }
  // A fixed starting order, so ties below never depend on the order the payload listed items in.
  return out.sort((a, b) => cmpStr(a.item.key, b.item.key) || cmpNum(a.ordinal, b.ordinal));
}

/** Rank a profile's bullets for one job. */
export function rankBullets(view: EvidenceView, jobSkills: readonly JobSkillRow[]): PickerModel {
  const matches = matchJobSkills(view, jobSkills);
  const weight = new Map(matches.map((m) => [m.id, pickWeight(m)]));
  const isRequired = new Map(matches.map((m) => [m.id, m.row.required]));
  const all = collectBullets(view, matches);

  const pool = all.filter((b) => b.covers.length > 0);
  const rest = all.filter((b) => b.covers.length === 0).sort(inResumeOrder);
  const covered = new Set<string>();
  const ranked: RankedBullet[] = [];

  const score = (b: PickerBullet) => {
    let newRequired = 0;
    let newAny = 0;
    let total = 0;
    for (const r of b.covers) {
      const w = weight.get(r) ?? 0;
      total += w;
      if (covered.has(r)) continue;
      newAny += w;
      if (isRequired.get(r)) newRequired += w;
    }
    return [round(newRequired), round(newAny), round(total), endKey(b.item)];
  };

  while (pool.length > 0) {
    let best = 0;
    let bestScore = score(pool[0]);
    for (let i = 1; i < pool.length; i++) {
      const s = score(pool[i]);
      for (let k = 0; k < s.length; k++) {
        if (s[k] === bestScore[k]) continue;
        if (s[k] > bestScore[k]) { best = i; bestScore = s; }
        break;
      }
    }
    // `pool` stays in (item key, ordinal) order, so an exact tie keeps the earlier one.
    const [pick] = pool.splice(best, 1);
    const gain = pick.covers.filter((r) => !covered.has(r));
    pick.covers.forEach((r) => covered.add(r));
    ranked.push({ ...pick, rank: ranked.length + 1, gain });
  }
  return { matches, ranked, rest };
}

/** Most recent work first, then the bullet's place in its item. */
function inResumeOrder(a: PickerBullet, b: PickerBullet): number {
  return byRecency(a.item, b.item) || cmpNum(a.ordinal, b.ordinal);
}

// ── the kept set ───────────────────────────────────────────────────────────────

/**
 * What is kept: the user's own ticks when they have made any (`override`), else the top
 * `budget` of the ranking. Ids that no longer exist (an edit removed the bullet) drop out.
 */
export function resolveKept(model: PickerModel, budget: number, override: ReadonlySet<string> | null): Set<string> {
  const known = new Set([...model.ranked, ...model.rest].map((b) => b.id));
  if (override) return new Set([...override].filter((id) => known.has(id)));
  return new Set(model.ranked.slice(0, clampBudget(budget)).map((b) => b.id));
}

/** The override after ticking or unticking `id` from whatever is kept now. */
export function toggleKept(kept: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(kept);
  if (next.has(id)) next.delete(id); else next.add(id);
  return next;
}

// ── coverage ───────────────────────────────────────────────────────────────────

/**
 * covered:   a kept bullet backs it.
 * coverable: some bullet cites it, but none is kept.
 * claimed:   the skill is on the profile and no bullet cites it, so no bullet can cover it.
 * missing:   not on the profile at all.
 */
export type CoverStatus = "covered" | "coverable" | "claimed" | "missing";

export interface CoverageRow {
  match: JobMatch;
  /** What to call it: the profile's own spelling when it has the skill, else the job's. */
  label: string;
  status: CoverStatus;
  /** Ranked and unranked bullets that cite it, as ids, in ranking order. */
  citing: string[];
  /** Those of them that are kept. */
  keptCiting: string[];
}

export interface CoverageCounts {
  total: number;
  covered: number;
  /** Sum of the weights of the requirements, and of those covered. Whole weights, never mixed with the other group. */
  weightTotal: number;
  weightCovered: number;
}

export interface Coverage {
  rows: CoverageRow[];
  required: CoverageCounts;
  preferred: CoverageCounts;
  /** Required skills no bullet can cover, named in the picker. */
  claimed: CoverageRow[];
  missing: CoverageRow[];
}

export function coverage(model: PickerModel, kept: ReadonlySet<string>): Coverage {
  const rows: CoverageRow[] = model.matches.map((match) => {
    const citing = model.ranked.filter((b) => b.covers.includes(match.id)).map((b) => b.id);
    // A bullet outside the ranking cites none of the job's skills, so it covers nothing; kept ones only matter here.
    const keptCiting = citing.filter((id) => kept.has(id));
    const status: CoverStatus = keptCiting.length > 0 ? "covered"
      : citing.length > 0 ? "coverable"
        : match.skills.length > 0 ? "claimed" : "missing";
    return { match, label: match.skills[0]?.name ?? match.row.name, status, citing, keptCiting };
  });
  const counts = (required: boolean): CoverageCounts => {
    const group = rows.filter((r) => r.match.row.required === required);
    const done = group.filter((r) => r.status === "covered");
    const sum = (rs: CoverageRow[]) => round(rs.reduce((t, r) => t + r.match.row.weight, 0));
    return { total: group.length, covered: done.length, weightTotal: sum(group), weightCovered: sum(done) };
  };
  const requiredRows = rows.filter((r) => r.match.row.required);
  return {
    rows, required: counts(true), preferred: counts(false),
    claimed: requiredRows.filter((r) => r.status === "claimed"),
    missing: requiredRows.filter((r) => r.status === "missing"),
  };
}

// ── the résumé preview ─────────────────────────────────────────────────────────

export interface PreviewItem {
  item: EvidenceItem;
  bullets: PickerBullet[];
}

export interface PreviewSection {
  title: "Experience" | "Projects";
  items: PreviewItem[];
}

/**
 * The kept bullets as a résumé: Experience then Projects (education has no bullets), each
 * newest first, each item's bullets in the order they are written.
 */
export function resumePreview(model: PickerModel, kept: ReadonlySet<string>): PreviewSection[] {
  const byItem = new Map<string, PreviewItem>();
  for (const b of [...model.ranked, ...model.rest]) {
    if (!kept.has(b.id)) continue;
    const entry = byItem.get(b.item.key) ?? { item: b.item, bullets: [] };
    entry.bullets.push(b);
    byItem.set(b.item.key, entry);
  }
  const items = [...byItem.values()].sort((a, b) => byRecency(a.item, b.item));
  items.forEach((i) => i.bullets.sort((a, b) => cmpNum(a.ordinal, b.ordinal)));
  const sections: PreviewSection[] = [
    { title: "Experience", items: items.filter((i) => i.item.kind === "exp") },
    { title: "Projects", items: items.filter((i) => i.item.kind === "proj") },
  ];
  return sections.filter((s) => s.items.length > 0);
}
