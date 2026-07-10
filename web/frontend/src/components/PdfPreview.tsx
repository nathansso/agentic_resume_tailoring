import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { colors, font } from "../theme";
import {
  buildOverlayModel,
  groupIntoLines,
  reorderPatch,
  type PdfTextItem,
  type ReorderPatch,
} from "../lib/pdfOverlay";
import { PdfDragOverlay } from "./PdfDragOverlay";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export interface OverlayProps {
  /** The tex snapshot the displayed PDF was compiled from (model source). */
  tex: string | null;
  /** False while the buffer has diverged from the render or a compile runs. */
  enabled: boolean;
  onMoveSection: (key: string, targetIndex: number) => void;
  onMoveBullet: (groupIndex: number, fromIdx: number, toIdx: number) => void;
  /** Double-click on the preview → jump the source editor to this tex line. */
  onJumpToLine: (texLine: number) => void;
}

interface Props {
  pdfData: Uint8Array | null;
  compiling: boolean;
  error: string | null;
  /** Auto-compile is paused (daily quota); show the manual recovery hint. */
  paused: boolean;
  onRecompile: () => void;
  /** Drag-to-reorder over page 1 (sections and bullets). */
  overlay?: OverlayProps;
}

interface Page1Text {
  items: PdfTextItem[];
  width: number;
  height: number;
}

const PAGE_GAP = 12;
// Slack subtracted from the available height so sub-pixel rounding never
// triggers a scrollbar when a page is scaled to "fit".
const FIT_MARGIN = 6;

/** Renders the compiled PDF onto canvases via pdf.js — no iframe, so no
 *  browser PDF-viewer chrome. The last good render stays visible while a new
 *  one compiles or fails (flicker-free swap). Page 1 also exposes its text
 *  geometry so PdfDragOverlay can map drag bands back onto the .tex source. */
