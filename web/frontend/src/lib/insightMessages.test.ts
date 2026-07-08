import { describe, expect, it } from "vitest";
import { jobInsightMessages } from "./insightMessages";
import type { JobDetail } from "../types";

function job(overrides: Partial<JobDetail> = {}): JobDetail {
  return {
    job_id: "j1",
    title: "SWE",
    company: "Acme",
    status: "tailored",
    ats_score: 71,
    description: "JD",
    matched_skills: [],
    missing_skills: [],
    score_breakdown: {},
    tailored_score_breakdown: {},
    retailor_count: 0,
    retailor_limit: 5,
    has_manual_edits: false,
    explainability: null,
    ...overrides,
  };
}

describe("jobInsightMessages", () => {
  it("returns nothing for a job with no analysis data", () => {
    expect(jobInsightMessages(job())).toEqual([]);
  });

  it("formats matched and missing skills as one message", () => {
    const msgs = jobInsightMessages(job({
      matched_skills: ["python", "fastapi"],
      missing_skills: ["kubernetes"],
    }));
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toContain("✓ Matched: python, fastapi");
    expect(msgs[0]).toContain("✗ Missing: kubernetes");
  });

  it("omits the empty half of the skills message", () => {
    const msgs = jobInsightMessages(job({ matched_skills: ["python"] }));
    expect(msgs[0]).toContain("Matched");
    expect(msgs[0]).not.toContain("Missing");
  });

  it("formats explainability as a changes message", () => {
    const msgs = jobInsightMessages(job({
      explainability: {
        matched: ["python"],
        emphasized: ["docker (≈containers)"],
        inferred: ["rest apis"],
        missing: ["kubernetes"],
        ats_score: 71,
      },
    }));
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toContain("Changes made by the last tailoring run");
    expect(msgs[0]).toContain("Emphasized: docker (≈containers)");
    expect(msgs[0]).toContain("Inferred from your work: rest apis");
    expect(msgs[0]).toContain("Still missing: kubernetes");
  });

  it("prefers the tailored score breakdown with its delta line", () => {
    const msgs = jobInsightMessages(job({
      score_breakdown: { skill_coverage: { score: 60, weight: 0.4 } },
      tailored_score_breakdown: {
        delta: 16,
        baseline_composite: 55,
        skill_coverage: { score: 80, weight: 0.4 },
        keyword_coverage: { score: 50, weight: 0.3, missing_keywords: ["k8s", "aws"] },
      },
    }));
    expect(msgs).toHaveLength(1);
    expect(msgs[0]).toContain("Tailored score breakdown (+16 vs. baseline 55):");
    expect(msgs[0]).toContain("Skill coverage: 80%");
    expect(msgs[0]).toContain("Keyword coverage: 50% (missing: k8s, aws)");
    expect(msgs[0]).not.toContain("60%");
  });

  it("falls back to the baseline breakdown before tailoring", () => {
    const msgs = jobInsightMessages(job({
      status: "analyzed",
      score_breakdown: { skill_coverage: { score: 60, weight: 0.4 } },
    }));
    expect(msgs[0]).toContain("Score breakdown:");
    expect(msgs[0]).toContain("Skill coverage: 60%");
  });

  it("stacks skills, changes, and score messages in order", () => {
    const msgs = jobInsightMessages(job({
      matched_skills: ["python"],
      explainability: { matched: [], emphasized: ["x"], inferred: [], missing: [], ats_score: 70 },
      tailored_score_breakdown: { skill_coverage: { score: 80, weight: 0.4 } },
    }));
    expect(msgs).toHaveLength(3);
    expect(msgs[0]).toContain("Skills match");
    expect(msgs[1]).toContain("Changes made");
    expect(msgs[2]).toContain("score breakdown");
  });
});
