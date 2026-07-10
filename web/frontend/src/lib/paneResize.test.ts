import { describe, expect, it } from "vitest";
import {
  chatHPadding,
  chatWidthFromPointer,
  clamp,
  clampEditorFraction,
  editorFractionBounds,
  editorFractionFromPointer,
  messageGap,
  minResumeWidth,
  readStoredNumber,
  CHAT_BUBBLE_MIN,
  CHAT_PAD_MAX,
  CHAT_PAD_MIN,
  MIN_CHAT_WIDTH,
  MIN_EDITOR_WIDTH,
  MIN_PREVIEW_WIDTH,
  MIN_PREVIEW_ONLY_WIDTH,
  MIN_EDITOR_FRACTION,
  MAX_EDITOR_FRACTION,
  MSG_GAP_MIN,
  MSG_GAP_MAX,
  RESUME_FLOOR,
  SPLIT_CHROME,
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
    // pointer 800px past the container's left edge: above the 760 chat floor and
    // below the resume-side cap (containerWidth − 320 = 880).
    expect(chatWidthFromPointer(900, containerLeft, containerWidth, MIN_CHAT_WIDTH, 320)).toBe(800);
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
  it("uses the Preview-only floor in preview view (~1/3 screen, the product)", () => {
    // The resume is the product, so Preview-only reserves a big floor.
    expect(minResumeWidth("preview")).toBe(MIN_PREVIEW_ONLY_WIDTH);
    expect(MIN_PREVIEW_ONLY_WIDTH).toBeGreaterThanOrEqual(900);
    expect(MIN_PREVIEW_ONLY_WIDTH).toBeGreaterThan(RESUME_FLOOR);
  });

  it("seats both panes plus chrome in split view", () => {
    expect(minResumeWidth("split")).toBeGreaterThan(
      MIN_EDITOR_WIDTH + MIN_PREVIEW_WIDTH - 1,
    );
  });

  it("only needs editor room in source view (down to the floor)", () => {
    // editor min (280) is below the absolute floor, so the floor wins
    expect(minResumeWidth("source")).toBe(RESUME_FLOOR);
    expect(MIN_EDITOR_WIDTH).toBeLessThan(RESUME_FLOOR);
  });
});

describe("editorFractionBounds", () => {
  it("raises the lower bound to keep the editor readable in a narrow split", () => {
    // 700px split → editor min fraction is 280/700 = 0.4, above the 0.2 floor
    const { min } = editorFractionBounds(700);
    expect(min).toBeCloseTo(MIN_EDITOR_WIDTH / 700);
  });

  it("lowers the upper bound so the preview keeps its minimum width", () => {
    // 2000px split → preview needs 900+24, so max leaves that much for the preview
    const { max } = editorFractionBounds(2000);
    expect(max).toBeCloseTo((2000 - MIN_PREVIEW_WIDTH - 24) / 2000);
    expect(max).toBeLessThan(MAX_EDITOR_FRACTION);
  });

  it("opens a real draggable band in a wide-enough split (the old aspect rule froze it)", () => {
    // Regression for #90: the band is a real interval, not a single value. Needs
    // a wide split now that the preview floor is ~1/3 of a large screen.
    const { min, max } = editorFractionBounds(2000);
    expect(max - min).toBeGreaterThan(0.2);
  });

  it("relaxes the editor min to the floor in a wide split (preview floor bounds the max)", () => {
    // With the big preview floor the editor max is preview-bound, not the 0.8
    // ceiling, until the split is very wide; the min relaxes to the 0.2 floor.
    const { min, max } = editorFractionBounds(2000);
    expect(min).toBe(MIN_EDITOR_FRACTION);
    expect(max).toBeCloseTo((2000 - MIN_PREVIEW_WIDTH - 24) / 2000);
  });
});

describe("clampEditorFraction / editorFractionFromPointer", () => {
  it("re-fits a stored preference into the valid band when the pane shrinks", () => {
    // an editor-heavy 0.7 split gets pulled back so the preview keeps its floor
    const max = editorFractionBounds(2000).max;
    expect(clampEditorFraction(0.7, 2000)).toBeCloseTo(max);
    expect(max).toBeLessThan(0.7);
  });

  it("leaves a valid preference untouched in a roomy split", () => {
    // 0.5 is inside the band only once the split is wide enough for a 900 preview
    expect(clampEditorFraction(0.5, 2600)).toBeCloseTo(0.5);
  });

  it("falls back to the full band before the split is measured (width 0)", () => {
    // Regression for #90: an unmeasured split must NOT collapse the applied
    // fraction to MIN_EDITOR_FRACTION — that pins the editor and freezes the
    // divider until (or unless) a resize is observed.
    expect(clampEditorFraction(0.5, 0)).toBeCloseTo(0.5);
    const { min, max } = editorFractionBounds(0);
    expect(min).toBe(MIN_EDITOR_FRACTION);
    expect(max).toBe(MAX_EDITOR_FRACTION);
  });

  it("maps pointer position to a fraction and clamps it", () => {
    // roomy split (2600px over [200..2800]): pointer at x=1500 → 0.5
    expect(editorFractionFromPointer(1500, 200, 2600)).toBeCloseTo(0.5);
    // dragging far right is capped so the preview keeps its 900px floor
    expect(editorFractionFromPointer(99999, 200, 2600)).toBeCloseTo(
      editorFractionBounds(2600).max,
    );
  });
});

describe("source fills the gap on entering split (#90 follow-up)", () => {
  it("expanding the editor to its max pins the preview at its floor", () => {
    // Clicking Split sets the editor intent to MAX_EDITOR_FRACTION; the clamp
    // hands the preview exactly its floor (+chrome) and the editor takes the
    // rest, so the source visibly fills the width the chat just freed up.
    for (const splitWidth of [1600, 2000, 2600]) {
      const frac = clampEditorFraction(MAX_EDITOR_FRACTION, splitWidth);
      const previewSide = (1 - frac) * splitWidth;
      expect(previewSide).toBeCloseTo(MIN_PREVIEW_WIDTH + SPLIT_CHROME, 0);
    }
  });
});

describe("MIN_CHAT_WIDTH", () => {
  it("is the doubled chat floor (#90 follow-up)", () => {
    expect(MIN_CHAT_WIDTH).toBe(760);
  });
});

describe("chatHPadding", () => {
  it("uses full padding at/above the default column width", () => {
    expect(chatHPadding(400)).toBe(CHAT_PAD_MAX);
    expect(chatHPadding(900)).toBe(CHAT_PAD_MAX);
  });

  it("reclaims padding down to the minimum at a narrow column", () => {
    expect(chatHPadding(360)).toBe(CHAT_PAD_MIN);
  });

  it("keeps the bubble at its initial width across the shrink range", () => {
    // content width = column − 2×padding stays ≈ CHAT_BUBBLE_MIN while shrinking
    // (±1px: integer padding can't perfectly halve an odd-width column).
    for (const w of [380, 385, 392, 400]) {
      expect(Math.abs(w - 2 * chatHPadding(w) - CHAT_BUBBLE_MIN)).toBeLessThanOrEqual(1);
    }
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
