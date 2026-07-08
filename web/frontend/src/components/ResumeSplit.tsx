import { useEffect, useRef, useState, type CSSProperties } from "react";
import { colors, font } from "../theme";
import { getTex, saveTex, discardTex } from "../api/jobs";
import { useAutoCompile } from "../hooks/useAutoCompile";
import { PdfPreview } from "./PdfPreview";
import { ReorderPanel } from "./ReorderPanel";

interface Props {
  jobId: string;
  /** Fires after save/discard so the workspace can resync has_manual_edits. */
  onEditsChanged: () => void;
}

const SAVE_DEBOUNCE_MS = 1800;

/** Overleaf-style live editor: .tex source on the left, compiled preview on
 *  the right. Edits auto-save and auto-compile a moment after typing stops
 *  (issues #70/#71 follow-up). The buffer seeds from the AI-tailored source
 *  or the last saved edit. */
export function ResumeSplit({ jobId, onEditsChanged }: Props) {
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

  if (loading) return <p style={s.muted}>Loading resume source…</p>;
  if (loadError) {
    return (
      <div style={s.errorBox}>
        <p style={s.error}>{loadError}</p>
        <button style={s.btn} onClick={load}>Retry</button>
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

  return (
    <div style={s.split}>
      {/* Left: .tex source */}
      <div style={s.editorPane}>
        <div style={s.toolbar}>
          {source === "edited" && (
            <button style={{ ...s.btn, color: colors.error, borderColor: colors.error }} onClick={handleDiscard}>
              Discard edits
            </button>
          )}
          <span style={s.sourceTag}>
            {source === "edited" ? "manually edited" : "AI-generated"}
            {saveStatus ? ` · ${saveStatus}` : ""}
          </span>
        </div>

        {saveError && <pre style={s.saveError}>{saveError}</pre>}

        <ReorderPanel tex={tex} onChange={setTex} />

        <textarea
          style={s.texArea}
          value={tex}
          onChange={e => setTex(e.target.value)}
          spellCheck={false}
          wrap="off"
        />
      </div>

      {/* Right: live compiled preview */}
      <div style={s.previewPane}>
        <PdfPreview
          pdfData={compile.pdfData}
          compiling={compile.compiling}
          error={compile.error}
          paused={compile.paused}
          onRecompile={compile.recompileNow}
        />
      </div>
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  split: { display: "flex", flex: 1, minHeight: 0, gap: "0.75rem" },
  editorPane: {
    flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: "0.5rem",
  },
  previewPane: {
    flex: 1, minWidth: 0, display: "flex", flexDirection: "column",
  },
  toolbar: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap", flexShrink: 0, minHeight: "1.5rem" },
  btn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.25rem 0.625rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  sourceTag: { color: colors.textMuted, fontSize: "0.7rem", fontStyle: "italic" },
  muted: { margin: 0, color: colors.textMuted, fontSize: font.size.sm },
  errorBox: { display: "flex", flexDirection: "column", gap: "0.5rem" },
  error: { margin: 0, color: colors.error, fontSize: font.size.sm },
  saveError: {
    margin: 0, color: colors.error, fontSize: "0.7rem", whiteSpace: "pre-wrap",
    wordBreak: "break-word", background: colors.surface,
    border: `1px solid ${colors.error}`, padding: "0.5rem 0.625rem",
    maxHeight: "6rem", overflowY: "auto", flexShrink: 0,
  },
  texArea: {
    flex: 1, minHeight: 0,
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.5rem 0.75rem",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    outline: "none", borderRadius: 0, resize: "none", lineHeight: 1.45,
    whiteSpace: "pre", overflow: "auto",
  },
};
