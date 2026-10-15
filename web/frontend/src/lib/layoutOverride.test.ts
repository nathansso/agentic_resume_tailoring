import { describe, expect, it } from "vitest";
import {
  bulletGroupFromTex,
  mergeBulletGroup,
  sectionOrderFromTex,
} from "./layoutOverride";
import FORMATTER_RAW from "./__fixtures__/formatter_resume.tex?raw";

const SAMPLE = [
  "%% ART-SECTION: header",
  "\\textbf{Ada}",
  "%% ART-SECTION: experience",
  "\\section{Experience}",
  "  \\resumeSubHeadingListStart",
  "    \\resumeSubheading{Software Engineer}{2020}{BigCo}{NYC}",
  "      \\resumeItemListStart",
  "        \\resumeItem{Led kubernetes deployments}",
  "        \\resumeItem{Managed terraform infrastructure}",
  "      \\resumeItemListEnd",
  "  \\resumeSubHeadingListEnd",
  "%% ART-SECTION: projects",
  "\\section{Projects}",
  "  \\resumeSubHeadingListStart",
  "    \\resumeProjectHeading{\\textbf{Research Pipeline} $|$ \\emph{Python}}{2024}",
  "      \\resumeItemListStart",
  "        \\resumeItem{Published machine learning research}",
  "      \\resumeItemListEnd",
  "  \\resumeSubHeadingListEnd",
  "\\end{document}",
].join("\n");

describe("sectionOrderFromTex", () => {
  it("reads the buffer's order, header excluded", () => {
    expect(sectionOrderFromTex(SAMPLE)).toEqual(["experience", "projects"]);
  });

  it("returns nothing when the markers are gone", () => {
    expect(sectionOrderFromTex("\\section{Experience}")).toEqual([]);
  });
});

describe("bulletGroupFromTex", () => {
  it("keys an experience group by its title — what the backend matches on", () => {
    expect(bulletGroupFromTex(SAMPLE, 0)).toEqual({
      section: "experience",
      item: "Software Engineer",
      order: ["Led kubernetes deployments", "Managed terraform infrastructure"],
    });
  });

  it("keys a project group by its name", () => {
    expect(bulletGroupFromTex(SAMPLE, 1)).toEqual({
      section: "projects",
      item: "Research Pipeline",
      order: ["Published machine learning research"],
    });
  });

  it("is null for a group that does not exist", () => {
    expect(bulletGroupFromTex(SAMPLE, 7)).toBeNull();
  });
});

describe("mergeBulletGroup", () => {
  const first = { section: "experience", item: "SWE", order: ["a", "b"] };

  it("adds a group to an empty override", () => {
    expect(mergeBulletGroup(null, first)).toEqual([first]);
  });

  it("replaces the entry for the same item rather than duplicating it", () => {
    const again = { section: "experience", item: "SWE", order: ["b", "a"] };
    expect(mergeBulletGroup([first], again)).toEqual([again]);
  });

  it("keeps entries for other items", () => {
    const other = { section: "projects", item: "Pipeline", order: ["x"] };
    const merged = mergeBulletGroup([first], other);
    expect(merged).toHaveLength(2);
    expect(merged).toContainEqual(first);
  });
});

describe("bulletGroupFromTex on formatter output (#277)", () => {
  const tex = FORMATTER_RAW.replace(/\r\n/g, "\n");

  it("names the experience by the title the server validates against", () => {
    expect(bulletGroupFromTex(tex, 0)).toEqual({
      section: "experience",
      item: "R&D Engineer",
      // Bullet text keeps the tex escape; agents/layout.py matches bullets by words.
      order: ["Cut inference cost by 18\\% with batching",
              "Shipped a feature store on Postgres", "Mentored two interns"],
    });
  });

  it("names the project by its name, not the heading's tech stack or links", () => {
    expect(bulletGroupFromTex(tex, 2)?.item).toBe("C# Tools");
    expect(bulletGroupFromTex(tex, 3)?.item).toBe("Pixel_Adventure");
  });
});
