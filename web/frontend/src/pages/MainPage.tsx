import { useState, useEffect, type CSSProperties } from "react";
import { useAuth } from "../context/AuthContext";
import { colors, font } from "../theme";
import { JobSidebar } from "../components/JobSidebar";
import { ChatPanel } from "../components/ChatPanel";
import { ProfilePanel } from "../components/ProfilePanel";
import { DataExplorer } from "../components/DataExplorer";
import { IngestPanel } from "../components/IngestPanel";
import { JobDetailPanel } from "../components/JobDetailPanel";
import { WelcomePanel } from "../components/WelcomePanel";
import { listJobs, createJob, deleteJob, getJob } from "../api/jobs";
import type { JobListItem, JobDetail } from "../types";

type ActiveView = "chat" | "data" | "ingest" | "profile" | "job";

export function MainPage() {
  const { user, logout } = useAuth();
  const [jobs, setJobs] = useState<JobListItem[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const [selectedJob, setSelectedJob] = useState<JobDetail | null>(null);
  const [activeView, setActiveView] = useState<ActiveView>("chat");
  const [jobsLoading, setJobsLoading] = useState(true);

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

  function handleSelectJob(jobId: string) {
    setSelectedJobId(jobId);
    setActiveView("chat");
  }

  async function handleCreateJob(title: string, company: string) {
    try {
      const job = await createJob(title, company);
      setJobs(prev => [job, ...prev]);
      setSelectedJobId(job.job_id);
      setActiveView("chat");
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
        return <IngestPanel />;
      case "profile":
        return <ProfilePanel />;
      case "job":
        return selectedJob
          ? <JobDetailPanel job={selectedJob} onJobUpdate={handleJobUpdate} onViewChange={v => setActiveView(v as ActiveView)} />
          : <ChatPanel jobId={selectedJobId} onViewChange={v => setActiveView(v as ActiveView)} />;
      default:
        if (!selectedJobId && jobs.length === 0 && !jobsLoading) {
          return <WelcomePanel onViewChange={v => setActiveView(v as ActiveView)} />;
        }
        return <ChatPanel jobId={selectedJobId} onViewChange={v => setActiveView(v as ActiveView)} />;
    }
  }

  const navItems: { key: ActiveView; label: string }[] = [
    { key: "chat", label: "Chat" },
    { key: "data", label: "Data" },
    { key: "ingest", label: "Ingest" },
    { key: "profile", label: "Profile" },
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
        <div style={s.headerRight}>
          <span style={s.userName}>{user?.name}</span>
          <button onClick={logout} style={s.signOut}>sign out</button>
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
    display: "flex",
    alignItems: "center",
    gap: "1rem",
    flexShrink: 0,
  },
  userName: {
    color: colors.textMuted,
    fontSize: font.size.sm,
  },
  signOut: {
    padding: "0.125rem 0.5rem",
    borderRadius: 0,
    background: "transparent",
    color: colors.text,
    border: `1px solid ${colors.primary}`,
    cursor: "pointer",
    fontSize: font.size.sm,
    fontFamily: "inherit",
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
