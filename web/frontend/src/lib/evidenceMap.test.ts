import { describe, expect, it } from "vitest";
import type {
  EvidenceEdge, EvidenceItem, EvidenceKind, EvidenceSkill, EvidenceText, EvidenceView, JobSkillRow, ProjectContext,
} from "../types";
import {
  DISMISSED_KEY, canDismiss, isPanelKey, needsPlacement, placementLabel, placementOptions, projectsToPlace, skillIdOf, topSuggestion,
  arcPath, areaFoldKey, dateKey, edgeWidth, editableFields, estimateTextWidth, evidenceRows, firstMatch, fitText,
  ghostBlock, githubOnlyLinks, highlightSegments, initialDraft, jobOverlay, layoutMap, linkPath,
  linksByBullet, neighborhood, planSave, searchSkills, skillEvidence, skillMatchKey, skillRadius,
  sourceArcs, weightedMedian, type LayoutOptions,
} from "./evidenceMap";

// ── fixtures ───────────────────────────────────────────────────────────────────

function text(...bullets: string[]): EvidenceText[] {
  let n = 0;
  return bullets.map((t, raw_index) => ({ raw_index, index: t ? n++ : null, text: t, blurb_id: null }));
}

function item(key: string, kind: EvidenceKind, o: Partial<EvidenceItem> = {}): EvidenceItem {
  const bullets = o.text ?? text("did a thing");
  return {
    key, id: key.split("/")[1] ?? key, kind, title: key, org: "", start: "", end: "", description: "",
    text: bullets, text_source: kind === "edu" ? "fields" : bullets.length ? "bullets" : "none",
    cite_key: "", parent_key: null, achievements: [], repo_url: "", fields: {}, ...o,
  };
}

function skill(name: string, area = "Languages", o: Partial<EvidenceSkill> = {}): EvidenceSkill {
  return {
    key: `skill/${name.toLowerCase()}`, name, category: "", area, proficiency: 3, sources: ["resume"],
    is_core: false, ...o,
  };
}

function edge(itemKey: string, s: EvidenceSkill, cites: number[] = [0], github = false): EvidenceEdge {
  return { item_key: itemKey, skill_key: s.key, cites, github, marks: cites.map(() => [[0, 3]]) };
}

const OPTS: LayoutOptions = { width: 900, folded: new Set(), query: "" };

function view(items: EvidenceItem[], skills: EvidenceSkill[], edges: EvidenceEdge[]): EvidenceView {
  const cited = new Set(edges.filter((e) => e.cites.length).map((e) => e.skill_key));
  const gh = new Set(edges.filter((e) => e.github && !e.cites.length).map((e) => e.skill_key));
  return {
    items, skills, edges,
    stats: {
      items: items.length, skills: skills.length, evidenced: cited.size, github_only: gh.size,
      unevidenced: skills.length - cited.size - gh.size, needs_text: items.filter((i) => i.text_source === "none").length,
    },
  };
}

const py = skill("Python");
const ts = skill("TypeScript");
const torch = skill("PyTorch", "ML & AI");
const sql = skill("SQL", "Data");
const kafka = skill("Kafka", "Data");
const docker = skill("Docker", "Tools");

const acme = item("exp/acme", "exp", { title: "Engineer", org: "Acme", start: "2023-06", end: "2024-09" });
const intern = item("exp/intern", "exp", { title: "Intern", org: "Beta", start: "2022-06", end: "2022-09" });
const school = item("edu/ucsd", "edu", { title: "B.S.", org: "UCSD", start: "2020", end: "Expected June 2027" });
const alpha = item("proj/alpha", "proj", { title: "Alpha", parent_key: "exp/acme", end: "2024-01" });
const beta = item("proj/beta", "proj", { title: "Beta", end: "2025-02" });
const bare = item("proj/bare", "proj", { title: "Bare", text: [], end: "2025-05" });

const ITEMS = [acme, intern, school, alpha, beta, bare];
const SKILLS = [py, ts, torch, sql, kafka, docker];
const EDGES = [
  edge("exp/acme", py, [0, 2]), edge("exp/acme", sql, [1]), edge("proj/alpha", py, [0]),
  edge("proj/alpha", torch, [0, 1, 2]), edge("proj/beta", torch, [0]), edge("exp/intern", sql, [0]),
  edge("proj/beta", ts, [], true),
];
const VIEW = view(ITEMS, SKILLS, EDGES);

