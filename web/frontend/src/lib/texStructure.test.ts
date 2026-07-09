import { describe, expect, it } from "vitest";
import { parseSections, moveSectionTo, parseBulletGroups, moveBulletTo } from "./texStructure";

const SAMPLE = String.raw`\documentclass{article}
\begin{document}

%% ART-SECTION: header
\textbf{\Huge \scshape Jake Ryan}

%% ART-SECTION: education
\section{Education}

%% ART-SECTION: experience
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Dev}{2020 -- Now}{Acme}{TX}
\resumeItemListStart
\resumeItem{Built \textbf{thing A} with Python}
\resumeItem{Shipped thing B}
\resumeItemListEnd
\resumeSubHeadingListEnd

%% ART-SECTION: projects
\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textbf{Gitlytics} $|$ \emph{Python}}{2021}
\resumeItemListStart
\resumeItem{Did X}
\resumeItemListEnd
\resumeSubHeadingListEnd

\end{document}
`;

describe("parseSections", () => {
  it("finds every marked section in document order", () => {
    expect(parseSections(SAMPLE).map(s => s.key)).toEqual([
      "header", "education", "experience", "projects",
    ]);
  });

  it("returns empty when markers were edited away", () => {
    expect(parseSections(SAMPLE.split("%% ART-SECTION:").join("% gone"))).toEqual([]);
  });
});

describe("moveSectionTo", () => {
  it("moves a section to an arbitrary position among movable sections", () => {
    const moved = moveSectionTo(SAMPLE, "projects", 0)!;
    expect(parseSections(moved).map(s => s.key)).toEqual([
      "header", "projects", "education", "experience",
    ]);
    // Content moves with the marker
    expect(moved.indexOf(String.raw`\section{Projects}`)).toBeLessThan(
      moved.indexOf(String.raw`\section{Education}`)
    );
  });

  it("moves a section to the back", () => {
    const moved = moveSectionTo(SAMPLE, "education", 2)!;
    expect(parseSections(moved).map(s => s.key)).toEqual([
      "header", "experience", "projects", "education",
    ]);
  });

  it("keeps the header pinned: it is not addressable and never moves", () => {
    expect(moveSectionTo(SAMPLE, "header", 0)).toBeNull();
    const moved = moveSectionTo(SAMPLE, "projects", 0)!;
    expect(parseSections(moved)[0].key).toBe("header");
  });

  it("returns the same buffer for a no-op move", () => {
    expect(moveSectionTo(SAMPLE, "education", 0)).toBe(SAMPLE);
  });

  it("rejects unknown keys and out-of-bounds targets", () => {
    expect(moveSectionTo(SAMPLE, "nope", 0)).toBeNull();
    expect(moveSectionTo(SAMPLE, "projects", 3)).toBeNull();
    expect(moveSectionTo(SAMPLE, "projects", -1)).toBeNull();
  });

  it("round-trips: moving there and back restores the original buffer", () => {
    const there = moveSectionTo(SAMPLE, "experience", 2)!;
    expect(moveSectionTo(there, "experience", 1)).toBe(SAMPLE);
  });

  it("returns null when markers were edited away", () => {
    const noMarkers = SAMPLE.split("%% ART-SECTION:").join("% gone");
    expect(moveSectionTo(noMarkers, "projects", 0)).toBeNull();
  });
});

describe("parseBulletGroups", () => {
  it("groups bullets under their headings with cleaned display text", () => {
    const groups = parseBulletGroups(SAMPLE);
    expect(groups.map(g => g.label)).toEqual(["Dev", "Gitlytics"]);
    expect(groups[0].sectionKey).toBe("experience");
    expect(groups[0].bullets.map(b => b.text)).toEqual([
      "Built thing A with Python",
      "Shipped thing B",
    ]);
  });
});

describe("moveBulletTo", () => {
  it("reorders bullets within one group", () => {
    const moved = moveBulletTo(SAMPLE, 0, 0, 1)!;
    expect(parseBulletGroups(moved)[0].bullets.map(b => b.text)).toEqual([
      "Shipped thing B",
      "Built thing A with Python",
    ]);
    // Other groups untouched
    expect(parseBulletGroups(moved)[1].bullets.map(b => b.text)).toEqual(["Did X"]);
  });

  it("round-trips: moving there and back restores the original buffer", () => {
    const there = moveBulletTo(SAMPLE, 0, 0, 1)!;
    expect(moveBulletTo(there, 0, 1, 0)).toBe(SAMPLE);
  });

  it("returns the same buffer for a no-op move", () => {
    expect(moveBulletTo(SAMPLE, 0, 1, 1)).toBe(SAMPLE);
  });

  it("rejects unknown groups and out-of-bounds indices", () => {
    expect(moveBulletTo(SAMPLE, 5, 0, 1)).toBeNull();
    expect(moveBulletTo(SAMPLE, 0, 0, 2)).toBeNull();
    expect(moveBulletTo(SAMPLE, 0, -1, 0)).toBeNull();
    expect(moveBulletTo(SAMPLE, 1, 0, 1)).toBeNull(); // single-bullet group
  });

  it("returns null when the bullet lines were edited away", () => {
    const noItems = SAMPLE.split("\\resumeItem{").join("% item ");
    expect(moveBulletTo(noItems, 0, 0, 1)).toBeNull();
  });
});
