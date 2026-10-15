import { describe, expect, it } from "vitest";
import type {
  EvidenceEdge, EvidenceItem, EvidenceKind, EvidenceSkill, EvidenceText, EvidenceView, JobSkillRow,
} from "../types";
import {
  clampBudget, coverage, DEFAULT_BUDGET, MAX_BUDGET, MIN_BUDGET, pickWeight, rankBullets, resolveKept,
  resumePreview, toggleKept,
} from "./bulletPicker";
import { highlightSegments, jobOverlay, matchJobSkills } from "./evidenceMap";

// ── fixtures ───────────────────────────────────────────────────────────────────

function text(...bullets: string[]): EvidenceText[] {
  let n = 0;
  return bullets.map((t, raw_index) => ({ raw_index, index: t ? n++ : null, text: t, blurb_id: null }));
}

function item(key: string, kind: EvidenceKind, o: Partial<EvidenceItem> = {}): EvidenceItem {
  const bullets = o.text ?? text("did a thing");
  return {
    key, id: key, kind, title: key, org: "", start: "", end: "", description: "",
    text: bullets, text_source: kind === "edu" ? "fields" : bullets.length ? "bullets" : "none",
    cite_key: "", parent_key: null, achievements: [], repo_url: "", fields: {}, ...o,
  };
}

function skill(name: string, o: Partial<EvidenceSkill> = {}): EvidenceSkill {
  return {
    key: `skill/${name.toLowerCase()}`, name, category: "", area: "Tools", proficiency: 3, sources: ["resume"],
    is_core: false, ...o,
  };
}

function edge(itemKey: string, s: EvidenceSkill, cites: number[], marks: [number, number][][] = []): EvidenceEdge {
  return { item_key: itemKey, skill_key: s.key, cites, github: false, marks: cites.map((_, i) => marks[i] ?? [[0, 3]]) };
}

function view(items: EvidenceItem[], skills: EvidenceSkill[], edges: EvidenceEdge[]): EvidenceView {
  return {
    items, skills, edges,
    stats: { items: items.length, skills: skills.length, evidenced: 0, unevidenced: 0, github_only: 0, needs_text: 0 },
  };
}

const req = (name: string, weight = 1, o: Partial<JobSkillRow> = {}): JobSkillRow =>
  ({ name, required: true, weight, area: "Tools", ...o });
const pref = (name: string, weight = 1, o: Partial<JobSkillRow> = {}): JobSkillRow =>
  ({ name, required: false, weight, area: "Tools", ...o });

const py = skill("Python");
const docker = skill("Docker");
const aws = skill("AWS");
const kafka = skill("Kafka");
const rust = skill("Rust");

// A is newer than B. A#b0 cites Python and Docker, A#b1 Python; B#b0 AWS, B#b1 Docker, AWS and Kafka.
const A = item("exp/a", "exp", { title: "Engineer", org: "Acme", start: "2024-01", end: "2024-09", text: text("a0", "a1") });
const B = item("proj/b", "proj", { title: "Beta", start: "2022-01", end: "2022-06", text: text("b0", "b1") });
const V = view(
  [A, B],
  [py, docker, aws, kafka, rust],
  [
    edge("exp/a", py, [0, 1]), edge("exp/a", docker, [0]),
    edge("proj/b", aws, [0, 1]), edge("proj/b", docker, [1]), edge("proj/b", kafka, [1]),
  ],
);
const JOB = [req("Python"), req("Docker"), req("AWS", 0.8), pref("Kafka")];

const ids = (m: { ranked: { id: string }[] }) => m.ranked.map((b) => b.id);

// ── tests ──────────────────────────────────────────────────────────────────────

