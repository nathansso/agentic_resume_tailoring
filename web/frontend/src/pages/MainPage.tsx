import { useState, useEffect, useRef, type CSSProperties } from "react";
import { useAuth } from "../context/AuthContext";
import { colors, font } from "../theme";
import { JobSidebar } from "../components/JobSidebar";
import { ChatPanel } from "../components/ChatPanel";
import { ProfilePanel } from "../components/ProfilePanel";
import { DataExplorer } from "../components/DataExplorer";
import { IngestPanel, type IngestTab } from "../components/IngestPanel";
import { JobWorkspace } from "../components/JobWorkspace";
import { WelcomePanel } from "../components/WelcomePanel";
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
          : <p style={{ color: colors.textMuted, padding: "1rem", fontSize: font.size.sm }}>Loading job…</p>;
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

  return (
    <div style={s.page}>
      {/* Header */}
      <header style={s.header}>
        <span style={s.brand}>ART</span>
        <nav style={s.nav}>
          {navItems.map(({ key, label }) => (
            <button
              key={key}
              style={{ ...s.navBtn, ...(activeView === key ? s.navBtnActive : {}) }}
              onClick={() => setActiveView(key)}
            >
              {label}
            </button>
          ))}
          {selectedJob && (
            <button
              style={{ ...s.navBtn, ...(activeView === "job" ? s.navBtnActive : {}) }}
              onClick={() => setActiveView("job")}
            >
              Job
            </button>
          )}
        </nav>
        <div style={s.headerRight} ref={menuRef}>
          <button
            style={{ ...s.userBtn, ...(menuOpen || activeView === "profile" ? s.userBtnActive : {}) }}
            onClick={() => setMenuOpen(o => !o)}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
          >
            {user?.name} <span style={s.caret}>▾</span>
          </button>
          {menuOpen && (
            <div style={s.menu} role="menu">
              <button
                style={s.menuItem}
                role="menuitem"
                onClick={() => { setActiveView("profile"); setMenuOpen(false); }}
              >
                Profile
              </button>
              <div style={s.menuDivider} />
              <button style={s.menuItem} role="menuitem" onClick={logout}>
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Body */}
      <div style={s.body}>
        <JobSidebar
          jobs={jobs}
          selectedJobId={selectedJobId}
          onSelect={handleSelectJob}
          onCreate={handleCreateJob}
          onDelete={handleDeleteJob}
          loading={jobsLoading}
        />
        <main style={s.main}>
          {renderMain()}
        </main>
      </div>
    </div>
  );
}


const s: Record<string, CSSProperties> = {
  page: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    overflow: "hidden",
    background: colors.background,
    color: colors.text,
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: "1rem",
    height: "2.25rem",
    padding: "0 1rem",
    background: colors.boost,
    borderBottom: `1px solid ${colors.primary}`,
    flexShrink: 0,
  },
  brand: {
    fontWeight: 700,
    fontSize: font.size.base,
    color: colors.accent,
    letterSpacing: "0.1em",
    marginRight: "0.5rem",
  },
  nav: {
    display: "flex",
    gap: "0.25rem",
    flex: 1,
  },
  navBtn: {
    background: "transparent",
    border: "none",
    color: colors.textMuted,
    fontSize: font.size.sm,
    padding: "0.125rem 0.5rem",
    cursor: "pointer",
    fontFamily: "inherit",
    borderRadius: 0,
  },
  navBtnActive: {
    color: colors.accent,
    borderBottom: `1px solid ${colors.accent}`,
  },
  headerRight: {
    position: "relative",
    display: "flex",
    alignItems: "center",
    flexShrink: 0,
  },
  userBtn: {
    padding: "0.125rem 0.5rem",
    borderRadius: 0,
    background: "transparent",
    color: colors.text,
    border: `1px solid ${colors.primary}`,
    cursor: "pointer",
    fontSize: font.size.sm,
    fontFamily: "inherit",
  },
  userBtnActive: {
    color: colors.accent,
    borderColor: colors.accent,
  },
  caret: {
    color: colors.textMuted,
    fontSize: "0.7rem",
  },
  menu: {
    position: "absolute",
    top: "calc(100% + 0.25rem)",
    right: 0,
    minWidth: "10rem",
    background: colors.surface,
    border: `1px solid ${colors.primary}`,
    display: "flex",
    flexDirection: "column",
    zIndex: 10,
  },
  menuItem: {
    background: "transparent",
    border: "none",
    color: colors.text,
    fontSize: font.size.sm,
    fontFamily: "inherit",
    textAlign: "left",
    padding: "0.5rem 0.75rem",
    cursor: "pointer",
    borderRadius: 0,
  },
  menuDivider: {
    borderTop: `1px solid ${colors.primary}`,
  },
  body: {
    display: "flex",
    flex: 1,
    overflow: "hidden",
  },
  main: {
    flex: 1,
    background: colors.background,
    overflowY: "auto",
  },
};