// ── tests ──────────────────────────────────────────────────────────────────────

describe("dateKey", () => {
  it("reads the date shapes resumes carry", () => {
    expect(dateKey("2024-07")).toBe(2024 * 12 + 6);
    expect(dateKey("Jun 2023")).toBe(2023 * 12 + 5);
    expect(dateKey("June 2025")).toBe(2025 * 12 + 5);
    expect(dateKey("Expected June 2027")).toBe(2027 * 12 + 5);
    expect(dateKey("Sep. 5, 2021")).toBe(2021 * 12 + 8);
    expect(dateKey("2027")).toBe(2027 * 12 + 6);
    expect(dateKey("Summer 2024")).toBe(2024 * 12 + 6);
  });
  it("puts Present last-ended and unreadable dates nowhere", () => {
    expect(dateKey("Present")).toBeGreaterThan(dateKey("Dec 2099") as number);
    expect(dateKey("current")).toBe(dateKey("Present"));
    for (const bad of ["", "?", "—", "soon", null, undefined]) expect(dateKey(bad)).toBeNull();
  });
});

describe("evidenceRows", () => {
  it("lists roles and study by end date, nests projects, then standalone, then items with no text", () => {
    const { rows, fixStart } = evidenceRows(ITEMS);
    expect(rows.map((r) => [r.item.key, r.indent, r.fix])).toEqual([
      ["edu/ucsd", 0, false],     // Expected June 2027 ends last
      ["exp/acme", 0, false],
      ["proj/alpha", 1, false],   // nested under Acme
      ["exp/intern", 0, false],
      ["proj/beta", 0, false],    // standalone
      ["proj/bare", 0, true],     // nothing to link from
    ]);
    expect(fixStart).toBe(5);
  });

  it("treats a project whose parent is not listed as standalone, and never files education under 'needs text'", () => {
    const orphan = item("proj/orphan", "proj", { parent_key: "exp/gone", end: "2026-01" });
    const { rows } = evidenceRows([orphan, item("edu/x", "edu", { text: [], text_source: "fields" })]);
    expect(rows.map((r) => [r.item.key, r.indent, r.fix])).toEqual([["edu/x", 0, false], ["proj/orphan", 0, false]]);
  });

  it("is a total order: ties on every date fall back to title, then key", () => {
    const a = item("proj/b", "proj", { title: "Same", end: "2024-01" });
    const b = item("proj/a", "proj", { title: "Same", end: "2024-01" });
    expect(evidenceRows([a, b]).rows.map((r) => r.item.key)).toEqual(["proj/a", "proj/b"]);
    expect(evidenceRows([b, a]).rows.map((r) => r.item.key)).toEqual(["proj/a", "proj/b"]);
  });
});

describe("weightedMedian", () => {
  it("is the point where half the weight lies at or below", () => {
    expect(weightedMedian([{ y: 0, w: 1 }, { y: 10, w: 1 }, { y: 20, w: 1 }])).toBe(10);
    expect(weightedMedian([{ y: 0, w: 5 }, { y: 10, w: 1 }, { y: 20, w: 1 }])).toBe(0);
    expect(weightedMedian([])).toBe(Infinity);
  });
});

