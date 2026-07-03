import { useState, useRef, type CSSProperties, type ChangeEvent } from "react";
import { colors, font } from "../theme";
import {
  ingestResume, ingestGithub, ingestGithubRepo,
  ingestLinkedin, ingestLinkedinPdf,
} from "../api/ingest";

type IngestTab = "resume" | "github" | "linkedin";
type GithubMode = "user" | "repo";
type LinkedinMode = "url" | "pdf";

const TAB_LABELS: Record<IngestTab, string> = {
  resume: "Resume",
  github: "GitHub",
  linkedin: "LinkedIn",
};

export function IngestPanel() {
  const [tab, setTab] = useState<IngestTab>("resume");
  const [file, setFile] = useState<File | null>(null);
  const [githubMode, setGithubMode] = useState<GithubMode>("user");
  const [githubInput, setGithubInput] = useState("");
  const [linkedinMode, setLinkedinMode] = useState<LinkedinMode>("url");
  const [linkedinInput, setLinkedinInput] = useState("");
  const [linkedinPdf, setLinkedinPdf] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const linkedinPdfRef = useRef<HTMLInputElement>(null);

  function reset() { setResult(null); setError(null); }

  async function handleIngestResume() {
    if (!file) return;
    setLoading(true); reset();
    try {
      const { result: r } = await ingestResume(file);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleIngestGithub() {
    if (!githubInput.trim()) return;
    setLoading(true); reset();
    try {
      const fn = githubMode === "user" ? ingestGithub : ingestGithubRepo;
      const { result: r } = await fn(githubInput.trim());
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleIngestLinkedin() {
    if (linkedinMode === "url" ? !linkedinInput.trim() : !linkedinPdf) return;
    setLoading(true); reset();
    try {
      const { result: r } = linkedinMode === "url"
        ? await ingestLinkedin(linkedinInput.trim())
        : await ingestLinkedinPdf(linkedinPdf as File);
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={s.panel}>
      <h2 style={s.title}>Ingest</h2>

      <div style={s.tabStrip}>
        {(["resume", "github", "linkedin"] as IngestTab[]).map(t => (
          <button
            key={t}
            style={{ ...s.tabBtn, ...(tab === t ? s.tabBtnActive : {}) }}
            onClick={() => { setTab(t); reset(); }}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === "resume" && (
        <div style={s.section}>
          <p style={s.hint}>Upload a resume file (PDF, DOCX, or Markdown).</p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.md"
            style={{ display: "none" }}
            onChange={(e: ChangeEvent<HTMLInputElement>) => setFile(e.target.files?.[0] ?? null)}
          />
          <div style={s.fileRow}>
            <button style={s.chooseBtn} onClick={() => fileInputRef.current?.click()}>
              Choose file
            </button>
            <span style={s.fileName}>{file ? file.name : "No file selected"}</span>
          </div>
          <button
            style={{ ...s.ingestBtn, opacity: file && !loading ? 1 : 0.5 }}
            onClick={handleIngestResume}
            disabled={!file || loading}
          >
            {loading ? "Ingesting…" : "Ingest Resume"}
          </button>
        </div>
      )}

      {tab === "github" && (
        <div style={s.section}>
          <div style={s.modeRow}>
            {(["user", "repo"] as GithubMode[]).map(m => (
              <label key={m} style={s.modeLabel}>
                <input
                  type="radio"
                  name="github-mode"
                  value={m}
                  checked={githubMode === m}
                  onChange={() => setGithubMode(m)}
                  style={{ accentColor: colors.accent }}
                />
                {m === "user" ? "All repos for username" : "Single repo (owner/repo)"}
              </label>
            ))}
          </div>
          <input
            style={s.textInput}
            placeholder={githubMode === "user" ? "e.g. octocat" : "e.g. openai/evals"}
            value={githubInput}
            onChange={e => setGithubInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleIngestGithub()}
          />
          <button
            style={{ ...s.ingestBtn, opacity: githubInput.trim() && !loading ? 1 : 0.5 }}
            onClick={handleIngestGithub}
            disabled={!githubInput.trim() || loading}
          >
            {loading ? "Ingesting… (this may take a minute)" : "Ingest GitHub"}
          </button>
        </div>
      )}

      {tab === "linkedin" && (
        <div style={s.section}>
          <div style={s.modeRow}>
            {(["url", "pdf"] as LinkedinMode[]).map(m => (
              <label key={m} style={s.modeLabel}>
                <input
                  type="radio"
                  name="linkedin-mode"
                  value={m}
                  checked={linkedinMode === m}
                  onChange={() => { setLinkedinMode(m); reset(); }}
                  style={{ accentColor: colors.accent }}
                />
                {m === "url" ? "Profile URL (auto-import)" : "Upload PDF export (fallback)"}
              </label>
            ))}
          </div>

          {linkedinMode === "url" ? (
            <>
              <p style={s.hint}>
                Enter your LinkedIn profile URL or username. It also imports
                automatically when you save your profile.
              </p>
              <input
                style={s.textInput}
                placeholder="e.g. https://www.linkedin.com/in/username"
                value={linkedinInput}
                onChange={e => setLinkedinInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleIngestLinkedin()}
              />
            </>
          ) : (
            <>
              <p style={s.hint}>Upload a LinkedIn PDF export (Profile → More → Save to PDF).</p>
              <input
                ref={linkedinPdfRef}
                type="file"
                accept=".pdf"
                style={{ display: "none" }}
                onChange={(e: ChangeEvent<HTMLInputElement>) => setLinkedinPdf(e.target.files?.[0] ?? null)}
              />
              <div style={s.fileRow}>
                <button style={s.chooseBtn} onClick={() => linkedinPdfRef.current?.click()}>
                  Choose file
                </button>
                <span style={s.fileName}>{linkedinPdf ? linkedinPdf.name : "No file selected"}</span>
              </div>
            </>
          )}

          <button
            style={{
              ...s.ingestBtn,
              opacity: (linkedinMode === "url" ? linkedinInput.trim() : linkedinPdf) && !loading ? 1 : 0.5,
            }}
            onClick={handleIngestLinkedin}
            disabled={(linkedinMode === "url" ? !linkedinInput.trim() : !linkedinPdf) || loading}
          >
            {loading ? "Importing… (this may take a minute)" : "Import LinkedIn"}
          </button>
        </div>
      )}

      {(result || error) && (
        <div style={s.resultBox}>
          <pre style={{ ...s.resultText, color: error ? colors.error : colors.text }}>
            {error ?? result}
          </pre>
        </div>
      )}
    </div>
  );
}

const s: Record<string, CSSProperties> = {
  panel: { padding: "1.5rem", maxWidth: "64ch" },
  title: { margin: "0 0 1.25rem", color: colors.accent, fontSize: font.size.xl, fontWeight: 700 },
  tabStrip: { display: "flex", gap: "0.25rem", borderBottom: `1px solid ${colors.primary}`, marginBottom: "1.25rem" },
  tabBtn: {
    background: "transparent", border: "none", borderBottom: "2px solid transparent",
    color: colors.textMuted, fontSize: font.size.sm, padding: "0.375rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  tabBtnActive: { color: colors.accent, borderBottomColor: colors.accent },
  section: { display: "flex", flexDirection: "column", gap: "0.75rem" },
  hint: { margin: 0, color: colors.textMuted, fontSize: font.size.sm },
  fileRow: { display: "flex", alignItems: "center", gap: "0.75rem" },
  chooseBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.375rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  fileName: { color: colors.textMuted, fontSize: font.size.sm },
  ingestBtn: {
    background: colors.accent, border: "none", color: colors.background,
    fontWeight: 700, fontSize: font.size.base, padding: "0.5rem 1rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0, alignSelf: "flex-start",
  },
  modeRow: { display: "flex", flexDirection: "column", gap: "0.375rem" },
  modeLabel: { display: "flex", alignItems: "center", gap: "0.5rem", color: colors.text, fontSize: font.size.sm, cursor: "pointer" },
  textInput: {
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.base, padding: "0.375rem 0.75rem",
    fontFamily: "inherit", outline: "none", borderRadius: 0,
  },
  resultBox: {
    marginTop: "1rem", background: colors.surface,
    border: `1px solid ${colors.primary}`, padding: "0.75rem",
  },
  resultText: {
    margin: 0, fontFamily: "inherit", fontSize: font.size.sm,
    whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.6,
  },
};