describe("rankBullets", () => {
  const model = rankBullets(V, JOB);

  it("picks greedily: the most uncovered required weight first, then what is left", () => {
    // A#b0 adds Python+Docker (2). B#b1 adds AWS+Kafka. Then A#b1 (total 1) before B#b0 (total 0.8): both add nothing new.
    expect(ids(model)).toEqual(["exp/a#b0", "proj/b#b1", "exp/a#b1", "proj/b#b0"]);
    expect(model.ranked.map((b) => b.rank)).toEqual([1, 2, 3, 4]);
  });

  it("records what each pick adds, and nothing for a bullet that repeats earlier picks", () => {
    const name = (b: { gain: string[] }) => b.gain.map((id) => model.matches.find((m) => m.id === id)?.row.name);
    expect(name(model.ranked[0])).toEqual(["Docker", "Python"]);
    expect(name(model.ranked[1])).toEqual(["AWS", "Kafka"]);
    expect(model.ranked[2].gain).toEqual([]);
    expect(model.ranked[3].gain).toEqual([]);
  });

  it("is the same for any order the data arrives in", () => {
    const want = JSON.stringify(model.ranked.map((b) => [b.id, b.gain]));
    const shuffled = view([B, A], [rust, kafka, aws, docker, py], [...V.edges].reverse());
    const got = rankBullets(shuffled, [...JOB].reverse());
    expect(JSON.stringify(got.ranked.map((b) => [b.id, b.gain]))).toBe(want);
    expect(got.matches.map((m) => m.id)).toEqual(model.matches.map((m) => m.id));
  });

  it("counts preferred skills at half weight, and required before preferred whatever the numbers", () => {
    expect(pickWeight(model.matches.find((m) => m.row.name === "Kafka")!)).toBe(0.5);
    // One bullet cites only a preferred skill of weight 1 (worth 0.5), the other only a required one of weight 0.3.
    const v = view(
      [item("exp/p", "exp", { text: text("p") }), item("exp/q", "exp", { text: text("q") })],
      [kafka, py],
      [edge("exp/p", kafka, [0]), edge("exp/q", py, [0])],
    );
    expect(ids(rankBullets(v, [pref("Kafka", 1), req("Python", 0.3)]))).toEqual(["exp/q#b0", "exp/p#b0"]);
    // Between two preferred-only bullets the heavier preferred weight wins, even with the half factor.
    const w = view(
      [item("exp/p", "exp", { text: text("p") }), item("exp/q", "exp", { text: text("q") })],
      [kafka, aws],
      [edge("exp/p", kafka, [0]), edge("exp/q", aws, [0])],
    );
    expect(ids(rankBullets(w, [pref("Kafka", 0.4), pref("AWS", 0.9)]))).toEqual(["exp/q#b0", "exp/p#b0"]);
  });

  it("prefers a bullet that adds a preferred skill to one that adds nothing, though the latter weighs more overall", () => {
    // After Python is covered, `heavy` re-covers it (total 1) and `light` adds Kafka (0.5).
    const v = view(
      [item("exp/first", "exp", { text: text("x") }), item("exp/heavy", "exp", { text: text("h") }),
        item("exp/light", "exp", { text: text("l") })],
      [py, kafka],
      [edge("exp/first", py, [0]), edge("exp/heavy", py, [0]), edge("exp/light", kafka, [0])],
    );
    const got = rankBullets(v, [req("Python"), pref("Kafka")]);
    expect(ids(got)).toEqual(["exp/first#b0", "exp/light#b0", "exp/heavy#b0"]);
  });

  it("breaks a tie on the more recent work, 'Present' counting as newest, then on item key and ordinal", () => {
    const old = item("exp/old", "exp", { end: "2020-01", text: text("o") });
    const mid = item("exp/mid", "exp", { end: "2023-05", text: text("m") });
    const now = item("exp/now", "exp", { end: "Present", text: text("n1", "n2") });
    const v = view([old, mid, now], [py], [
      edge("exp/old", py, [0]), edge("exp/mid", py, [0]), edge("exp/now", py, [0, 1]),
    ]);
    // All three would add Python; only the first pick does. Ties go newest first; within an item, the lower ordinal.
    expect(ids(rankBullets(v, [req("Python")]))).toEqual(["exp/now#b0", "exp/now#b1", "exp/mid#b0", "exp/old#b0"]);
    const same = view([item("exp/z", "exp", { end: "2023-05", text: text("z") }),
      item("exp/y", "exp", { end: "2023-05", text: text("y") })], [py], [edge("exp/z", py, [0]), edge("exp/y", py, [0])]);
    expect(ids(rankBullets(same, [req("Python")]))).toEqual(["exp/y#b0", "exp/z#b0"]);
  });

  it("lists bullets that cite none of the job's skills apart, skipping empty bullets and education", () => {
    const plain = item("exp/plain", "exp", { text: text("no skills here", "", "cites Rust") });
    const school = item("edu/u", "edu", { text: text("coursework") });
    const v = view([A, plain, school], [py, docker, rust], [
      edge("exp/a", py, [0]), edge("exp/plain", rust, [1]),   // Rust is cited by the third bullet, which has ordinal 1
    ]);
    const got = rankBullets(v, [req("Python")]);
    expect(ids(got)).toEqual(["exp/a#b0"]);
    expect(got.rest.map((b) => b.id)).toEqual(["exp/a#b1", "exp/plain#b0", "exp/plain#b1"]);
  });

  it("matches a requirement by any of its terms", () => {
    const got = rankBullets(V, [req("Container platforms", 1, { terms: ["kubernetes", "docker"] })]);
    expect(ids(got)).toEqual(["exp/a#b0", "proj/b#b1"]);
    expect(got.matches[0].skills.map((s) => s.name)).toEqual(["Docker"]);
    expect(got.ranked[1].gain).toEqual([]);          // already covered by the first pick
  });

  it("marks only the words that name a skill the job asks for", () => {
    const v = view(
      [item("exp/m", "exp", { text: text("Built Python and Rust tools") })], [py, rust],
      [edge("exp/m", py, [0], [[[6, 12]]]), edge("exp/m", rust, [0], [[[17, 21]]])],
    );
    const [b] = rankBullets(v, [req("Python")]).ranked;
    expect(highlightSegments(b.text, b.marks).filter((s) => s.hit).map((s) => s.text)).toEqual(["Python"]);
  });
});

