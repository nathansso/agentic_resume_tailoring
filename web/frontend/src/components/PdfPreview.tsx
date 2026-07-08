import { useEffect, useRef, useState, type CSSProperties } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { colors, font } from "../theme";

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

interface Props {
  pdfData: Uint8Array | null;
  compiling: boolean;
  error: string | null;
  /** Auto-compile is paused (daily quota); show the manual recovery hint. */
  paused: boolean;
  onRecompile: () => void;
}

const PAGE_GAP = 12;

/** Renders the compiled PDF onto canvases via pdf.js — no iframe, so no
 *  browser PDF-viewer chrome. The last good render stays visible while a new
 *  one compiles or fails (flicker-free swap). */
export function PdfPreview({ pdfData, compiling, error, paused, onRecompile }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const pagesRef = useRef<HTMLDivElement>(null);
  const docRef = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const renderGen = useRef(0);
  const [width, setWidth] = useState(0);
  const [hasRender, setHasRender] = useState(false);

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
      }
      if (gen !== renderGen.current) {
        void doc.destroy();
        return;
      }
      // Swap only after every page rendered — the old render stays up until now.
      pagesRef.current?.replaceChildren(...canvases);
      void docRef.current?.destroy();
      docRef.current = doc;
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

  return (
    <div style={s.pane}>
      <div style={s.statusBar}>
        <span style={s.statusText}>{compiling ? "Compiling…" : hasRender ? "Preview up to date" : ""}</span>
        {(error || paused) && (
          <button style={s.recompileBtn} onClick={onRecompile}>Recompile</button>
        )}
      </div>
      {error && <pre style={s.compileError}>{error}</pre>}
      <div ref={containerRef} style={s.scroll}>
        <div ref={pagesRef} />
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
  muted: { margin: "0.75rem", color: "#c9d1d9", fontSize: font.size.sm },
};
