import { describe, expect, it } from "vitest";
import { parseSections, moveSection, parseBulletGroups, moveBullet } from "./texStructure";

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

describe("moveSection", () => {
  it("swaps a section with its neighbor", () => {
    const moved = moveSection(SAMPLE, "projects", -1)!;
    expect(parseSections(moved).map(s => s.key)).toEqual([
      "header", "education", "projects", "experience",
    ]);
    // Content moves with the marker
    expect(moved.indexOf(String.raw`\section{Projects}`)).toBeLessThan(
      moved.indexOf(String.raw`\section{Experience}`)
    );
  });

  it("keeps the header pinned at the top", () => {
    expect(moveSection(SAMPLE, "header", 1)).toBeNull();
    expect(moveSection(SAMPLE, "education", -1)).toBeNull();
  });

  it("rejects moves past the document edges", () => {
    expect(moveSection(SAMPLE, "projects", 1)).toBeNull();
  });

  it("round-trips: down then up restores the original buffer", () => {
    const down = moveSection(SAMPLE, "experience", 1)!;
    expect(moveSection(down, "experience", -1)).toBe(SAMPLE);
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

describe("moveBullet", () => {
  it("swaps adjacent bullets inside one list", () => {
    const groups = parseBulletGroups(SAMPLE);
    const first = groups[0].bullets[0];
    const moved = moveBullet(SAMPLE, first.line, 1)!;
    expect(parseBulletGroups(moved)[0].bullets.map(b => b.text)).toEqual([
      "Shipped thing B",
      "Built thing A with Python",
    ]);
  });

  it("never moves a bullet out of its container", () => {
    const groups = parseBulletGroups(SAMPLE);
    expect(moveBullet(SAMPLE, groups[0].bullets[0].line, -1)).toBeNull();
    expect(moveBullet(SAMPLE, groups[0].bullets[1].line, 1)).toBeNull();
    expect(moveBullet(SAMPLE, groups[1].bullets[0].line, 1)).toBeNull();
  });

  it("rejects lines that are not bullets", () => {
    expect(moveBullet(SAMPLE, 0, 1)).toBeNull();
  });
});
