import { describe, expect, it } from "vitest";
import { jobWelcome, type WelcomeJob } from "./welcome";

function job(overrides: Partial<WelcomeJob> = {}): WelcomeJob {
  return {
    status: "created",
    description: "",
    retailor_count: 0,
    retailor_limit: 3,
    ...overrides,
  };
}

describe("jobWelcome", () => {
  it("suggests pasting the JD for a fresh job without a description", () => {
    const text = jobWelcome(job());
    expect(text).toContain("Paste the job description");
  });

  it("suggests tailoring once a description exists", () => {
    const text = jobWelcome(job({ description: "We need Python." }));
    expect(text).toContain('"tailor"');
  });

  it("suggests tailoring for analyzed jobs", () => {
    const text = jobWelcome(job({ status: "analyzed", description: "JD" }));
    expect(text).toContain('"tailor"');
    expect(text).toContain("missing");
  });

  it("suggests revision re-tailoring with runs left once tailored", () => {
    const text = jobWelcome(job({ status: "tailored", description: "JD", retailor_count: 1 }));
    expect(text).toContain('tailor emphasize Python more');
    expect(text).toContain("2 runs left");
  });

  it("uses singular 'run' when one run remains", () => {
    const text = jobWelcome(job({ status: "tailored", description: "JD", retailor_count: 2 }));
    expect(text).toContain("1 run left");
  });

  it("reports an exhausted budget instead of suggesting re-tailoring", () => {
    const text = jobWelcome(job({ status: "tailored", description: "JD", retailor_count: 3 }));
    expect(text).toContain("Re-tailor budget used (3/3)");
    expect(text).not.toContain("runs left");
  });

  it("treats exported like tailored", () => {
    const text = jobWelcome(job({ status: "exported", description: "JD" }));
    expect(text).toContain("tailor emphasize Python more");
  });
});
