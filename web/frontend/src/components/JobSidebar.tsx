import { useState, type CSSProperties } from "react";
import type { JobListItem } from "../types";
import { colors, font } from "../theme";

interface Props {
  jobs: JobListItem[];
  selectedJobId: string | null;
  onSelect: (jobId: string) => void;
  onCreate: (title: string, company: string, description: string) => void;
  onDelete: (jobId: string) => void;
  loading: boolean;
}

function atsColor(score: number | null): string {
  if (score === null) return colors.textMuted;
  if (score >= 70) return colors.accent;
  if (score >= 50) return "#d29922";
  return colors.error;
}

function statusLabel(status: string): string {
  return status === "created" ? "" : ` [${status}]`;
}

export function JobSidebar({ jobs, selectedJobId, onSelect, onCreate, onDelete, loading }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [description, setDescription] = useState("");
  const [hoveredId, setHoveredId] = useState<string | null>(null);

  function handleCreate() {
    if (!title.trim() || !company.trim()) return;
    onCreate(title.trim(), company.trim(), description.trim());
    setTitle("");
    setCompany("");
    setDescription("");
    setShowForm(false);
  }

  // Collapsed rail that expands on hover (overlaying the content so panes
  // don't reflow). Stays pinned open while the create form is in use.
  return (
    <div
      style={s.railWrap}
      onMouseEnter={() => setExpanded(true)}
      onMouseLeave={() => {
        if (!showForm) setExpanded(false);
      }}
    >
      <div style={s.rail}>
        <span style={s.railLabel}>JOBS</span>
        {jobs.length > 0 && <span style={s.railCount}>{jobs.length}</span>}
      </div>

      {expanded && (
        <div style={s.panel}>
          {sidebarContent()}
        </div>
      )}
    </div>
  );

  function sidebarContent() {
    return (
      <>
      <div style={s.header}>
        <span style={s.title}>Jobs</span>
        <button style={s.newBtn} onClick={() => setShowForm(v => !v)}>
          {showForm ? "Cancel" : "+ New"}
        </button>
      </div>

      {showForm && (
        <div style={s.form}>
          <input
            style={s.input}
            placeholder="Job title"
            value={title}
            onChange={e => setTitle(e.target.value)}
            autoFocus
          />
          <input
            style={s.input}
            placeholder="Company"
            value={company}
            onChange={e => setCompany(e.target.value)}
            onKeyDown={e => e.key === "Enter" && handleCreate()}
          />
          <textarea
            style={{ ...s.input, resize: "vertical", lineHeight: 1.4 }}
            placeholder="Paste job description (analyzes + tailors automatically)"
            value={description}
            onChange={e => setDescription(e.target.value)}
            rows={5}
          />
          <button style={s.saveBtn} onClick={handleCreate}>Save</button>
        </div>
      )}

      <div style={s.list}>
        {loading && <p style={s.muted}>Loading…</p>}
        {!loading && jobs.length === 0 && (
          <p style={s.muted}>No jobs yet — create one above</p>
        )}
        {jobs.map(job => {
          const selected = job.job_id === selectedJobId;
          const hovered = job.job_id === hoveredId;
          return (
            <div
              key={job.job_id}
              style={{
                ...s.jobRow,
                ...(selected ? s.jobRowSelected : {}),
                ...(hovered && !selected ? s.jobRowHovered : {}),
              }}
              onClick={() => onSelect(job.job_id)}
              onMouseEnter={() => setHoveredId(job.job_id)}
              onMouseLeave={() => setHoveredId(null)}
            >
              <div style={s.jobInfo}>
                <span style={s.jobTitle}>{job.title}</span>
                <span style={s.jobCompany}>
                  {job.company}
                  <span style={{ color: colors.textMuted }}>{statusLabel(job.status)}</span>
                </span>
              </div>
              <div style={s.jobRight}>
                {job.ats_score !== null && (
                  <span style={{ ...s.atsTag, color: atsColor(job.ats_score) }}>
                    {Math.round(job.ats_score)}%
                  </span>
                )}
                <button
                  style={s.delBtn}
                  onClick={e => { e.stopPropagation(); onDelete(job.job_id); }}
                  title="Delete job"
                >
                  ×
                </button>
              </div>
            </div>
          );
        })}
      </div>
      </>
    );
  }
}

