import { useCallback, useEffect, useRef, useState } from "react";
import { CompileScheduler } from "../lib/compileScheduler";
import { previewPdf } from "../api/jobs";

export interface AutoCompile {
  pdfData: Uint8Array | null;
  /** The exact buffer the displayed PDF was compiled from (drag-reorder
      staleness checks compare this against the live buffer). */
  compiledTex: string | null;
  compiling: boolean;
  error: string | null;
  /** Auto-compile hit the daily quota and is paused; recompileNow retries. */
  paused: boolean;
  recompileNow: () => void;
}

/** Debounced live compile of the .tex buffer (Overleaf-style). Pass
 *  `ready=false` until the buffer has loaded; the first ready buffer compiles
 *  immediately, subsequent edits on the debounce trailing edge. */
export function useAutoCompile(jobId: string, tex: string, ready: boolean): AutoCompile {
  const [pdfData, setPdfData] = useState<Uint8Array | null>(null);
  const [compiledTex, setCompiledTex] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const schedRef = useRef<CompileScheduler | null>(null);
  const texRef = useRef(tex);
  texRef.current = tex;

  useEffect(() => {
    const sched = new CompileScheduler({
      compile: (t, signal) => previewPdf(jobId, t, signal),
      onResult: (pdf, t) => {
        setPdfData(pdf);
        setCompiledTex(t);
        setError(null);
        setPaused(false);
      },
      onError: (msg, fatal) => {
        setError(msg);
        if (fatal) setPaused(true);
      },
      onStateChange: setCompiling,
    });
    schedRef.current = sched;
    return () => {
      sched.dispose();
      schedRef.current = null;
    };
  }, [jobId]);

  const flushedRef = useRef(false);
  useEffect(() => {
    if (!ready) return;
    if (!flushedRef.current) {
      flushedRef.current = true;
      schedRef.current?.flush(tex);
    } else {
      schedRef.current?.input(tex);
    }
  }, [tex, ready]);

  const recompileNow = useCallback(() => {
    schedRef.current?.flush(texRef.current);
  }, []);

  return { pdfData, compiledTex, compiling, error, paused, recompileNow };
}