describe("layoutMap", () => {
  const layout = layoutMap(VIEW, OPTS);

  it("is deterministic: the same data lays out identically, and the order rows arrive in does not matter", () => {
    expect(JSON.stringify(layoutMap(VIEW, OPTS))).toBe(JSON.stringify(layout));
    const shuffled = view([...ITEMS].reverse(), [...SKILLS].reverse(), [...EDGES].reverse());
    expect(JSON.stringify(layoutMap(shuffled, OPTS))).toBe(JSON.stringify(layout));
    const rotated = view([...ITEMS.slice(2), ...ITEMS.slice(0, 2)], [...SKILLS.slice(3), ...SKILLS.slice(0, 3)],
      [...EDGES.slice(4), ...EDGES.slice(0, 4)]);
    expect(JSON.stringify(layoutMap(rotated, OPTS))).toBe(JSON.stringify(layout));
  });

  it("places one row per skill however many items cite it: two projects share the single Python row", () => {
    const pythons = layout.areas.flatMap((a) => a.skills).filter((s) => s.skill.name === "PyTorch");
    expect(pythons).toHaveLength(1);
    const into = layout.edges.filter((e) => e.skillKey === torch.key);
    expect(into.map((e) => e.itemKey).sort()).toEqual(["proj/alpha", "proj/beta"]);
    // both curves end at the same point
    expect(new Set(into.map((e) => e.d.split(",").slice(-2).join(","))).size).toBe(1);
  });

  it("orders areas by the median of the rows they link to, and skills within an area the same way", () => {
    // Rows: edu0 acme1 alpha2 intern3 beta4. Languages median 1, Data 1 (tie: the fixed area order), ML & AI 2, Tools none.
    expect(layout.areas.map((a) => a.key)).toEqual(["Languages", "Data", "ML & AI", "Tools"]);
    const languages = layout.areas[0].skills.map((s) => s.skill.name);
    expect(languages).toEqual(["Python", "TypeScript"]);   // median 1 then 4; TypeScript is GitHub-only and still a row
    expect(layout.areas[0].chips).toHaveLength(0);
  });

  it("keeps a skill with no evidence in its area as a chip, counted in the area header", () => {
    const tools = layout.areas.find((a) => a.key === "Tools");
    expect(tools?.skills).toHaveLength(0);
    expect(tools?.chips.map((c) => c.skill.name)).toEqual(["Docker"]);
    expect(tools?.total).toBe(1);
    expect(tools?.evidenced).toBe(0);
    const data = layout.areas.find((a) => a.key === "Data");
    expect([data?.total, data?.evidenced]).toEqual([2, 1]);    // SQL cited, Kafka a chip
    expect(data?.chips.map((c) => c.skill.name)).toEqual(["Kafka"]);
    expect(layout.skillOrder).toContain(kafka.key);
  });

  it("draws a link with no bullet as a dashed hairline, and a cited one wider the more bullets cite it", () => {
    const gh = layout.edges.find((e) => e.itemKey === "proj/beta" && e.skillKey === ts.key);
    expect(gh?.dashed).toBe(true);
    expect(gh?.width).toBe(1);
    const one = layout.edges.find((e) => e.itemKey === "proj/beta" && e.skillKey === torch.key);
    const three = layout.edges.find((e) => e.itemKey === "proj/alpha" && e.skillKey === torch.key);
    expect(one?.dashed).toBe(false);
    expect((three?.width ?? 0)).toBeGreaterThan(one?.width ?? 0);
    expect(three?.width).toBeCloseTo(edgeWidth(3), 1);
  });

  it("puts the items with no text under their own heading after the others", () => {
    expect(layout.rows[layout.rows.length - 1].item.key).toBe("proj/bare");
    expect(layout.fixLabelY).not.toBeNull();
    const before = layout.rows[layout.fixStart - 1].y;
    expect(layout.rows[layout.fixStart].y).toBeGreaterThan(before + layout.rowH);
  });

  it("folding an area aggregates its edges onto the header and hides its skills", () => {
    const folded = layoutMap(VIEW, { ...OPTS, folded: new Set([areaFoldKey("Data")]) });
    const data = folded.areas.find((a) => a.key === "Data");
    expect(data?.collapsed).toBe(true);
    expect(data?.skills).toHaveLength(0);
    expect(data?.chips).toHaveLength(0);
    expect(folded.edges.some((e) => e.skillKey === sql.key)).toBe(false);
    const agg = folded.edges.filter((e) => e.areaKey === "Data");
    expect(agg.map((e) => [e.itemKey, e.cites])).toEqual([["exp/acme", 1], ["exp/intern", 1]]);
    // the aggregate lands on the header, and the other areas keep their edges
    expect(agg[0].d.endsWith(`${folded.skillX - 10},${data?.hubY}`)).toBe(true);
    expect(folded.edges.filter((e) => e.skillKey === py.key)).toHaveLength(2);
    expect(folded.areas.find((a) => a.key === "ML & AI")!.y).toBeLessThan(layout.areas.find((a) => a.key === "ML & AI")!.y);
  });

  it("sums the bullets of every folded skill into one edge per item, dashed only when none is cited", () => {
    const v = view([acme], [sql, kafka], [edge("exp/acme", sql, [0, 1]), edge("exp/acme", kafka, [], true)]);
    const folded = layoutMap(v, { ...OPTS, folded: new Set([areaFoldKey("Data")]) });
    expect(folded.edges).toHaveLength(1);
    expect(folded.edges[0].cites).toBe(2);
    expect(folded.edges[0].dashed).toBe(false);
    const ghOnly = view([acme], [kafka], [edge("exp/acme", kafka, [], true)]);
    expect(layoutMap(ghOnly, { ...OPTS, folded: new Set([areaFoldKey("Data")]) }).edges[0].dashed).toBe(true);
  });

  it("a search match unfolds its area and finds the skill; an empty search matches nothing special", () => {
    const folded = new Set([areaFoldKey("Data"), areaFoldKey("ML & AI")]);
    const found = layoutMap(VIEW, { ...OPTS, folded, query: "  kaf " });
    expect(found.areas.find((a) => a.key === "Data")?.collapsed).toBe(false);
    expect(found.areas.find((a) => a.key === "ML & AI")?.collapsed).toBe(true);   // no match in it
    expect(found.matches).toEqual(new Set([kafka.key]));
    expect(firstMatch(found)).toBe(kafka.key);
    const none = layoutMap(VIEW, { ...OPTS, folded, query: "" });
    expect(none.matches).toBeNull();
    expect(firstMatch(none)).toBeNull();
    expect(none.areas.find((a) => a.key === "Data")?.collapsed).toBe(true);
  });

  it("is the same map at any width apart from the horizontal geometry", () => {
    const wide = layoutMap(VIEW, { ...OPTS, width: 1400 });
    expect(wide.areas.map((a) => a.key)).toEqual(layout.areas.map((a) => a.key));
    expect(wide.rows.map((r) => r.item.key)).toEqual(layout.rows.map((r) => r.item.key));
    expect(layoutMap(VIEW, { ...OPTS, width: 100 }).width).toBe(688);     // never narrower than it can be read
  });

  it("lays out an empty profile", () => {
    const empty = layoutMap(view([], [], []), OPTS);
    expect(empty.rows).toEqual([]);
    expect(empty.areas).toEqual([]);
    expect(empty.edges).toEqual([]);
    expect(empty.height).toBeGreaterThan(0);
  });
});