const s: Record<string, CSSProperties> = {
  railWrap: {
    position: "relative",
    flexShrink: 0,
    width: "2.5rem",
  },
  rail: {
    width: "2.5rem",
    height: "100%",
    borderRight: `1px solid ${colors.primary}`,
    background: colors.surface,
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: "0.5rem",
    paddingTop: "0.75rem",
    cursor: "pointer",
  },
  railLabel: {
    color: colors.accent,
    fontWeight: 700,
    fontSize: "0.7rem",
    letterSpacing: "0.15em",
    writingMode: "vertical-rl",
  },
  railCount: {
    color: colors.textMuted,
    fontSize: "0.7rem",
    border: `1px solid ${colors.primary}`,
    padding: "0 0.25rem",
  },
  panel: {
    position: "absolute",
    top: 0,
    left: 0,
    bottom: 0,
    width: "calc(32ch + 1.5rem)",
    borderRight: `1px solid ${colors.primary}`,
    background: colors.surface,
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
    zIndex: 20,
    boxShadow: "4px 0 12px rgba(0,0,0,0.4)",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0.625rem 0.75rem",
    borderBottom: `1px solid ${colors.primary}`,
    flexShrink: 0,
  },
  title: {
    fontWeight: 700,
    color: colors.accent,
    fontSize: font.size.base,
    letterSpacing: "0.05em",
  },
  newBtn: {
    background: "transparent",
    border: `1px solid ${colors.primary}`,
    color: colors.text,
    fontSize: font.size.sm,
    padding: "0.125rem 0.5rem",
    cursor: "pointer",
    fontFamily: "inherit",
    borderRadius: 0,
  },
  form: {
    padding: "0.625rem 0.75rem",
    borderBottom: `1px solid ${colors.primary}`,
    display: "flex",
    flexDirection: "column",
    gap: "0.375rem",
    flexShrink: 0,
  },
  input: {
    background: colors.background,
    border: `1px solid ${colors.primary}`,
    color: colors.text,
    fontSize: font.size.sm,
    padding: "0.25rem 0.5rem",
    fontFamily: "inherit",
    outline: "none",
    borderRadius: 0,
  },
  saveBtn: {
    background: colors.accent,
    color: colors.background,
    border: "none",
    fontWeight: 700,
    fontSize: font.size.sm,
    padding: "0.25rem 0.5rem",
    cursor: "pointer",
    fontFamily: "inherit",
    borderRadius: 0,
  },
  list: {
    flex: 1,
    overflowY: "auto",
    padding: "0.375rem 0",
  },
  muted: {
    color: colors.textMuted,
    fontSize: font.size.sm,
    padding: "0.5rem 0.75rem",
    margin: 0,
  },
  jobRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "0.5rem 0.75rem",
    cursor: "pointer",
    borderLeft: "2px solid transparent",
    gap: "0.5rem",
  },
  jobRowSelected: {
    borderLeft: `2px solid ${colors.accent}`,
    background: colors.accentDim,
  },
  jobRowHovered: {
    background: colors.boost,
  },
  jobInfo: {
    display: "flex",
    flexDirection: "column",
    minWidth: 0,
    flex: 1,
  },
  jobTitle: {
    fontSize: font.size.sm,
    color: colors.text,
    fontWeight: 600,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  jobCompany: {
    fontSize: font.size.sm,
    color: colors.textMuted,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  },
  jobRight: {
    display: "flex",
    alignItems: "center",
    gap: "0.375rem",
    flexShrink: 0,
  },
  atsTag: {
    fontSize: "0.7rem",
    fontWeight: 700,
  },
  delBtn: {
    background: "transparent",
    border: "none",
    color: colors.error,
    cursor: "pointer",
    fontSize: font.size.base,
    padding: "0 0.125rem",
    lineHeight: 1,
    fontFamily: "inherit",
  },
};