describe("the kept set", () => {
  const model = rankBullets(V, JOB);

  it("defaults to the top of the ranking and follows the budget", () => {
    expect([...resolveKept(model, 2, null)]).toEqual(["exp/a#b0", "proj/b#b1"]);
    expect(resolveKept(model, DEFAULT_BUDGET, null).size).toBe(4);       // fewer bullets than the budget
    expect(clampBudget(0)).toBe(MIN_BUDGET);
    expect(clampBudget(99)).toBe(MAX_BUDGET);
    expect(clampBudget(7.6)).toBe(8);
  });

  it("keeps the user's own choice when the budget moves, and drops ids that no longer exist", () => {
    const own = toggleKept(resolveKept(model, 2, null), "exp/a#b1");
    expect([...own].sort()).toEqual(["exp/a#b0", "exp/a#b1", "proj/b#b1"]);
    expect(resolveKept(model, 5, own)).toEqual(own);
    expect(toggleKept(own, "exp/a#b0").has("exp/a#b0")).toBe(false);
    expect([...resolveKept(model, 5, new Set(["gone#b3", "exp/a#b0"]))]).toEqual(["exp/a#b0"]);
  });

  it("lets a bullet that touches nothing the job asks for be kept", () => {
    const v = view([A, item("exp/x", "exp", { text: text("headline project") })], [py], [edge("exp/a", py, [0])]);
    const m = rankBullets(v, [req("Python")]);
    expect(m.rest.map((b) => b.id)).toContain("exp/x#b0");
    expect(resolveKept(m, 1, new Set(["exp/x#b0"])).has("exp/x#b0")).toBe(true);
  });
});

describe("coverage", () => {
  // Python and Docker required, AWS required, Rust required (on the profile, cited by nothing), Go required (not on the profile).
  const job = [req("Python"), req("Docker"), req("AWS", 0.8), req("Rust", 0.5), req("Go", 0.4), pref("Kafka"), pref("Zig", 0.2)];
  const model = rankBullets(V, job);
  const row = (c: ReturnType<typeof coverage>, name: string) => c.rows.find((r) => r.match.row.name === name)!;

  it("says covered, coverable, claimed or missing for each requirement", () => {
    const c = coverage(model, resolveKept(model, 1, null));          // only A#b0: Python and Docker
    expect(row(c, "Python").status).toBe("covered");
    expect(row(c, "Docker").status).toBe("covered");
    expect(row(c, "AWS").status).toBe("coverable");                  // B cites it, nothing kept does
    expect(row(c, "Kafka").status).toBe("coverable");
    expect(row(c, "Rust").status).toBe("claimed");                   // on the profile, no bullet cites it
    expect(row(c, "Go").status).toBe("missing");
    expect(row(c, "Zig").status).toBe("missing");
    expect(c.claimed.map((r) => r.label)).toEqual(["Rust"]);
    expect(c.missing.map((r) => r.label)).toEqual(["Go"]);          // required skills only
  });

  it("keeps the counts separate and updates them as bullets are ticked", () => {
    const one = coverage(model, resolveKept(model, 1, null));
    expect(one.required).toEqual({ total: 5, covered: 2, weightTotal: 3.7, weightCovered: 2 });
    expect(one.preferred).toEqual({ total: 2, covered: 0, weightTotal: 1.2, weightCovered: 0 });

    const two = coverage(model, resolveKept(model, 2, null));        // + B#b1: AWS and Kafka
    expect(two.required).toEqual({ total: 5, covered: 3, weightTotal: 3.7, weightCovered: 2.8 });
    expect(two.preferred).toEqual({ total: 2, covered: 1, weightTotal: 1.2, weightCovered: 1 });
    expect(row(two, "AWS").keptCiting).toEqual(["proj/b#b1"]);
    expect(row(two, "AWS").citing).toEqual(["proj/b#b1", "proj/b#b0"]);

    const none = coverage(model, new Set());
    expect(none.required.covered + none.preferred.covered).toBe(0);
    // No score field exists to add the groups into.
    expect(Object.keys(none).sort()).toEqual(["claimed", "missing", "preferred", "required", "rows"]);
  });

  it("names a requirement by the profile's own spelling when it has the skill", () => {
    const c = coverage(rankBullets(V, [req("docker", 1, { terms: ["docker"] })]), new Set());
    expect(c.rows[0].label).toBe("Docker");
  });

  it("labels a requirement by the skill its own name matches before another skill its terms reach", () => {
    const torch = skill("PyTorch");
    const v = view([A], [torch, py], [edge("exp/a", py, [0])]);
    const c = coverage(rankBullets(v, [req("python", 1, { terms: ["python", "pytorch"] })]), new Set());
    expect(c.rows[0].label).toBe("Python");             // not PyTorch, whose key sorts first
    const via = coverage(rankBullets(v, [req("deep learning", 1, { terms: ["pytorch"] })]), new Set());
    expect(via.rows[0].label).toBe("PyTorch");           // no skill of that name: the one a term reaches
  });
});

