import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { colors, font } from "../theme";
import { buildOverlayModel, groupIntoLines, type PdfTextItem } from "../lib/pdfOverlay";
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
  const [hasRender, setHasRender] = useState(false);
  const [page1, setPage1] = useState<Page1Text | null>(null);

  // Track the pane width (debounced) so pages re-render scaled to fit.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    let timer: number | undefined;
    const ro = new ResizeObserver(entries => {
      const w = entries[entries.length - 1].contentRect.width;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => setWidth(w), 150);
    });
    ro.observe(el);
    setWidth(el.clientWidth);
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
        const cssScale = width / page.getViewport({ scale: 1 }).width;
        const viewport = page.getViewport({ scale: cssScale * dpr });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
        canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;
        canvas.style.display = "block";
        canvas.style.background = "#fff";
        canvas.style.marginBottom = `${PAGE_GAP}px`;
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
      setHasRender(true);
    })().catch(() => {
      // Render failure: keep the last good canvases.
    });
  }, [pdfData, width]);

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
              onMoveSection={overlay.onMoveSection}
              onMoveBullet={overlay.onMoveBullet}
              onJumpToLine={overlay.onJumpToLine}
            />
          )}
          {/* A drop/edit takes a compile round-trip to show — make the wait
              unmistakable instead of leaving a silently stale render. */}
          {hasRender && !error && (compiling || (overlay && !overlay.enabled)) && (
            <div style={s.staleVeil}>
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
  pagesWrap: { position: "relative" },
  staleVeil: {
    position: "absolute", top: 0, left: 0, right: 0, bottom: 0, zIndex: 3,
    background: "rgba(13,17,23,0.35)", display: "flex",
    justifyContent: "center", alignItems: "flex-start",
    pointerEvents: "none",
  },
  staleBadge: {
    marginTop: "3rem", background: colors.surface, color: colors.accent,
    border: `1px solid ${colors.accent}`, padding: "0.375rem 0.875rem",
    fontSize: font.size.sm, fontWeight: 700, position: "sticky", top: "3rem",
  },
  muted: { margin: "0.75rem", color: "#c9d1d9", fontSize: font.size.sm },
};