describe("searchSkills", () => {
  it("matches names by case-insensitive substring", () => {
    expect(searchSkills(VIEW, "PY")).toEqual(new Set([py.key, torch.key]));
    expect(searchSkills(VIEW, "")).toBeNull();
    expect(searchSkills(VIEW, "zzz")).toEqual(new Set());
  });
});

describe("neighborhood", () => {
  it("lights a node and what it links", () => {
    expect(neighborhood(VIEW, torch.key)).toEqual(new Set([torch.key, "proj/alpha", "proj/beta"]));
    expect(neighborhood(VIEW, "exp/acme")).toEqual(new Set(["exp/acme", py.key, sql.key]));
  });
  it("lights a whole area from its header", () => {
    expect(neighborhood(VIEW, areaFoldKey("Data"))).toEqual(
      new Set([areaFoldKey("Data"), sql.key, kafka.key, "exp/acme", "exp/intern"]));
  });
});

describe("the side panel's reads", () => {
  it("lists a skill's evidence in the map's order with each citing bullet", () => {
    const acmeWithText = item("exp/acme", "exp", { title: "Engineer", end: "2024-09", text: text("Wrote Python", "x", "More Python") });
    const alphaWithText = item("proj/alpha", "proj", { title: "Alpha", parent_key: "exp/acme", end: "2024-01", text: text("A Python tool") });
    const v = view([alphaWithText, acmeWithText], [py], [edge("proj/alpha", py, [0]), edge("exp/acme", py, [0, 2])]);
    const ev = skillEvidence(v, py.key);
    expect(ev.map((e) => e.item.key)).toEqual(["exp/acme", "proj/alpha"]);
    expect(ev[0].bullets.map((b) => [b.index, b.text])).toEqual([[0, "Wrote Python"], [2, "More Python"]]);
    expect(ev[0].bullets[0].marks).toEqual([[0, 3]]);
    expect(skillEvidence(v, "skill/none")).toEqual([]);
  });

  it("finds the skills each bullet links, by ordinal, and the GitHub-only ones apart", () => {
    const links = linksByBullet(VIEW, "proj/alpha");
    expect([...links.keys()].sort()).toEqual([0, 1, 2]);
    expect(links.get(0)?.map((s) => s.name)).toEqual(["PyTorch", "Python"]);
    expect(links.get(1)?.map((s) => s.name)).toEqual(["PyTorch"]);
    expect(githubOnlyLinks(VIEW, "proj/beta").map((s) => s.name)).toEqual(["TypeScript"]);
    expect(githubOnlyLinks(VIEW, "proj/alpha")).toEqual([]);
  });
});

