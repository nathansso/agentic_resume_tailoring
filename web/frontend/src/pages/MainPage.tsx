import { useState, useEffect, useRef } from "react";
import { ChevronDown } from "lucide-react";
import { useAuth } from "../context/AuthContext";
import { cn } from "../lib/utils";
import { JobSidebar } from "../components/JobSidebar";
import { ChatPanel } from "../components/ChatPanel";
import { ProfilePanel } from "../components/ProfilePanel";
import { DataExplorer } from "../components/DataExplorer";
import { IngestPanel, type IngestTab } from "../components/IngestPanel";
import { JobWorkspace } from "../components/JobWorkspace";
import { WelcomePanel } from "../components/WelcomePanel";
import { ThemeToggle } from "../components/ThemeToggle";
import { listJobs, createJob, deleteJob, getJob } from "../api/jobs";
import type { JobListItem, JobDetail } from "../types";

type ActiveView = "chat" | "data" | "ingest" | "profile" | "job";

// The GitHub OAuth callback redirects to /?github_connected=1 — land the user
// back on the GitHub ingest tab so they can import right away.
function githubConnectRedirect(): boolean {
  return new URLSearchParams(window.location.search).get("github_connected") === "1";
}

export function MainPage() {
  const { user, logout } = useAuth();
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>(() =>
    githubConnectRedirect() ? "ingest" : "chat"
  );
  const [ingestTab] = useState<IngestTab | undefined>(() =>
    githubConnectRedirect() ? "github" : undefined
  );
  const [welcomeDismissed, setWelcomeDismissed] = useState(false);
  const [jobsLoading, setJobsLoading] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Strip the OAuth redirect flag from the URL after consuming it
  useEffect(() => {
    if (githubConnectRedirect()) {
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  // Close the user menu on outside click
  useEffect(() => {
    if (!menuOpen) return;
    function onClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [menuOpen]);

  // Load jobs on mount
  useEffect(() => {
    setJobsLoading(true);
    listJobs()
      .then(setJobs)
      .catch(() => {})
      .finally(() => setJobsLoading(false));
  }, []);

  // Fetch job detail when selection changes
  useEffect(() => {
    if (!selectedJobId) { setSelectedJob(null); return; }
    getJob(selectedJobId).then(setSelectedJob).catch(() => setSelectedJob(null));
  }, [selectedJobId]);

  // Jobs created this session with a pasted JD — the workspace auto-runs
  // analyze + tailor for these (issue #70).
  const autoStartIds = useRef(new Set<string>()).current;

  function handleSelectJob(jobId: string) {
    setSelectedJobId(jobId);
    setActiveView("job");
  }

  async function handleCreateJob(title: string, company: string, description: string) {
    try {
      const job = await createJob(title, company, description);
      if (description.trim()) autoStartIds.add(job.job_id);
      setJobs(prev => [job, ...prev]);
      setSelectedJobId(job.job_id);
      setActiveView("job");
    } catch {}
  }

  async function handleDeleteJob(jobId: string) {
    try {
      await deleteJob(jobId);
      setJobs(prev => prev.filter(j => j.job_id !== jobId));
      if (selectedJobId === jobId) {
        setSelectedJobId(null);
        setSelectedJob(null);
      }
    } catch {}
  }


  function handleJobUpdate(job: JobDetail) {
    setSelectedJob(job);
    setJobs(prev => prev.map(j => j.job_id === job.job_id
      ? { ...j, status: job.status, ats_score: job.ats_score }
      : j
    ));
  }

  function renderMain() {
    switch (activeView) {
      case "data":
        return <DataExplorer />;
      case "ingest":
        return <IngestPanel key={ingestTab ?? "default"} initialTab={ingestTab} />;
      case "profile":
        return <ProfilePanel />;
      case "job":
        return selectedJob
          ? <JobWorkspace
              job={selectedJob}
              autoStart={autoStartIds.has(selectedJob.job_id)}
              onJobUpdate={handleJobUpdate}
              onViewChange={v => setActiveView(v as ActiveView)}
            />
          : <p className="p-4 text-sm text-muted-foreground">Loading job…</p>;
      default:
        if (!welcomeDismissed && !selectedJobId && jobs.length === 0 && !jobsLoading) {
          return <WelcomePanel onViewChange={v => { setWelcomeDismissed(true); setActiveView(v as ActiveView); }} />;
        }
        // The top-nav Chat tab is always the landing chat; job-scoped chat
        // lives inside the Job workspace (issue #70).
        return <ChatPanel jobId={null} onViewChange={v => setActiveView(v as ActiveView)} />;
    }
  }

  const navItems: { key: ActiveView; label: string }[] = [
    { key: "chat", label: "Chat" },
    { key: "data", label: "Data" },
    { key: "ingest", label: "Ingest" },
  ];

  const navBtn = (active: boolean) =>
    cn(
      "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
      active
        ? "bg-accent/10 text-accent"
        : "text-muted-foreground hover:bg-secondary hover:text-foreground"
    );

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      {/* Header */}
      <header className="flex h-12 flex-shrink-0 items-center gap-3 border-b border-border bg-card px-4">
        <span className="flex items-center gap-2 font-bold tracking-tight">
          <span className="grid h-6 w-6 place-items-center rounded bg-primary text-xs text-primary-foreground">
            A
          </span>
          ARTie
        </span>

        <nav className="flex flex-1 gap-1">
          {navItems.map(({ key, label }) => (
            <button
              key={key}
              className={navBtn(activeView === key)}
              onClick={() => setActiveView(key)}
            >
              {label}
            </button>
          ))}
          {selectedJob && (
            <button
              className={navBtn(activeView === "job")}
              onClick={() => setActiveView("job")}
            >
              Job
            </button>
          )}
        </nav>

        <ThemeToggle className="mr-1 flex-shrink-0" />

        <div className="relative flex flex-shrink-0 items-center" ref={menuRef}>
          <button
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm transition-colors",
              menuOpen || activeView === "profile"
                ? "border-primary/50 bg-primary/10 text-foreground"
                : "border-border text-muted-foreground hover:bg-secondary hover:text-foreground"
            )}
            onClick={() => setMenuOpen(o => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            {user?.name}
            <ChevronDown className="h-3.5 w-3.5" />
          </button>
          {menuOpen && (
            <div
              className="absolute right-0 top-[calc(100%+0.375rem)] z-20 flex min-w-[10rem] flex-col overflow-hidden rounded-md border border-border bg-card py-1 shadow-xl"
              role="menu"
            >
              <button
                className="px-3 py-2 text-left text-sm transition-colors hover:bg-secondary"
                role="menuitem"
                onClick={() => { setActiveView("profile"); setMenuOpen(false); }}
              >
                Profile
              </button>
              <div className="my-1 border-t border-border" />
              <button
                className="px-3 py-2 text-left text-sm transition-colors hover:bg-secondary"
                role="menuitem"
                onClick={logout}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Body */}
      <div className="flex flex-1 overflow-hidden">
        <JobSidebar
          jobs={jobs}
          selectedJobId={selectedJobId}
          onSelect={handleSelectJob}
          onCreate={handleCreateJob}
          onDelete={handleDeleteJob}
          loading={jobsLoading}
        />
        <main className="flex-1 overflow-y-auto bg-background">
          {renderMain()}
        </main>
      </div>
    </div>
  );
}
