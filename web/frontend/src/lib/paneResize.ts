/** Pure geometry for the draggable pane dividers on the Jobs tab (issue #90).
 *  Kept side-effect-free so the clamping rules are unit-testable without a DOM. */

/** Chat column never narrows past this — below it the message bubbles start to
 *  compress and prose wraps awkwardly. */
export const MIN_CHAT_WIDTH = 380;
/** The .tex source/editor pane stays at least this wide so lines stay legible. */
export const MIN_EDITOR_WIDTH = 280;
/** Absolute floor for the resume area regardless of page geometry. */
export const RESUME_FLOOR = 320;
/** Editor pane fraction bounds in split view (before the width/height rules
 *  below tighten them further). */
export const MIN_EDITOR_FRACTION = 0.2;
export const MAX_EDITOR_FRACTION = 0.8;
/** US-Letter page aspect (width / height). The compiled resume is one letter
 *  page, so a pane narrower than aspect × height would force the preview to
 *  shrink its height to fit — which we forbid (#90). */
export const PAGE_ASPECT = 8.5 / 11;
/** Divider + inter-pane gaps that eat into the split's usable width. */
export const SPLIT_CHROME = 24;

// Vertical gap between chat messages grows as the column narrows so bubbles
// stay visually separated when they're stacked tall and thin.
export const MSG_GAP_MIN = 8; // px, at/above MSG_GAP_WIDE
export const MSG_GAP_MAX = 15; // px, at/below MSG_GAP_NARROW
export const MSG_GAP_WIDE = 620;
export const MSG_GAP_NARROW = 360;

export function clamp(value: number, lo: number, hi: number): number {
  if (hi < lo) return lo;
  return Math.min(hi, Math.max(lo, value));
}

/** New chat-column width (px) for a pointer at `clientX`, given the columns
 *  container's left edge and width. Clamped so both sides stay usable —
 *  `minResume` should come from {@link minResumeWidth} so the resume preview
 *  never has to reduce its height. */
export function chatWidthFromPointer(
  clientX: number,
  containerLeft: number,
  containerWidth: number,
  minChat: number = MIN_CHAT_WIDTH,
  minResume: number = RESUME_FLOOR,
): number {
  const raw = clientX - containerLeft;
  return clamp(raw, minChat, Math.max(minChat, containerWidth - minResume));
}

/** Minimum width the resume area needs so the preview can render at full height.
 *  In split view it must also seat the editor at its minimum width. */
export function minResumeWidth(
  view: "split" | "source" | "preview",
  previewHeight: number,
): number {
  const minPreview = PAGE_ASPECT * Math.max(0, previewHeight);
  let need: number;
  if (view === "source") need = MIN_EDITOR_WIDTH;
  else if (view === "split") need = minPreview + MIN_EDITOR_WIDTH + SPLIT_CHROME;
  else need = minPreview;
  return Math.max(RESUME_FLOOR, need);
}

/** Valid editor-fraction band for the current split size: the lower bound keeps
 *  the editor ≥ {@link MIN_EDITOR_WIDTH}; the upper bound keeps the preview wide
 *  enough to render at full height (aspect × height). */
export function editorFractionBounds(
  splitWidth: number,
  splitHeight: number,
): { min: number; max: number } {
  if (splitWidth <= 0) return { min: MIN_EDITOR_FRACTION, max: MIN_EDITOR_FRACTION };
  const min = Math.min(
    MAX_EDITOR_FRACTION,
    Math.max(MIN_EDITOR_FRACTION, MIN_EDITOR_WIDTH / splitWidth),
  );
  const minPreview = PAGE_ASPECT * Math.max(0, splitHeight);
  const maxByPreview = (splitWidth - minPreview - SPLIT_CHROME) / splitWidth;
  const max = Math.max(min, Math.min(MAX_EDITOR_FRACTION, maxByPreview));
  return { min, max };
}

/** Clamp a desired editor fraction into the currently-valid band. Used both to
 *  bound a drag and to re-fit the stored preference when the pane is resized. */
export function clampEditorFraction(
  fraction: number,
  splitWidth: number,
  splitHeight: number,
): number {
  const { min, max } = editorFractionBounds(splitWidth, splitHeight);
  return clamp(fraction, min, max);
}

/** New editor-pane fraction for a pointer at `clientX`, clamped to the band. */
export function editorFractionFromPointer(
  clientX: number,
  splitLeft: number,
  splitWidth: number,
  splitHeight: number,
): number {
  if (splitWidth <= 0) return MIN_EDITOR_FRACTION;
  const raw = (clientX - splitLeft) / splitWidth;
  return clampEditorFraction(raw, splitWidth, splitHeight);
}

/** Vertical gap (px) between chat messages for a given column width — wider as
 *  the column narrows so stacked bubbles keep breathing room. */
export function messageGap(width: number): number {
  if (width <= MSG_GAP_NARROW) return MSG_GAP_MAX;
  if (width >= MSG_GAP_WIDE) return MSG_GAP_MIN;
  const t = (width - MSG_GAP_NARROW) / (MSG_GAP_WIDE - MSG_GAP_NARROW);
  return Math.round(MSG_GAP_MAX + (MSG_GAP_MIN - MSG_GAP_MAX) * t);
}

/** Parse a persisted numeric setting, rejecting NaN / non-numeric junk. */
export function readStoredNumber(raw: string | null): number | null {
  if (raw == null || raw.trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}