describe("term matching in the job overlay", () => {
  it("resolves a requirement through any of its terms and counts it once", () => {
    const row: JobSkillRow = { name: "cloud", required: true, weight: 1, area: "Tools", terms: ["cloud", "aws", "azure"] };
    const cloudy = skill("Azure");
    const v = view([B], [aws, cloudy], [edge("proj/b", aws, [0])]);
    const ov = jobOverlay(v, [row]);
    expect(ov.required).toEqual({ total: 1, evidenced: 1, claimed: 0, missing: 0 });    // two skills name it: still one
    expect(ov.classBySkill.get(aws.key)).toBe("required-evidenced");
    expect(ov.classBySkill.get(cloudy.key)).toBe("required-claimed");
    expect(ov.missing).toEqual([]);
  });

  it("is missing only when no term names a skill, and matchJobSkills lists those skills", () => {
    const v = view([B], [aws], [edge("proj/b", aws, [0])]);
    const row: JobSkillRow = { name: "gpu work", required: true, weight: 1, area: "Tools", terms: ["cuda", "triton"] };
    expect(jobOverlay(v, [row]).missing.map((m) => m.name)).toEqual(["gpu work"]);
    expect(matchJobSkills(v, [{ ...row, terms: ["CUDA", "Amazon"] }])[0].skills).toEqual([]);
    expect(matchJobSkills(v, [{ ...row, terms: ["aws"] }])[0].skills.map((s) => s.name)).toEqual(["AWS"]);
  });
});

describe("resumePreview", () => {
  it("groups the kept bullets by section, newest first, and each item's bullets in order", () => {
    const old = item("exp/old", "exp", { title: "Intern", end: "2021-08", text: text("o0") });
    const proj = item("proj/p", "proj", { title: "Side", end: "2025-01", text: text("p0") });
    const v = view([old, A, B, proj], [py], [
      edge("exp/old", py, [0]), edge("exp/a", py, [0, 1]), edge("proj/b", py, [0]), edge("proj/p", py, [0]),
    ]);
    const model = rankBullets(v, [req("Python")]);
    const kept = new Set(["exp/a#b1", "exp/a#b0", "exp/old#b0", "proj/b#b0", "proj/p#b0"]);
    const out = resumePreview(model, kept);
    expect(out.map((s) => s.title)).toEqual(["Experience", "Projects"]);
    expect(out[0].items.map((i) => i.item.key)).toEqual(["exp/a", "exp/old"]);
    expect(out[0].items[0].bullets.map((b) => b.ordinal)).toEqual([0, 1]);
    expect(out[1].items.map((i) => i.item.key)).toEqual(["proj/p", "proj/b"]);
    expect(resumePreview(model, new Set())).toEqual([]);
    expect(resumePreview(model, new Set(["proj/p#b0"])).map((s) => s.title)).toEqual(["Projects"]);
  });
});