describe("dismissing a skill nothing proves (#321)", () => {
  it("offers it for a skill with no link, or only a GitHub link, and not for a cited one", () => {
    // VIEW: Python, SQL, PyTorch are cited; TypeScript is linked from GitHub only; Kafka and Docker have no link.
    expect(canDismiss(VIEW, kafka.key)).toBe(true);
    expect(canDismiss(VIEW, docker.key)).toBe(true);
    expect(canDismiss(VIEW, ts.key)).toBe(true);
    expect(canDismiss(VIEW, py.key)).toBe(false);
    expect(canDismiss(VIEW, torch.key)).toBe(false);
  });

  it("is cited as soon as any one item's bullet names it", () => {
    const v = view([acme, beta], [ts], [edge("proj/beta", ts, [], true), edge("exp/acme", ts, [1])]);
    expect(canDismiss(v, ts.key)).toBe(false);
  });

  it("does not offer a skill the view does not hold", () => {
    expect(canDismiss(VIEW, "skill/ghost")).toBe(false);
  });

  it("tells the server id from the node key, and a panel selection from a node", () => {
    expect(skillIdOf("skill/3f2a-91")).toBe("3f2a-91");
    expect(isPanelKey(DISMISSED_KEY)).toBe(true);
    expect(isPanelKey("ghost:Rust")).toBe(true);
    expect(isPanelKey(py.key) || isPanelKey(acme.key)).toBe(false);
  });
});

