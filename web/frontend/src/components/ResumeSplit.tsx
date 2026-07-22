import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "../lib/utils";
import { getTex, saveTex, discardTex } from "../api/jobs";
import { useAutoCompile } from "../hooks/useAutoCompile";
import { moveBulletTo, moveSectionTo } from "../lib/texStructure";
import { clampEditorFraction, editorFractionFromPointer, readStoredNumber, MAX_EDITOR_FRACTION } from "../lib/paneResize";
import { PdfPreview } from "./PdfPreview";
import { ResizeDivider } from "./ResizeDivider";

const EDITOR_FRACTION_KEY = "art:jobs:editorFraction";

export type ResumeView = "split" | "source" | "preview";

interface Props {
  jobId: string;
  /** Pane layout — owned by the workspace so the chat column can widen when
   *  only one resume pane is visible. */
  view: ResumeView;
  onViewChange: (view: ResumeView) => void;
  /** Fires after save/discard so the workspace can resync has_manual_edits. */
  onEditsChanged: () => void;
}

const SAVE_DEBOUNCE_MS = 1800;

const btn =
  "rounded-md border border-border px-2.5 py-1 text-sm transition-colors hover:bg-secondary";

/** Overleaf-style live editor: .tex source on the left, compiled preview on
 *  the right. Edits auto-save and auto-compile a moment after typing stops
 *  (issues #70/#71 follow-up). The buffer seeds from the AI-tailored source
 *  or the last saved edit. */