export function PdfPreview({ pdfData, compiling, error, paused, onRecompile, overlay }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderGen = useRef(0);
  const [width, setWidth] = useState(0);
  // Available height of the scroll viewport — pages are scaled to fit both
  // dimensions so the whole page stays visible as the pane is resized (#90).
  const [height, setHeight] = useState(0);
  const [hasRender, setHasRender] = useState(false);
  const [page1, setPage1] = useState<Page1Text | null>(null);
  // True after a drop's slices were re-composited onto the canvas — the
  // preview already shows the new order, so the wait veil skips its dim.
  const [patched, setPatched] = useState(false);

  // Track the pane size (debounced) so pages re-render scaled to fit.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let timer: number | undefined;
    const ro = new ResizeObserver(entries => {
      const rect = entries[entries.length - 1].contentRect;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        setWidth(rect.width);
        setHeight(rect.height);
      }, 150);
    });
    ro.observe(el);
    setWidth(el.clientWidth);
    setHeight(el.clientHeight);
    return () => {
      ro.disconnect();
      window.clearTimeout(timer);
    };
  }, []);

  useEffect(() => {
    if (!pdfData || width <= 0) return;
    const gen = ++renderGen.current;
    (async () => {
      // pdf.js transfers the buffer to its worker — hand it a copy.
      const doc = await pdfjs.getDocument({ data: pdfData.slice() }).promise;
      if (gen !== renderGen.current) {
        void doc.destroy();
        return;
      }
      const dpr = window.devicePixelRatio || 1;
      const canvases: HTMLCanvasElement[] = [];
      let page1Text: Page1Text | null = null;
      for (let i = 1; i <= doc.numPages; i++) {
        const page = await doc.getPage(i);
        const base = page.getViewport({ scale: 1 });
        // Fit each page to the pane on both axes so its entirety stays visible
        // without scrolling; fall back to width-fit until height is measured.
        const byWidth = width / base.width;
        const byHeight = height > 0 ? (height - FIT_MARGIN) / base.height : byWidth;
        const cssScale = Math.max(0.01, Math.min(byWidth, byHeight));
        const viewport = page.getViewport({ scale: cssScale * dpr });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
        canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;
        canvas.style.display = "block";
        canvas.style.background = "#fff";
        // No gap after the last page — a trailing margin would force a sliver
        // of scroll even when the page itself fits.
        if (i < doc.numPages) canvas.style.marginBottom = `${PAGE_GAP}px`;
        const ctx = canvas.getContext("2d");
        if (!ctx) return;
        await page.render({ canvasContext: ctx, viewport }).promise;
        canvases.push(canvas);

        if (i === 1) {
          // Text geometry in CSS pixels (top-down) for the drag overlay.
          const cssViewport = page.getViewport({ scale: cssScale });
          const tc = await page.getTextContent();
          const items: PdfTextItem[] = [];
          for (const it of tc.items) {
            if (!("str" in it)) continue;
            const tx = pdfjs.Util.transform(cssViewport.transform, it.transform);
            const fontHeight = Math.hypot(tx[2], tx[3]);
            items.push({ str: it.str, x: tx[4], y: tx[5] - fontHeight, height: fontHeight });
          }
          page1Text = { items, width: cssViewport.width, height: cssViewport.height };
        }
      }
      if (gen !== renderGen.current) {
        void doc.destroy();
        return;
      }
      // Swap only after every page rendered — the old render stays up until now.
      pagesRef.current?.replaceChildren(...canvases);
      void docRef.current?.destroy();
      docRef.current = doc;
      setPage1(page1Text);
      setPatched(false);
      setHasRender(true);
    })().catch(() => {
      // Render failure: keep the last good canvases.
    });
  }, [pdfData, width, height]);

  // Unmount: drop the last document proxy.
  useEffect(
    () => () => {
      renderGen.current++;
      void docRef.current?.destroy();
      docRef.current = null;
    },
    [],
  );

  const model = useMemo(() => {
    if (!overlay?.tex || !page1) return null;
    return buildOverlayModel(overlay.tex, groupIntoLines(page1.items), page1.height);
  }, [overlay?.tex, page1]);

  /** Re-composite the page-1 canvas per the patch so a drop shows its new
   *  order instantly — the real compile replaces the canvas seconds later.
   *  Snapshot the disturbed region, blank it, redraw each slice at its
   *  destination (coords are CSS px; the canvas backing store is scaled). */
  function applyPatch(patch: ReorderPatch) {
    const canvas = pagesRef.current?.querySelector("canvas");
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !page1) return;
    const k = canvas.width / page1.width;
    const regionY = Math.floor(patch.regionTop * k);
    const regionH = Math.min(Math.ceil((patch.regionBottom - patch.regionTop) * k), canvas.height - regionY);
    if (regionH <= 0) return;
    const snap = document.createElement("canvas");
    snap.width = canvas.width;
    snap.height = regionH;
    const snapCtx = snap.getContext("2d");
    if (!snapCtx) return;
    snapCtx.drawImage(canvas, 0, regionY, canvas.width, regionH, 0, 0, canvas.width, regionH);
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, regionY, canvas.width, regionH);
    for (const sl of patch.slices) {
      const h = Math.round(sl.height * k);
      if (h <= 0) continue;
      ctx.drawImage(
        snap,
        0, Math.floor((sl.srcTop - patch.regionTop) * k), canvas.width, h,
        0, Math.floor(sl.destTop * k), canvas.width, h,
      );
    }
    setPatched(true);
  }

  function moveSectionWithPreview(key: string, targetIndex: number) {
    if (model) {
      const from = model.sections.findIndex(sec => sec.key === key);
      const to = model.sections.findIndex(sec => sec.index === targetIndex);
      const patch = from >= 0 && to >= 0 ? reorderPatch(model.sections, from, to) : null;
      if (patch) applyPatch(patch);
    }
    overlay?.onMoveSection(key, targetIndex);
  }

  function moveBulletWithPreview(groupIndex: number, fromIdx: number, toIdx: number) {
    const bullets = model?.bulletGroups.find(g => g.groupIndex === groupIndex)?.bullets;
    const patch = bullets ? reorderPatch(bullets, fromIdx, toIdx) : null;
    if (patch) applyPatch(patch);
    overlay?.onMoveBullet(groupIndex, fromIdx, toIdx);
  }

  const dragReady = model !== null && model.sections.length > 0;
  const statusText = compiling
    ? "Compiling…"
    : !hasRender
      ? ""
      : overlay && model && !dragReady
        ? "Reordering unavailable — the %% ART-SECTION markers were edited out"
        : overlay && dragReady && !overlay.enabled
          ? "Changes pending…"
          : overlay && dragReady
            ? "Preview up to date — drag sections or bullets to reorder"
            : "Preview up to date";

  return (
    <div style={s.pane}>
      <div style={s.statusBar}>
        <span style={s.statusText}>{statusText}</span>
        {(error || paused) && (
          <button style={s.recompileBtn} onClick={onRecompile}>Recompile</button>
        )}
      </div>
      {error && <pre style={s.compileError}>{error}</pre>}
      <div ref={containerRef} style={s.scroll}>
        <div style={s.pagesWrap}>
          <div ref={pagesRef} />
          {overlay && model && dragReady && page1 && (
            <PdfDragOverlay
              model={model}
              width={page1.width}
              height={page1.height}
              enabled={overlay.enabled && !compiling}
              onMoveSection={moveSectionWithPreview}
              onMoveBullet={moveBulletWithPreview}
              onJumpToLine={overlay.onJumpToLine}
            />
          )}
          {/* An edit takes a compile round-trip to show — make the wait
              unmistakable instead of leaving a silently stale render. After a
              drop the canvas was already re-composited optimistically, so
              only the badge shows (no dim over an already-correct preview). */}
          {hasRender && !error && (compiling || (overlay && !overlay.enabled)) && (
            <div style={{ ...s.staleVeil, ...(patched ? s.staleVeilClear : {}) }}>
              <span style={s.staleBadge}>Updating preview…</span>
            </div>
          )}
        </div>
        {!hasRender && !error && (
          <p style={s.muted}>{compiling ? "Compiling preview…" : "The preview appears here once compiled."}</p>
        )}
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  pane: { display: "flex", flexDirection: "column", flex: 1, minHeight: 0, gap: "0.375rem" },
  statusBar: {
    display: "flex", alignItems: "center", justifyContent: "space-between",
    gap: "0.5rem", minHeight: "1.5rem", flexShrink: 0,
  },
  statusText: { color: colors.textMuted, fontSize: "0.7rem", fontStyle: "italic" },
  recompileBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.125rem 0.5rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  compileError: {
    margin: 0, color: colors.error, fontSize: "0.7rem", whiteSpace: "pre-wrap",
    wordBreak: "break-word", background: colors.surface,
    border: `1px solid ${colors.error}`, padding: "0.5rem 0.625rem",
    maxHeight: "8rem", overflowY: "auto", flexShrink: 0,
  },
  scroll: {
    flex: 1, minHeight: 0, overflowY: "auto", overflowX: "hidden",
    border: `1px solid ${colors.primary}`, background: "#525659",
  },
  // fit-content + auto margins center the page when it's height-constrained
  // (narrower than the pane); the drag overlay is absolutely positioned within
  // this wrapper, so canvas and overlay shift together and stay aligned.
  pagesWrap: { position: "relative", width: "fit-content", margin: "0 auto" },
  staleVeil: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0, zIndex: 3,
    background: "rgba(13,17,23,0.35)", display: "flex",
    justifyContent: "center", alignItems: "flex-start",
    pointerEvents: "none",
  },
  staleVeilClear: { background: "transparent" },
  staleBadge: {
    marginTop: "3rem", background: colors.surface, color: colors.accent,
    border: `1px solid ${colors.accent}`, padding: "0.375rem 0.875rem",
    fontSize: font.size.sm, fontWeight: 700, position: "sticky", top: "3rem",
  },
  muted: { margin: "0.75rem", color: "#c9d1d9", fontSize: font.size.sm },
};
