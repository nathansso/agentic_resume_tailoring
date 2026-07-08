import { describe, expect, it } from "vitest";
import {
  buildOverlayModel,
  groupIntoLines,
  normalizeForMatch,
  slotToIndex,
  targetIndexForPointer,
  type PdfTextLine,
} from "./pdfOverlay";

const TEX = String.raw`\documentclass{article}
\begin{document}

%% ART-SECTION: header
\textbf{\Huge \scshape Jake Ryan}

%% ART-SECTION: education
\section{Education}
UC San Diego

%% ART-SECTION: experience
\section{Experience}
\resumeSubHeadingListStart
\resumeSubheading{Dev}{2020 -- Now}{Acme}{TX}
\resumeItemListStart
\resumeItem{Built \textbf{thing A} with Python education pipelines}
\resumeItem{Shipped thing B}
\resumeItemListEnd
\resumeSubHeadingListEnd

%% ART-SECTION: projects
\section{Projects}
\resumeSubHeadingListStart
\resumeProjectHeading{\textbf{Gitlytics} $|$ \emph{Python}}{2021}
\resumeItemListStart
\resumeItem{Improved accuracy by 20\%}
\resumeItemListEnd
\resumeSubHeadingListEnd

\end{document}
`;

/** Hand-built page-1 lines simulating pdf.js output for TEX: smallcaps
 *  headings extract uppercased, the first experience bullet wraps onto a
 *  second line (which even contains the word "education"), and the project
 *  bullet renders its escaped percent. */
const LINES: PdfTextLine[] = [
  { text: "Jake Ryan", top: 20, bottom: 34 },
  { text: "EDUCATION", top: 50, bottom: 62 },
  { text: "UC San Diego", top: 66, bottom: 76 },
  { text: "EXPERIENCE", top: 90, bottom: 102 },
  { text: "Dev 2020 – Now", top: 106, bottom: 116 },
  { text: "• Built thing A with Python", top: 120, bottom: 130 },
  { text: "education pipelines", top: 134, bottom: 144 },
  { text: "• Shipped thing B", top: 148, bottom: 158 },
  { text: "PROJECTS", top: 170, bottom: 182 },
  { text: "Gitlytics | Python", top: 186, bottom: 196 },
  { text: "• Improved accuracy by 20%", top: 200, bottom: 210 },
];

const PAGE_HEIGHT = 400;

describe("normalizeForMatch", () => {
  it("strips LaTeX commands, braces, and case", () => {
    expect(normalizeForMatch(String.raw`Built \textbf{thing A} with Python`)).toBe(
      "builtthingawithpython",
    );
  });

  it("unescapes literals and drops inline math", () => {
    expect(normalizeForMatch(String.raw`\textbf{Gitlytics} $|$ \emph{Python}`)).toBe(
      "gitlyticspython",
    );
    expect(normalizeForMatch(String.raw`Improved accuracy by 20\%`)).toBe(
      normalizeForMatch("• Improved accuracy by 20%"),
    );
  });

  it("folds ligatures via NFKD", () => {
    expect(normalizeForMatch("ﬁne-tuned")).toBe("finetuned");
  });
});

describe("groupIntoLines", () => {
  it("clusters items into y-lines and joins left-to-right", () => {
    const lines = groupIntoLines([
      { str: "with Python", x: 120, y: 100.5, height: 10 },
      { str: "•", x: 10, y: 100, height: 10 },
      { str: "Built thing A", x: 20, y: 101, height: 10 },
      { str: "Shipped", x: 10, y: 120, height: 10 },
    ]);
    expect(lines.map(l => l.text)).toEqual(["• Built thing A with Python", "Shipped"]);
    expect(lines[0].top).toBe(100);
    expect(lines[0].bottom).toBe(111);
  });

  it("drops whitespace-only items", () => {
    expect(groupIntoLines([{ str: "  ", x: 0, y: 0, height: 10 }])).toEqual([]);
  });
});

