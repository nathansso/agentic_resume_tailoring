import { useState, useEffect, useRef, type CSSProperties, type ChangeEvent } from "react";
import { colors, font } from "../theme";
import {
  ingestResume, ingestGithub, ingestGithubRepo,
  ingestLinkedin, ingestLinkedinPdf,
} from "../api/ingest";
import { getGithubStatus } from "../api/auth";
import { ProgressBar } from "./ProgressBar";

export type IngestTab = "resume" | "github" | "linkedin";
type LinkedinMode = "url" | "pdf";

const TAB_LABELS: Record<IngestTab, string> = {
  resume: "Resume",
  github: "GitHub",
  linkedin: "LinkedIn",
};

interface GithubStatus {
  connected: boolean;
  oauthConfigured: boolean;
  username: string | null;
}

interface Props {
  initialTab?: IngestTab;
}

export function IngestPanel({ initialTab }: Props) {
  const [tab, setTab] = useState<IngestTab>(initialTab ?? "resume");
  const [file, setFile] = useState<File | null>(null);
  const [githubStatus, setGithubStatus] = useState<GithubStatus | null>(null);
  const [repoInput, setRepoInput] = useState("");
  const [usernameInput, setUsernameInput] = useState("");
  const [linkedinMode, setLinkedinMode] = useState<LinkedinMode>("url");
  const [linkedinInput, setLinkedinInput] = useState("");
  const [linkedinPdf, setLinkedinPdf] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingLabel, setLoadingLabel] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const linkedinPdfRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getGithubStatus()
      .then(s => setGithubStatus({
        connected: s.connected,
        oauthConfigured: s.oauth_configured,
        username: s.github_username,
      }))
      .catch(() => setGithubStatus({ connected: false, oauthConfigured: false, username: null }));
  }, []);

  function reset() { setResult(null); setError(null); }

  async function run(label: string, fn: () => Promise<{ result: string }>) {
    setLoading(true); setLoadingLabel(label); reset();
    try {
      const { result: r } = await fn();
      setResult(r);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ingestion failed");
    } finally {
      setLoading(false);
      setLoadingLabel("");
    }
  }

  function handleIngestResume() {
    if (!file || loading) return;
    void run(`Ingesting ${file.name}…`, () => ingestResume(file));
  }

  function handleIngestConnectedGithub() {
    if (loading) return;
    const who = githubStatus?.username ? `@${githubStatus.username}` : "your GitHub account";
    void run(`Importing repositories from ${who}…`, () => ingestGithub());
  }

  function handleIngestGithubUsername() {
    if (!usernameInput.trim() || loading) return;
    void run(`Importing repositories from @${usernameInput.trim()}…`,
      () => ingestGithub(usernameInput.trim()));
  }

  function handleIngestGithubRepo() {
    if (!repoInput.trim() || loading) return;
    void run(`Importing ${repoInput.trim()}…`, () => ingestGithubRepo(repoInput.trim()));
  }

  function handleIngestLinkedin() {
    if (loading) return;
    if (linkedinMode === "url" ? !linkedinInput.trim() : !linkedinPdf) return;
    void run(
      linkedinMode === "url" ? "Importing LinkedIn profile…" : `Ingesting ${linkedinPdf?.name}…`,
      () => linkedinMode === "url"
        ? ingestLinkedin(linkedinInput.trim())
        : ingestLinkedinPdf(linkedinPdf as File),
    );
  }

  function renderGithubTab() {
    if (!githubStatus) {
      return <p style={s.hint}>Checking GitHub connection…</p>;
    }

    return (
      <div style={s.section}>
        {githubStatus.oauthConfigured ? (
          githubStatus.connected ? (
            <>
              <p style={s.hint}>
                Connected as{" "}
                <span style={{ color: colors.accent }}>
                  {githubStatus.username ? `@${githubStatus.username}` : "your GitHub account"}
                </span>
                . Import your repositories to extract skills and projects.
              </p>
              <button
                style={{ ...s.ingestBtn, opacity: loading ? 0.5 : 1 }}
                onClick={handleIngestConnectedGithub}
                disabled={loading}
              >
                Import My Repositories
              </button>
              <p style={s.hint}>Manage the connection from the Profile menu (top right).</p>
            </>
          ) : (
            <>
              <p style={s.hint}>
                Connect your GitHub account to import your repositories — including private
                ones — and extract skills and projects.
              </p>
              <a href="/api/auth/github" style={s.connectBtn}>Connect GitHub</a>
            </>
          )
        ) : (
          <>
            <p style={s.hint}>
              Enter a public GitHub username to import that account's repositories.
            </p>
            <input
              style={s.textInput}
              placeholder="e.g. octocat"
              value={usernameInput}
              onChange={e => setUsernameInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleIngestGithubUsername()}
            />
            <button
              style={{ ...s.ingestBtn, opacity: usernameInput.trim() && !loading ? 1 : 0.5 }}
              onClick={handleIngestGithubUsername}
              disabled={!usernameInput.trim() || loading}
            >
              Import Repositories
            </button>
          </>
        )}

        <div style={s.divider} />
        <p style={s.hint}>Or import a single public repository:</p>
        <input
          style={s.textInput}
          placeholder="owner/repo, e.g. openai/evals"
          value={repoInput}
          onChange={e => setRepoInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleIngestGithubRepo()}
        />
        <button
          style={{ ...s.secondaryBtn, opacity: repoInput.trim() && !loading ? 1 : 0.5 }}
          onClick={handleIngestGithubRepo}
          disabled={!repoInput.trim() || loading}
        >
          Import Single Repo
        </button>
      </div>
    );
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
            Ingest Resume
          </button>
        </div>
      )}

      {tab === "github" && renderGithubTab()}

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
            Import LinkedIn
          </button>
        </div>
      )}

      {loading && (
        <div style={s.progressWrap}>
          <ProgressBar label={loadingLabel || "Working…"} />
        </div>
      )}

      {(result || error) && !loading && (
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
  secondaryBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.sm, padding: "0.375rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0, alignSelf: "flex-start",
  },
  connectBtn: {
    background: colors.accent, border: "none", color: colors.background,
    fontWeight: 700, fontSize: font.size.base, padding: "0.5rem 1rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
    textDecoration: "none", display: "inline-block", alignSelf: "flex-start",
  },
  divider: { borderTop: `1px solid ${colors.primary}`, margin: "0.5rem 0" },
  modeRow: { display: "flex", flexDirection: "column", gap: "0.375rem" },
  modeLabel: { display: "flex", alignItems: "center", gap: "0.5rem", color: colors.text, fontSize: font.size.sm, cursor: "pointer" },
  textInput: {
    background: colors.background, border: `1px solid ${colors.primary}`,
    color: colors.text, fontSize: font.size.base, padding: "0.375rem 0.75rem",
    fontFamily: "inherit", outline: "none", borderRadius: 0,
  },
  progressWrap: { marginTop: "1rem" },
  resultBox: {
    marginTop: "1rem", background: colors.surface,
    border: `1px solid ${colors.primary}`, padding: "0.75rem",
  },
  resultText: {
    margin: 0, fontFamily: "inherit", fontSize: font.size.sm,
    whiteSpace: "pre-wrap", wordBreak: "break-word", lineHeight: 1.6,
  },
};