describe("placing a project (#322)", () => {
  const unplaced = (key: string, o: Partial<EvidenceItem> = {}) =>
    item(key, "proj", { context_status: "unreviewed", ...o });

  it("counts a project as to place only when it is unreviewed and standalone", () => {
    expect(needsPlacement(unplaced("proj/a"))).toBe(true);
    expect(needsPlacement(item("proj/b", "proj", { context_status: "personal" }))).toBe(false);
    expect(needsPlacement(item("proj/c", "proj", { context_status: "linked", parent_key: "exp/acme" }))).toBe(false);
    // An older payload with no status, a role and a degree are never "to place".
    expect(needsPlacement(item("proj/d", "proj"))).toBe(false);
    expect(needsPlacement(item("exp/e", "exp", { context_status: "unreviewed" }))).toBe(false);
    expect(needsPlacement(item("edu/f", "edu"))).toBe(false);
  });

  it("lists the projects to place in the order the map shows them, and shrinks as they are placed", () => {
    const newer = unplaced("proj/newer", { title: "Newer", end: "2026-01" });
    const older = unplaced("proj/older", { title: "Older", end: "2023-01" });
    const nested = item("proj/nested", "proj", { context_status: "linked", parent_key: "exp/acme", end: "2024-01" });
    const personal = item("proj/mine", "proj", { context_status: "personal", end: "2025-01" });
    const v = view([older, acme, nested, personal, newer], [], []);
    expect(projectsToPlace(v).map((i) => i.key)).toEqual(["proj/newer", "proj/older"]);

    const placed = view([older, acme, { ...newer, context_status: "linked", parent_key: "exp/acme" }], [], []);
    expect(projectsToPlace(placed).map((i) => i.key)).toEqual(["proj/older"]);
    expect(projectsToPlace(view([acme, alpha], [], []))).toEqual([]);
  });

  it("includes a project that needs a description, which is listed last", () => {
    const bareUnplaced = unplaced("proj/bare2", { text: [] });
    const first = unplaced("proj/first", { end: "2024-01" });
    expect(projectsToPlace(view([bareUnplaced, first], [], [])).map((i) => i.key)).toEqual(["proj/first", "proj/bare2"]);
  });

  it("offers every role and degree, newest first within each group, labelled with where", () => {
    const opts = placementOptions(view([intern, school, acme, alpha, beta], [], []));
    expect(opts.experiences).toEqual([
      { key: "exp/acme", label: "Engineer · Acme" }, { key: "exp/intern", label: "Intern · Beta" },
    ]);
    expect(opts.education).toEqual([{ key: "edu/ucsd", label: "B.S. · UCSD" }]);
    // The same list whatever order the items arrive in.
    expect(placementOptions(view([acme, beta, school, alpha, intern], [], []))).toEqual(opts);
    expect(placementOptions(view([beta], [], []))).toEqual({ experiences: [], education: [] });
  });

  it("names a degree by its institution alone when it has no other title", () => {
    expect(placementLabel(item("edu/x", "edu", { title: "UCSD", org: "UCSD" }))).toBe("UCSD");
    expect(placementLabel(item("exp/y", "exp", { title: "Intern", org: "" }))).toBe("Intern");
  });

  it("offers the best suggestion only for a project nobody has placed", () => {
    const candidates = [
      { key: "exp/acme", title: "Engineer @ Acme", reason: "repo name shares 'acme' with Acme" },
      { key: "exp/intern", title: "Intern @ Beta", reason: "description names Beta" },
    ];
    const ctx = (status: ProjectContext["status"], parent: string | null = null): ProjectContext =>
      ({ project_id: "beta", status, parent, candidates });
    expect(topSuggestion(ctx("unreviewed"))).toEqual(candidates[0]);
    expect(topSuggestion(ctx("linked", "exp/intern"))).toBeNull();
    expect(topSuggestion(ctx("personal"))).toBeNull();
    expect(topSuggestion({ ...ctx("unreviewed"), candidates: [] })).toBeNull();
    expect(topSuggestion(undefined)).toBeNull();
  });
});

describe("highlightSegments", () => {
  it("splits text at the marks and keeps all of it", () => {
    expect(highlightSegments("Built it with torch and PyTorch.", [[14, 19], [24, 31]])).toEqual([
      { text: "Built it with ", hit: false }, { text: "torch", hit: true }, { text: " and ", hit: false },
      { text: "PyTorch", hit: true }, { text: ".", hit: false },
    ]);
    expect(highlightSegments("plain", [])).toEqual([{ text: "plain", hit: false }]);
    expect(highlightSegments("", [[0, 3]])).toEqual([]);
    expect(highlightSegments("abc", [[0, 99]]).map((s) => s.text).join("")).toBe("abc");
  });
});

