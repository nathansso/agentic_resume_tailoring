import { useState, useEffect, useRef, type ChangeEvent } from "react";
import { cn } from "../lib/utils";
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

const btnPrimary =
  "self-start rounded-md bg-primary px-4 py-2 font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50";
const btnGhost =
  "self-start rounded-md border border-border px-3 py-1.5 text-sm transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50";
const textInput =
  "rounded-md border border-input bg-background px-3 py-2 outline-none transition-colors placeholder:text-muted-foreground/60 focus:border-primary";

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
      return <p className="text-sm text-muted-foreground">Checking GitHub connection…</p>;
    }

    return (
      <div className="flex flex-col gap-3">
        {githubStatus.oauthConfigured ? (
          githubStatus.connected ? (
            <>
              <p className="text-sm text-muted-foreground">
                Connected as{" "}
                <span className="text-success">
                  {githubStatus.username ? `@${githubStatus.username}` : "your GitHub account"}
                </span>
                . Import your repositories to extract skills and projects.
              </p>
              <button
                className={btnPrimary}
                onClick={handleIngestConnectedGithub}
                disabled={loading}
              >
                Import My Repositories
              </button>
              <p className="text-sm text-muted-foreground">
                Manage the connection from the Profile menu (top right).
              </p>
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Connect your GitHub account to import your repositories — including private
                ones — and extract skills and projects.
              </p>
              <a href="/api/auth/github" className={cn(btnPrimary, "inline-block no-underline")}>
                Connect GitHub
              </a>
            </>
          )
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              Enter a public GitHub username to import that account's repositories.
            </p>
            <input
              className={textInput}
              placeholder="e.g. octocat"
              value={usernameInput}
              onChange={e => setUsernameInput(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleIngestGithubUsername()}
            />
            <button
              className={btnPrimary}
              onClick={handleIngestGithubUsername}
              disabled={!usernameInput.trim() || loading}
            >
              Import Repositories
            </button>
          </>
        )}

        <div className="my-2 border-t border-border" />
        <p className="text-sm text-muted-foreground">Or import a single public repository:</p>
        <input
          className={textInput}
          placeholder="owner/repo, e.g. openai/evals"
          value={repoInput}
          onChange={e => setRepoInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleIngestGithubRepo()}
        />
        <button
          className={btnGhost}
          onClick={handleIngestGithubRepo}
          disabled={!repoInput.trim() || loading}
        >
          Import Single Repo
        </button>
      </div>
    );
  }

  return (
    <div className="max-w-[64ch] p-6">
      <h2 className="mb-5 text-xl font-bold tracking-tight">Ingest</h2>

      <div className="mb-5 flex gap-1 border-b border-border">
        {(["resume", "github", "linkedin"] as IngestTab[]).map(t => (
          <button
            key={t}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors",
              tab === t
                ? "border-accent text-accent"
                : "border-transparent text-muted-foreground hover:text-foreground"
            )}
            onClick={() => { setTab(t); reset(); }}
          >
            {TAB_LABELS[t]}
          </button>
        ))}
      </div>

      {tab === "resume" && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            Upload a resume file (PDF, DOCX, or Markdown).
          </p>
          <input
            ref={fileInputRef}
            type="file"
            accept=".pdf,.docx,.md"
            className="hidden"
            onChange={(e: ChangeEvent<HTMLInputElement>) => setFile(e.target.files?.[0] ?? null)}
          />
          <div className="flex items-center gap-3">
            <button className={btnGhost} onClick={() => fileInputRef.current?.click()}>
              Choose file
            </button>
            <span className="text-sm text-muted-foreground">
              {file ? file.name : "No file selected"}
            </span>
          </div>
          <button className={btnPrimary} onClick={handleIngestResume} disabled={!file || loading}>
            Ingest Resume
          </button>
        </div>
      )}

      {tab === "github" && renderGithubTab()}

      {tab === "linkedin" && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            {(["url", "pdf"] as LinkedinMode[]).map(m => (
              <label key={m} className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="linkedin-mode"
                  value={m}
                  checked={linkedinMode === m}
                  onChange={() => { setLinkedinMode(m); reset(); }}
                  className="accent-primary"
                />
                {m === "url" ? "Profile URL (auto-import)" : "Upload PDF export (fallback)"}
              </label>
            ))}
          </div>

          {linkedinMode === "url" ? (
            <>
              <p className="text-sm text-muted-foreground">
                Enter your LinkedIn profile URL or username. It also imports
                automatically when you save your profile.
              </p>
              <input
                className={textInput}
                placeholder="e.g. https://www.linkedin.com/in/username"
                value={linkedinInput}
                onChange={e => setLinkedinInput(e.target.value)}
                onKeyDown={e => e.key === "Enter" && handleIngestLinkedin()}
              />
            </>
          ) : (
            <>
              <p className="text-sm text-muted-foreground">
                Upload a LinkedIn PDF export (Profile → More → Save to PDF).
              </p>
              <input
                ref={linkedinPdfRef}
                type="file"
                accept=".pdf"
                className="hidden"
                onChange={(e: ChangeEvent<HTMLInputElement>) => setLinkedinPdf(e.target.files?.[0] ?? null)}
              />
              <div className="flex items-center gap-3">
                <button className={btnGhost} onClick={() => linkedinPdfRef.current?.click()}>
                  Choose file
                </button>
                <span className="text-sm text-muted-foreground">
                  {linkedinPdf ? linkedinPdf.name : "No file selected"}
                </span>
              </div>
            </>
          )}

          <button
            className={btnPrimary}
            onClick={handleIngestLinkedin}
            disabled={(linkedinMode === "url" ? !linkedinInput.trim() : !linkedinPdf) || loading}
          >
            Import LinkedIn
          </button>
        </div>
      )}

      {loading && (
        <div className="mt-4">
          <ProgressBar label={loadingLabel || "Working…"} />
        </div>
      )}

      {(result || error) && !loading && (
        <div className="mt-4 rounded-lg border border-border bg-card p-3">
          <pre
            className={cn(
              "m-0 whitespace-pre-wrap break-words font-sans text-sm leading-relaxed",
              error ? "text-destructive" : "text-foreground"
            )}
          >
            {error ?? result}
          </pre>
        </div>
      )}
    </div>
  );
}