describe("buildOverlayModel", () => {
  const model = buildOverlayModel(TEX, LINES, PAGE_HEIGHT);

  it("finds all movable sections in order with correct bands", () => {
    expect(model.sections.map(s => s.key)).toEqual(["education", "experience", "projects"]);
    expect(model.sections.map(s => s.index)).toEqual([0, 1, 2]);
    const [edu, exp, proj] = model.sections;
    expect(edu.top).toBe(50);
    expect(edu.height).toBe(40); // extends to Experience heading
    expect(exp.top).toBe(90);
    expect(exp.height).toBe(80);
    expect(proj.top).toBe(170);
    expect(proj.height).toBe(PAGE_HEIGHT - 170); // last section runs to page bottom
  });

  it("anchors bullets and absorbs wrapped continuation lines", () => {
    const exp = model.bulletGroups.find(g => g.groupIndex === 0)!;
    expect(exp.bullets).toHaveLength(2);
    const [b0, b1] = exp.bullets;
    expect(b0.top).toBe(120);
    expect(b0.height).toBe(28); // covers the "education pipelines" wrap line
    expect(b1.top).toBe(148);
  });

  it("does not confuse a section heading with the same word inside a bullet", () => {
    // "education pipelines" (a bullet continuation) appears after the
    // Education heading — the ordered cursor + whole-line equality means the
    // education band still anchors at the real heading (top 50).
    expect(model.sections[0].top).toBe(50);
  });

  it("matches bullets containing escaped/math characters", () => {
    const proj = model.bulletGroups.find(g => g.groupIndex === 1)!;
    expect(proj.bullets).toHaveLength(1);
    expect(proj.bullets[0].top).toBe(200);
  });

  it("drops a whole group when one bullet cannot be matched, keeping others", () => {
    const lines = LINES.filter(l => l.text !== "• Shipped thing B");
    const m = buildOverlayModel(TEX, lines, PAGE_HEIGHT);
    expect(m.bulletGroups.map(g => g.groupIndex)).toEqual([1]); // experience group dropped
    expect(m.sections).toHaveLength(3); // sections unaffected
  });

  it("omits a section handle when its heading is not on the page", () => {
    const lines = LINES.filter(l => l.text !== "EDUCATION");
    const m = buildOverlayModel(TEX, lines, PAGE_HEIGHT);
    expect(m.sections.map(s => s.key)).toEqual(["experience", "projects"]);
    // Indices stay document-order among ALL movable sections
    expect(m.sections.map(s => s.index)).toEqual([1, 2]);
  });

  it("returns an empty model when the markers were edited away", () => {
    const noMarkers = TEX.split("%% ART-SECTION:").join("% gone");
    expect(buildOverlayModel(noMarkers, LINES, PAGE_HEIGHT)).toEqual({
      sections: [],
      bulletGroups: [],
    });
  });
});

describe("targetIndexForPointer / slotToIndex", () => {
  const bands = [
    { top: 0, height: 100 },   // midpoint 50
    { top: 100, height: 100 }, // midpoint 150
    { top: 200, height: 100 }, // midpoint 250
  ];

  it("maps pointer positions to insertion slots by band midpoints", () => {
    expect(targetIndexForPointer(10, bands)).toBe(0);
    expect(targetIndexForPointer(60, bands)).toBe(1);
    expect(targetIndexForPointer(160, bands)).toBe(2);
    expect(targetIndexForPointer(260, bands)).toBe(3);
  });

  it("converts slots to splice-style move targets", () => {
    expect(slotToIndex(0, 2)).toBe(0);  // drag band 2 above everything
    expect(slotToIndex(3, 0)).toBe(2);  // drag band 0 below everything
    expect(slotToIndex(1, 1)).toBe(1);  // no-op zone
    expect(slotToIndex(2, 1)).toBe(1);  // slot just after itself is also a no-op
  });
});
