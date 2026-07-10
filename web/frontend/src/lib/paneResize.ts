/** Pure geometry for the draggable pane dividers on the Jobs tab (issue #90).
 *  Kept side-effect-free so the clamping rules are unit-testable without a DOM.
 *
 *  The compiled preview scales to fit *both* axes (see PdfPreview — it renders at
 *  min(width-fit, height-fit)), so the whole page stays visible at any pane
 *  width; it simply shrinks. That means the panes only need plain pixel floors —
 *  no page-aspect width reservation. The earlier aspect×height reservation
 *  collapsed the editor-fraction band in a tall split and froze the divider. */

/** Chat column never narrows past this — below it the response bubble would have
 *  to render narrower than its initial size (see {@link CHAT_BUBBLE_MIN}). */
export const CHAT_PAD_MAX = 16; // px side padding at/above the default column
export const CHAT_PAD_MIN = 6; // px side padding at the minimum column width
/** The AI response bubble's content-box width at the default column. The chat
 *  reclaims side padding before ever shrinking the bubble below this (#90). */
export const CHAT_BUBBLE_MIN = 368;
/** The chat column can't be dragged narrower than this. Doubled from the
 *  original 380 per the #90 follow-up — the chat kept condensing too far. */
export const MIN_CHAT_WIDTH = 2 * (CHAT_BUBBLE_MIN + 2 * CHAT_PAD_MIN); // 760
/** The .tex source/editor pane stays at least this wide so lines stay legible. */
export const MIN_EDITOR_WIDTH = 280;
/** The compiled-preview pane floor *inside split view*, where the editor also
 *  needs room. The resume is the product, so even sharing the pane with the
 *  editor the preview stays large (~1/3 of a wide screen) rather than a sliver.
 *  This tightens the editor-fraction band: on a split narrower than
 *  MIN_EDITOR_WIDTH + this + SPLIT_CHROME the band collapses (divider pins) —
 *  graceful, no overflow — so split view wants a wide workspace (#90). */
export const MIN_PREVIEW_WIDTH = 900;
/** The resume floor in the *Preview-only* view. Much larger than the split-view
 *  preview floor: the compiled resume *is* the product, so it should stay big
 *  and readable — pretty much always visible at a comfortable size rather than
 *  merely legible. With no editor sharing the pane, the whole width is the page.
 *  Error-safe: on a window too small to seat this plus the chat floor,
 *  `chatWidthFromPointer` keeps the chat at its floor and the resume just takes
 *  whatever remains (no overflow/freeze), so it degrades gracefully (#90). */
export const MIN_PREVIEW_ONLY_WIDTH = 900;
/** Absolute floor for the resume area regardless of view. */
export const RESUME_FLOOR = 320;
/** Editor pane fraction bounds in split view (before the width rules tighten). */
export const MIN_EDITOR_FRACTION = 0.2;
export const MAX_EDITOR_FRACTION = 0.8;
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
 *  `minResume` should come from {@link minResumeWidth}. */
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

/** Side padding (px) for the chat scroll area at a given column width. As the
 *  column narrows from the default toward {@link MIN_CHAT_WIDTH}, padding is
 *  reclaimed (16 → 6) so the response bubble keeps its initial content width and
 *  text never wraps tighter than it did on first render (#90). */
export function chatHPadding(width: number): number {
  return clamp(Math.round((width - CHAT_BUBBLE_MIN) / 2), CHAT_PAD_MIN, CHAT_PAD_MAX);
}

/** Minimum width the resume area needs. In split view it must seat both the
 *  editor and the preview at their pixel floors plus the divider chrome. */
export function minResumeWidth(view: "split" | "source" | "preview"): number {
  let need: number;
  if (view === "source") need = MIN_EDITOR_WIDTH;
  else if (view === "split") need = MIN_EDITOR_WIDTH + MIN_PREVIEW_WIDTH + SPLIT_CHROME;
  else need = MIN_PREVIEW_ONLY_WIDTH;
  return Math.max(RESUME_FLOOR, need);
}

/** Valid editor-fraction band for the current split width: the lower bound keeps
 *  the editor ≥ {@link MIN_EDITOR_WIDTH}; the upper bound keeps the preview
 *  ≥ {@link MIN_PREVIEW_WIDTH}. */
export function editorFractionBounds(splitWidth: number): { min: number; max: number } {
  // Before the split is measured, allow the *full* band rather than collapsing
  // to the minimum — otherwise a missing/late measurement pins the applied
  // fraction at MIN_EDITOR_FRACTION and freezes the divider (the #90 bug).
  if (splitWidth <= 0) return { min: MIN_EDITOR_FRACTION, max: MAX_EDITOR_FRACTION };
  const min = Math.min(
    MAX_EDITOR_FRACTION,
    Math.max(MIN_EDITOR_FRACTION, MIN_EDITOR_WIDTH / splitWidth),
  );
  const maxByPreview = (splitWidth - MIN_PREVIEW_WIDTH - SPLIT_CHROME) / splitWidth;
  const max = Math.max(min, Math.min(MAX_EDITOR_FRACTION, maxByPreview));
  return { min, max };
}

/** Clamp a desired editor fraction into the currently-valid band. Used both to
 *  bound a drag and to re-fit the stored preference when the pane is resized. */
export function clampEditorFraction(fraction: number, splitWidth: number): number {
  const { min, max } = editorFractionBounds(splitWidth);
  return clamp(fraction, min, max);
}

/** New editor-pane fraction for a pointer at `clientX`, clamped to the band. */
export function editorFractionFromPointer(
  clientX: number,
  splitLeft: number,
  splitWidth: number,
): number {
  if (splitWidth <= 0) return MIN_EDITOR_FRACTION;
  const raw = (clientX - splitLeft) / splitWidth;
  return clampEditorFraction(raw, splitWidth);
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
