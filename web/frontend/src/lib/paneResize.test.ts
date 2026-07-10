import { describe, expect, it } from "vitest";
import {
  chatWidthFromPointer,
  clamp,
  clampEditorFraction,
  editorFractionBounds,
  editorFractionFromPointer,
  messageGap,
  minResumeWidth,
  readStoredNumber,
  MIN_CHAT_WIDTH,
  MIN_EDITOR_WIDTH,
  MIN_EDITOR_FRACTION,
  MAX_EDITOR_FRACTION,
  MSG_GAP_MIN,
  MSG_GAP_MAX,
  PAGE_ASPECT,
  RESUME_FLOOR,
} from "./paneResize";

describe("clamp", () => {
  it("bounds a value within range", () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
  });

  it("returns the low bound when the range is inverted", () => {
    expect(clamp(5, 10, 0)).toBe(10);
  });
});

describe("chatWidthFromPointer", () => {
  const containerLeft = 100;
  const containerWidth = 1200; // container spans x=100..1300

  it("tracks the pointer within the usable band", () => {
    expect(chatWidthFromPointer(600, containerLeft, containerWidth, MIN_CHAT_WIDTH, 320)).toBe(500);
  });

  it("clamps to the minimum chat width", () => {
    expect(chatWidthFromPointer(120, containerLeft, containerWidth)).toBe(MIN_CHAT_WIDTH);
  });

  it("clamps so the resume area keeps its minimum", () => {
    expect(chatWidthFromPointer(5000, containerLeft, containerWidth, MIN_CHAT_WIDTH, 400)).toBe(
      containerWidth - 400,
    );
  });

  it("never violates the chat minimum even in a tiny container", () => {
    expect(chatWidthFromPointer(500, 0, 400, MIN_CHAT_WIDTH, 320)).toBe(MIN_CHAT_WIDTH);
  });
});

describe("minResumeWidth", () => {
  it("reserves an aspect-scaled width so preview keeps full height", () => {
    // preview-only view: just the page's full-height width
    expect(minResumeWidth("preview", 800)).toBeCloseTo(PAGE_ASPECT * 800);
  });

  it("adds the editor's minimum width in split view", () => {
    expect(minResumeWidth("split", 800)).toBeGreaterThan(minResumeWidth("preview", 800) + MIN_EDITOR_WIDTH - 1);
  });

  it("only needs editor room in source view (down to the floor)", () => {
    // editor min (280) is below the absolute floor, so the floor wins
    expect(minResumeWidth("source", 800)).toBe(RESUME_FLOOR);
    expect(MIN_EDITOR_WIDTH).toBeLessThan(RESUME_FLOOR);
  });

  it("never drops below the absolute floor", () => {
    expect(minResumeWidth("preview", 10)).toBe(RESUME_FLOOR);
  });
});

describe("editorFractionBounds", () => {
  it("raises the lower bound to keep the editor readable in a narrow split", () => {
    // 700px split → editor min fraction is 280/700 = 0.4, above the 0.2 floor
    const { min } = editorFractionBounds(700, 0);
    expect(min).toBeCloseTo(MIN_EDITOR_WIDTH / 700);
  });

  it("lowers the upper bound so the preview keeps full height", () => {
    // tall + not-very-wide split: the page needs ~PAGE_ASPECT*900 = 695px,
    // so the editor can't grow past what leaves the preview that width
    const { max } = editorFractionBounds(1400, 900);
    expect(max).toBeGreaterThan(MIN_EDITOR_FRACTION);
    expect(max).toBeLessThan(0.5);
  });

  it("keeps the default band in a wide, short split", () => {
    // 350px tall page only needs ~270px, so the editor can reach its full max
    const { min, max } = editorFractionBounds(1600, 350);
    expect(min).toBe(MIN_EDITOR_FRACTION);
    expect(max).toBe(MAX_EDITOR_FRACTION);
  });
});

describe("clampEditorFraction / editorFractionFromPointer", () => {
  it("re-fits a stored preference into the valid band when the pane shrinks", () => {
    // a comfortable 0.5 split gets pulled down when the pane is tall/narrow
    expect(clampEditorFraction(0.5, 1000, 1000)).toBeLessThan(0.5);
  });

  it("leaves a valid preference untouched in a roomy split", () => {
    expect(clampEditorFraction(0.5, 1600, 350)).toBeCloseTo(0.5);
  });

  it("maps pointer position to a fraction and clamps it", () => {
    // roomy split: pointer at x=1000 over [200..1800] → 0.5
    expect(editorFractionFromPointer(1000, 200, 1600, 350)).toBeCloseTo(0.5);
    // dragging far right is capped by the editor-max / preview-height rules
    expect(editorFractionFromPointer(9999, 200, 1600, 350)).toBe(MAX_EDITOR_FRACTION);
  });
});

describe("messageGap", () => {
  it("is smallest for a wide column", () => {
    expect(messageGap(800)).toBe(MSG_GAP_MIN);
  });

  it("is largest for a narrow column", () => {
    expect(messageGap(300)).toBe(MSG_GAP_MAX);
  });

  it("interpolates between the bounds", () => {
    const g = messageGap(490); // midpoint of 360..620
    expect(g).toBeGreaterThan(MSG_GAP_MIN);
    expect(g).toBeLessThan(MSG_GAP_MAX);
  });
});

describe("readStoredNumber", () => {
  it("returns null for missing values", () => {
    expect(readStoredNumber(null)).toBeNull();
  });

  it("parses valid numbers", () => {
    expect(readStoredNumber("480")).toBe(480);
    expect(readStoredNumber("0.5")).toBe(0.5);
  });

  it("rejects non-numeric junk", () => {
    expect(readStoredNumber("wide")).toBeNull();
    expect(readStoredNumber("")).toBeNull();
  });
});