describe("compare to a job", () => {
  const job: JobSkillRow[] = [
    { name: "python", required: true, weight: 2, area: "Languages" },      // cited
    { name: "TypeScript", required: true, weight: 1, area: "Languages" },  // on the profile, GitHub only: claimed
    { name: "Kafka", required: true, weight: 1, area: "Data" },            // on the profile, no evidence: claimed
    { name: "Rust", required: true, weight: 1, area: "Languages" },        // not on the profile
    { name: "Airflow", required: true, weight: 1, area: "Data" },          // not on the profile
    { name: "SQL", required: false, weight: 0.5, area: "Data" },           // preferred, cited
    { name: "Go", required: false, weight: 0.5, area: "Languages" },       // preferred, missing
  ];
  const overlay = jobOverlay(VIEW, job);

  it("counts required and preferred separately, each splitting into three that add up", () => {
    expect(overlay.required).toEqual({ total: 5, evidenced: 1, claimed: 2, missing: 2 });
    expect(overlay.preferred).toEqual({ total: 2, evidenced: 1, claimed: 0, missing: 1 });
    for (const c of [overlay.required, overlay.preferred]) expect(c.evidenced + c.claimed + c.missing).toBe(c.total);
  });

  it("classifies the profile skills the job names and leaves the rest alone", () => {
    expect(overlay.classBySkill.get(py.key)).toBe("required-evidenced");
    expect(overlay.classBySkill.get(ts.key)).toBe("required-claimed");
    expect(overlay.classBySkill.get(kafka.key)).toBe("required-claimed");
    expect(overlay.classBySkill.get(sql.key)).toBe("preferred-evidenced");
    expect(overlay.classBySkill.has(docker.key)).toBe(false);
    expect(overlay.weightBySkill.get(py.key)).toBe(2);
  });

  it("lists only the missing required skills as chips, and counts required per area", () => {
    expect(overlay.missing.map((m) => m.name).sort()).toEqual(["Airflow", "Rust"]);
    expect(overlay.areaRequired.get("Languages")).toEqual({ total: 3, evidenced: 1 });   // Python, TypeScript, Rust
    expect(overlay.areaRequired.get("Data")).toEqual({ total: 2, evidenced: 0 });        // Kafka, Airflow
  });

  it("matches spellings that differ in case, spacing and punctuation, and counts a doubled name once", () => {
    expect(skillMatchKey("Node.js")).toBe(skillMatchKey("nodejs"));
    expect(skillMatchKey("scikit-learn")).toBe(skillMatchKey("Scikit Learn"));
    expect(skillMatchKey("C++")).not.toBe(skillMatchKey("C"));
    const twice = jobOverlay(VIEW, [
      { name: "Python", required: false, weight: 1, area: "Languages" },
      { name: "python", required: true, weight: 1, area: "Languages" },
    ]);
    expect(twice.required.total + twice.preferred.total).toBe(1);
    expect(twice.required.total).toBe(1);    // required wins
  });

  it("an empty job has nothing to compare", () => {
    const none = jobOverlay(VIEW, []);
    expect(none.required.total + none.preferred.total).toBe(0);
    expect(none.missing).toEqual([]);
  });

  it("restyles without moving anything: the missing chips sit below the whole map", () => {
    const layout = layoutMap(VIEW, OPTS);
    const before = JSON.stringify(layout);
    const block = ghostBlock(layout, overlay.missing);
    expect(JSON.stringify(layout)).toBe(before);            // the layout is not an input to anything the job changes
    expect(block.chips).toHaveLength(2);
    expect(block.top).toBe(layout.height);
    for (const c of block.chips) expect(c.y).toBeGreaterThan(layout.height);
    expect(block.chips.map((c) => c.skill.name)).toEqual(["Rust", "Airflow"]);   // by area order, then name
    expect(ghostBlock(layout, []).height).toBe(0);
  });

  it("wraps ghost chips inside the skill column", () => {
    const layout = layoutMap(VIEW, OPTS);
    const many = Array.from({ length: 12 }, (_, i): JobSkillRow => ({ name: `Skill number ${i}`, required: true, weight: 1, area: "Other" }));
    const { chips } = ghostBlock(layout, many);
    expect(new Set(chips.map((c) => c.y)).size).toBeGreaterThan(1);
    for (const c of chips) expect(c.x + c.w).toBeLessThanOrEqual(layout.colW + 1);
  });
});

describe("path helpers", () => {
  it("draws a horizontal cubic link", () => {
    expect(linkPath(0, 10, 100, 50)).toBe("M0,10C50,10,50,50,100,50");
  });
  it("scales radius with citing bullets and width with log of them", () => {
    expect(skillRadius(0)).toBeCloseTo(3.2);
    expect(skillRadius(4)).toBeCloseTo(3.2 + 2.3 * 2);
    expect(edgeWidth(0)).toBe(1);
    expect(edgeWidth(1)).toBeCloseTo(2.5);
    expect(edgeWidth(3)).toBeCloseTo(4);
  });
  it("draws one arc per source", () => {
    expect(sourceArcs(0, 0, 10, 0)).toEqual([]);
    expect(sourceArcs(0, 0, 10, 1)).toHaveLength(1);
    expect(sourceArcs(0, 0, 10, 3)).toHaveLength(3);
    expect(arcPath(0, 0, 10, 0, Math.PI)).toBe("M0,-10A10,10,0,0,1,0,10");
    expect(estimateTextWidth("abcd", 10)).toBe(22.4);
    expect(fitText("short", 200)).toBe("short");
    const cut = fitText("A very long project title that cannot fit", 100, 13);
    expect(cut.endsWith("…")).toBe(true);
    expect(estimateTextWidth(cut, 13)).toBeLessThanOrEqual(100);
  });
});

