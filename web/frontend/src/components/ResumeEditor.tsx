import { useEffect, useRef, useState, type CSSProperties } from "react";
import { colors, font } from "../theme";
import { getTex, saveTex, discardTex, previewPdf } from "../api/jobs";
import { ReorderPanel } from "./ReorderPanel";

interface Props {
  jobId: string;
  /** Fires after save/discard so the workspace can resync has_manual_edits. */
  onEditsChanged: () => void;
}

/** Manual .tex editor with save + compile-preview (issue #71). The buffer seeds
 *  from the AI-tailored source (or the last saved edit) and previews compile the
 *  current buffer, so unsaved changes are previewable. */
export function ResumeEditor({ jobId, onEditsChanged }: Props) {
  const [tex, setTex] = useState("");
  const [savedTex, setSavedTex] = useState("");
  const [source, setSource] = useState<"edited" | "generated">("generated");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [compiling, setCompiling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const pdfUrlRef = useRef<string | null>(null);

  const dirty = tex !== savedTex;

  function setPreview(url: string | null) {
    if (pdfUrlRef.current) URL.revokeObjectURL(pdfUrlRef.current);
    pdfUrlRef.current = url;
    setPdfUrl(url);
  }

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
    return () => setPreview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId]);

  async function handleSave() {
    if (!dirty || saving) return;
    setSaving(true);
    setActionError(null);
    try {
      await saveTex(jobId, tex);
      setSavedTex(tex);
      setSource("edited");
      onEditsChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleCompile() {
    if (compiling) return;
    setCompiling(true);
    setActionError(null);
    try {
      const blob = await previewPdf(jobId, tex);
      setPreview(URL.createObjectURL(blob));
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Compile failed");
    } finally {
      setCompiling(false);
    }
  }

  async function handleDiscard() {
    if (!window.confirm("Discard your manual edits and reset to the AI-tailored resume?")) return;
    setActionError(null);
    try {
      await discardTex(jobId);
      setPreview(null);
      onEditsChanged();
      await load();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Discard failed");
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

  return (
    <div style={s.editor}>
      {/* Toolbar */}
      <div style={s.toolbar}>
        <button
          style={{ ...s.btn, ...(dirty ? s.btnPrimary : {}) }}
          onClick={handleSave}
          disabled={!dirty || saving}
        >
          {saving ? "Saving…" : dirty ? "Save" : "Saved"}
        </button>
        <button style={s.btn} onClick={handleCompile} disabled={compiling}>
          {compiling ? "Compiling…" : "Compile preview"}
        </button>
        {source === "edited" && (
          <button style={{ ...s.btn, color: colors.error, borderColor: colors.error }} onClick={handleDiscard}>
            Discard edits
          </button>
        )}
        <span style={s.sourceTag}>
          {source === "edited" ? "manually edited" : "AI-generated"}
          {dirty ? " · unsaved changes" : ""}
        </span>
      </div>

      {actionError && (
        <pre style={s.compileError}>{actionError}</pre>
      )}

      <ReorderPanel tex={tex} onChange={setTex} />

      <textarea
        style={s.texArea}
        value={tex}
        onChange={e => setTex(e.target.value)}
        spellCheck={false}
        wrap="off"
      />

      {pdfUrl ? (
        <iframe src={pdfUrl} style={s.preview} title="Resume preview" />
      ) : (
        <p style={s.muted}>Click “Compile preview” to render the current source as PDF.</p>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  editor: { display: "flex", flexDirection: "column", gap: "0.625rem" },
  toolbar: { display: "flex", alignItems: "center", gap: "0.5rem", flexWrap: "wrap" },
  btn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.25rem 0.625rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  btnPrimary: { background: colors.accent, color: colors.background, borderColor: colors.accent, fontWeight: 700 },
  sourceTag: { color: colors.textMuted, fontSize: "0.7rem", fontStyle: "italic" },
  muted: { margin: 0, color: colors.textMuted, fontSize: font.size.sm },
  errorBox: { display: "flex", flexDirection: "column", gap: "0.5rem" },
  error: { margin: 0, color: colors.error, fontSize: font.size.sm },
  compileError: {
    margin: 0, color: colors.error, fontSize: "0.7rem", whiteSpace: "pre-wrap",
    wordBreak: "break-word", background: colors.surface,
    border: `1px solid ${colors.error}`, padding: "0.5rem 0.625rem",
    maxHeight: "10rem", overflowY: "auto",
  },
  texArea: {
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.5rem 0.75rem",
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
    outline: "none", borderRadius: 0, resize: "vertical", lineHeight: 1.45,
    minHeight: "22rem", whiteSpace: "pre", overflowX: "auto",
  },
  preview: {
    width: "100%", height: "34rem", border: `1px solid ${colors.primary}`,
    background: "#525659",
  },
};