export function ResumeSplit({ jobId, view, onViewChange, onEditsChanged }: Props) {
  const [tex, setTex] = useState("");
  const [savedTex, setSavedTex] = useState("");
  const [source, setSource] = useState<"edited" | "generated">("generated");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  // Out-of-order save protection: only the latest save applies its state.
  const saveGen = useRef(0);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const texAreaRef = useRef<HTMLTextAreaElement>(null);
  // Editor pane's share of the split (0..1); preview fills the rest (#90).
  // `editorFrac` is the user's intent; `effectiveFrac` (below) is that intent
  // re-fitted to the live pane size so the preview always keeps full height.
  const splitRef = useRef<HTMLDivElement | null>(null);
  const splitRoRef = useRef<ResizeObserver | null>(null);
  const [editorFrac, setEditorFrac] = useState<number>(
    () => readStoredNumber(localStorage.getItem(EDITOR_FRACTION_KEY)) ?? 0.5,
  );
  const [splitWidth, setSplitWidth] = useState(0);

  // Attach a ResizeObserver via a *callback ref* rather than a mount effect:
  // the split div is only rendered once the tex has loaded (before that the
  // component early-returns a "Loading…" placeholder), so an empty-deps mount
  // effect would run while the node is still absent and never re-bind. The
  // callback ref fires exactly when the node mounts/unmounts, so `splitWidth`
  // is always measured — without it the fraction stays pinned at the minimum
  // and the editor↔preview divider can't move (#90).
  const attachSplit = useCallback((el: HTMLDivElement | null) => {
    splitRoRef.current?.disconnect();
    splitRef.current = el;
    if (!el) return;
    const ro = new ResizeObserver(entries => {
      setSplitWidth(entries[entries.length - 1].contentRect.width);
    });
    ro.observe(el);
    splitRoRef.current = ro;
    setSplitWidth(el.clientWidth);
  }, []);

  const effectiveFrac = clampEditorFraction(editorFrac, splitWidth);

  const dirty = tex !== savedTex;
  const ready = !loading && !loadError && tex !== "";
  const compile = useAutoCompile(jobId, tex, ready);

  async function load() {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await getTex(jobId);
      setTex(res.tex);
      setSavedTex(res.tex);
      setSource(res.source);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load .tex");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  // Clicking into Split expands the editor to fill the width the chat frees up
  // as it condenses (JobWorkspace snaps the chat to its floor). Setting the
  // intent to the max fraction lets clampEditorFraction pin the preview at its
  // own floor and hand the rest to the source — "source slides out to fill the
  // gap." Fires only on entering Split, so the divider stays freely draggable
  // afterward (a drag sets a smaller fraction that sticks until the next entry).
  useEffect(() => {
    if (view === "split") setEditorFrac(MAX_EDITOR_FRACTION);
  }, [view]);

  // Auto-save on the debounce trailing edge (Overleaf-style persistence).
  // Skips blank buffers (the server rejects them) — those stay dirty.
  useEffect(() => {
    if (!ready || !dirty || !tex.trim()) return;
    const timer = setTimeout(() => {
      const gen = ++saveGen.current;
      setSaving(true);
      setSaveError(null);
      saveTex(jobId, tex)
        .then(() => {
          if (gen !== saveGen.current) return;
          setSavedTex(tex);
          if (sourceRef.current === "generated") {
            setSource("edited");
            onEditsChanged(); // has_manual_edits flipped — resync the workspace
          }
        })
        .catch(e => {
          if (gen !== saveGen.current) return;
          setSaveError(e instanceof Error ? e.message : "Save failed");
        })
        .finally(() => {
          if (gen === saveGen.current) setSaving(false);
        });
    }, SAVE_DEBOUNCE_MS);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tex, ready, dirty]);

  // Unsettled keystrokes (not yet auto-saved) would be lost on refresh.
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  /** Apply a drag-reorder result: edit the buffer like typing would, then
   *  compile immediately so the preview (and drag handles) catch up fast. */
  function applyReorder(next: string | null) {
    if (!next || next === tex) return;
    setTex(next);
    compile.compileNow(next);
  }

  /** Double-click on the preview → reveal the source pane (if hidden), scroll
   *  the matching tex line into view, and select it (Overleaf-style sync). */
  function jumpToLine(line: number) {
    if (view === "preview") onViewChange("split");
    // Defer until after the view switch re-renders (display:none → visible).
    window.setTimeout(() => {
      const ta = texAreaRef.current;
      if (!ta) return;
      const lines = tex.split("\n");
      if (line < 0 || line >= lines.length) return;
      let start = 0;
      for (let i = 0; i < line; i++) start += lines[i].length + 1;
      ta.focus();
      ta.setSelectionRange(start, start + lines[line].length);
      const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 18;
      ta.scrollTop = Math.max(0, line * lineHeight - ta.clientHeight / 2);
    }, 0);
  }

  function handleSplitDrag(clientX: number) {
    const el = splitRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setEditorFrac(editorFractionFromPointer(clientX, rect.left, rect.width));
  }

  function persistEditorFrac() {
    setEditorFrac(f => {
      localStorage.setItem(EDITOR_FRACTION_KEY, String(f));
      return f;
    });
  }

  function resetEditorFrac() {
    localStorage.removeItem(EDITOR_FRACTION_KEY);
    setEditorFrac(0.5);
  }

  async function handleDiscard() {
    if (!window.confirm("Discard your manual edits and reset to the AI-tailored resume?")) return;
    setSaveError(null);
    saveGen.current++; // invalidate any in-flight save
    try {
      await discardTex(jobId);
      onEditsChanged();
      await load();
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : "Discard failed");
    }
  }

  if (loading) return <p className="text-sm text-muted-foreground">Loading resume source…</p>;
  if (loadError) {
    return (
      <div className="flex flex-col gap-2">
        <p className="text-sm text-destructive">{loadError}</p>
        <button className={cn(btn, "self-start")} onClick={load}>Retry</button>
      </div>
    );
  }

  const saveStatus = saving
    ? "Saving…"
    : dirty
      ? "unsaved changes"
      : source === "edited"
        ? "Saved"
        : "";

  const views: { key: ResumeView; label: string }[] = [
    { key: "split", label: "Split" },
    { key: "source", label: "Source" },
    { key: "preview", label: "Preview" },
  ];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* One shared toolbar: view toggle + edit state */}
      <div className="flex min-h-6 flex-shrink-0 flex-wrap items-center gap-2">
        <div className="flex rounded-md border border-border p-0.5">
          {views.map(v => (
            <button
              key={v.key}
              className={cn(
                "rounded px-2.5 py-1 text-sm transition-colors",
                view === v.key
                  ? "bg-accent/10 text-accent"
                  : "text-muted-foreground hover:text-foreground"
              )}
              onClick={() => onViewChange(v.key)}
            >
              {v.label}
            </button>
          ))}
        </div>
        {source === "edited" && (
          <button
            className="rounded-md border border-destructive/60 px-2.5 py-1 text-sm text-destructive transition-colors hover:bg-destructive/10"
            onClick={handleDiscard}
          >
            Discard edits
          </button>
        )}
        <span className="text-[0.7rem] italic text-muted-foreground">
          {source === "edited" ? "manually edited" : "AI-generated"}
          {saveStatus ? ` · ${saveStatus}` : ""}
        </span>
      </div>

      {saveError && (
        <pre className="m-0 max-h-24 flex-shrink-0 overflow-y-auto whitespace-pre-wrap break-words rounded-md border border-destructive bg-card px-2.5 py-2 font-mono text-[0.7rem] text-destructive">
          {saveError}
        </pre>
      )}

      <div className="flex min-h-0 flex-1 gap-2" ref={attachSplit}>
        {/* Left: .tex source (hidden in preview-only view, state retained).
            In split view its width is user-draggable; in source view it fills. */}
        <div
          className={cn(
            "min-w-0 flex-col gap-2",
            view === "preview" ? "hidden" : "flex",
            view === "split" ? "flex-none" : "flex-1"
          )}
          // Computed split fraction (#90) — not expressible as a utility.
          style={view === "split" ? { flexBasis: `${effectiveFrac * 100}%` } : undefined}
        >
          <textarea
            ref={texAreaRef}
            className="min-h-0 flex-1 resize-none overflow-auto whitespace-pre rounded-md border border-input bg-background px-3 py-2 font-mono text-sm leading-[1.45] outline-none transition-colors focus:border-primary"
            value={tex}
            onChange={e => setTex(e.target.value)}
            spellCheck={false}
            wrap="off"
          />
        </div>

        {view === "split" && (
          <ResizeDivider
            ariaLabel="Resize editor and preview panes"
            onDrag={handleSplitDrag}
            onDragEnd={persistEditorFrac}
            onReset={resetEditorFrac}
          />
        )}

        {/* Right: live compiled preview with drag-to-reorder */}
        <div className={cn("min-w-0 flex-1 flex-col", view === "source" ? "hidden" : "flex")}>
          <PdfPreview
            pdfData={compile.pdfData}
            compiling={compile.compiling}
            error={compile.error}
            paused={compile.paused}
            onRecompile={compile.recompileNow}
            overlay={{
              tex: compile.compiledTex,
              // Drag only when the preview reflects the live buffer — indices
              // computed on a stale render must never touch a diverged buffer.
              enabled: !compile.compiling && compile.compiledTex === tex,
              onMoveSection: (key, targetIndex) => applyReorder(moveSectionTo(tex, key, targetIndex)),
              onMoveBullet: (g, from, to) => applyReorder(moveBulletTo(tex, g, from, to)),
              onJumpToLine: jumpToLine,
            }}
          />
        </div>
      </div>
    </div>
  );
}