describe("the editor", () => {
  const exp = item("exp/acme", "exp", {
    id: "acme", title: "Engineer", org: "Acme", text: text("Wrote Python", "", "Used Docker"),
    fields: { title: "Engineer", company: "Acme", start_date: "2023", end_date: "", description: "" },
  });
  const blurbProj = item("proj/alpha", "proj", {
    id: "p1", title: "Alpha", text_source: "blurbs", fields: { name: "Alpha", description: "A tool" },
    text: [
      { raw_index: 0, index: 0, text: "First", blurb_id: "b1" },
      { raw_index: 1, index: 1, text: "Second", blurb_id: "b2" },
    ],
  });
  const descProj = item("proj/beta", "proj", {
    id: "p2", title: "Beta", text_source: "description", text: text("Does a thing"),
    fields: { name: "Beta", description: "Does a thing" },
  });
  const edu = item("edu/ucsd", "edu", { id: "e1", title: "B.S.", fields: { institution: "UCSD", degree: "B.S.", gpa: "3.9" } });

  it("edits the columns an item has, and a project's description only where it is not the text", () => {
    expect(editableFields(exp).map((f) => f.key)).toEqual(["title", "company", "start_date", "end_date", "description"]);
    expect(editableFields(edu).map((f) => f.key)).toContain("gpa");
    expect(editableFields(blurbProj).map((f) => f.key)).toContain("description");
    expect(editableFields(descProj).map((f) => f.key)).not.toContain("description");
  });

  it("plans nothing for an untouched draft", () => {
    for (const it of [exp, blurbProj, descProj, edu]) expect(planSave(it, initialDraft(it))).toEqual({ ops: [], errors: [] });
  });

  it("writes an experience's bullets back by raw position, with the changed columns, in one call", () => {
    const d = initialDraft(exp);
    d.texts[2] = "Used Docker and Kafka";
    d.texts.push("A new bullet");
    d.fields.title = "Senior Engineer";
    expect(planSave(exp, d)).toEqual({
      ops: [{ kind: "experience", id: "acme", body: {
        title: "Senior Engineer", bullets: ["Wrote Python", "", "Used Docker and Kafka", "A new bullet"] } }],
      errors: [],
    });
  });

  it("edits a project's blurbs one by one, content only, and refuses an empty one", () => {
    const d = initialDraft(blurbProj);
    d.texts[1] = "Second, rewritten";
    expect(planSave(blurbProj, d).ops).toEqual([{ kind: "blurb", projectId: "p1", blurbId: "b2", content: "Second, rewritten" }]);
    d.texts[0] = "  ";
    const bad = planSave(blurbProj, d);
    expect(bad.ops).toEqual([]);
    expect(bad.errors).toEqual(["Bullet 1 can't be empty."]);
  });

  it("edits a description-only project through its description, and gives a textless project one box", () => {
    const d = initialDraft(descProj);
    d.texts[0] = "Does a better thing";
    expect(planSave(descProj, d).ops).toEqual([{ kind: "project", id: "p2", body: { description: "Does a better thing" } }]);
    const bare = item("proj/bare", "proj", { id: "p3", title: "Bare", text: [], text_source: "none", fields: { name: "Bare", description: "" } });
    const db = initialDraft(bare);
    expect(db.texts).toEqual([""]);
    db.texts[0] = "Now it says something";
    expect(planSave(bare, db).ops).toEqual([{ kind: "project", id: "p3", body: { description: "Now it says something" } }]);
  });

  it("plans education as a column edit, and refuses to blank a required column", () => {
    const d = initialDraft(edu);
    d.fields.gpa = "4.0";
    expect(planSave(edu, d).ops).toEqual([{ kind: "education", id: "e1", body: { gpa: "4.0" } }]);
    d.fields.institution = " ";
    expect(planSave(edu, d)).toEqual({ ops: [], errors: ["Institution can't be empty."] });
  });
});
