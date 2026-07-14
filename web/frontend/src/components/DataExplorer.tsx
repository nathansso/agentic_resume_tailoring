import { useState, useEffect, type CSSProperties } from "react";
import type { SkillRow, ExpRow, ProjectRow, EducationRow, AchievementRow, GraphData } from "../types";
import { colors, font } from "../theme";
import {
  getSkills, getExperiences, getProjects, getEducation, getAchievements, getGraph, setSkillCore,
  updateExperience, deleteExperience, updateEducation, deleteEducation, updateProject, deleteProject,
} from "../api/profile";

type Tab = "skills" | "experiences" | "education" | "projects" | "achievements" | "graph" | "charts";

export function DataExplorer() {
  const [tab, setTab] = useState<Tab>("skills");
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [exps, setExps] = useState<ExpRow[]>([]);
  const [education, setEducation] = useState<EducationRow[]>([]);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [achievements, setAchievements] = useState<AchievementRow[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    Promise.all([getSkills(), getExperiences(), getProjects(), getEducation(), getAchievements(), getGraph()])
      .then(([s, e, p, ed, a, g]) => { setSkills(s); setExps(e); setProjects(p); setEducation(ed); setAchievements(a); setGraph(g); })
      .catch(err => setError(err instanceof Error ? err.message : "Failed to load data"))
      .finally(() => setLoading(false));
  }, []);

  // Optimistic pin/unpin of a core skill (issue #54); revert on failure.
  const toggleCore = async (name: string, next: boolean) => {
    setSkills(prev => prev.map(s => (s.name === name ? { ...s, is_core: next } : s)));
    try {
      await setSkillCore(name, next);
    } catch {
      setSkills(prev => prev.map(s => (s.name === name ? { ...s, is_core: !next } : s)));
    }
  };

  const tabs: { key: Tab; label: string }[] = [
    { key: "skills", label: `Skills (${skills.length})` },
    { key: "experiences", label: `Experiences (${exps.length})` },
    { key: "education", label: `Education (${education.length})` },
    { key: "projects", label: `Projects (${projects.length})` },
    { key: "achievements", label: `Achievements (${achievements.length})` },
    { key: "graph", label: "Graph" },
    { key: "charts", label: "Charts" },
  ];

  return (
    <div style={s.panel}>
      <div style={s.tabStrip}>
        {tabs.map(({ key, label }) => (
          <button
            key={key}
            style={{ ...s.tabBtn, ...(tab === key ? s.tabBtnActive : {}) }}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={s.content}>
        {loading && <p style={s.muted}>Loading…</p>}
        {error && <p style={{ ...s.muted, color: colors.error }}>{error}</p>}
        {!loading && !error && tab === "skills" && <SkillsTab skills={skills} onToggleCore={toggleCore} />}
        {!loading && !error && tab === "experiences" && <ExpsTab exps={exps} setExps={setExps} />}
        {!loading && !error && tab === "education" && <EducationTab education={education} setEducation={setEducation} />}
        {!loading && !error && tab === "projects" && <ProjectsTab projects={projects} setProjects={setProjects} />}
        {!loading && !error && tab === "achievements" && <AchievementsTab achievements={achievements} />}
        {!loading && !error && tab === "graph" && <GraphTab graph={graph ?? { top_skills: [], by_category: {}, evidence: {} }} />}
        {!loading && !error && tab === "charts" && <ChartsTab skills={skills} graph={graph} />}
      </div>
    </div>
  );
}

function SkillsTab({
  skills,
  onToggleCore,
}: {
  skills: SkillRow[];
  onToggleCore: (name: string, next: boolean) => void;
}) {
  if (skills.length === 0) return <p style={sInner.empty}>No skills yet — ingest your resume to get started.</p>;

  const byCategory: Record<string, SkillRow[]> = {};
  for (const sk of skills) {
    const cat = sk.category || "Uncategorized";
    (byCategory[cat] = byCategory[cat] ?? []).push(sk);
  }

  return (
    <div style={sInner.skillsRoot}>
      <p style={sInner.pinHint}>★ Pin a skill to always include it in tailored resumes.</p>
      {Object.keys(byCategory).sort().map(cat => (
        <div key={cat} style={sInner.catBlock}>
          <div style={sInner.catHeader}>{cat} <span style={sInner.catCount}>({byCategory[cat].length})</span></div>
          <div style={sInner.skillGrid}>
            {byCategory[cat]
              // Pinned skills first, then alphabetical.
              .sort((a, b) => (Number(b.is_core) - Number(a.is_core)) || a.name.localeCompare(b.name))
              .map(sk => (
              <div key={sk.name} style={{ ...sInner.skillChip, ...(sk.is_core ? sInner.skillChipPinned : {}) }}>
                <button
                  type="button"
                  title={sk.is_core ? "Unpin core skill" : "Pin as core skill"}
                  aria-pressed={sk.is_core}
                  style={{ ...sInner.pinBtn, ...(sk.is_core ? sInner.pinBtnActive : {}) }}
                  onClick={() => onToggleCore(sk.name, !sk.is_core)}
                >
                  {sk.is_core ? "★" : "☆"}
                </button>
                <span style={sInner.skillName}>{sk.name}</span>
                {sk.proficiency !== "N/A" && <span style={sInner.skillMeta}>lvl {sk.proficiency}</span>}
                <div style={sInner.confBarBg}>
                  <div style={{ ...sInner.confBarFill, width: `${Math.min(parseFloat(sk.confidence) * 10, 100)}%` }} />
                </div>
                {sk.source && <span style={sInner.skillSource}>{sk.source}</span>}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

const EXP_COLS = "2fr 1.5fr 1fr 1fr 7.5rem";

function ExpsTab({ exps, setExps }: { exps: ExpRow[]; setExps: (rows: ExpRow[]) => void }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (exps.length === 0) return <p style={sInner.empty}>No experiences yet — ingest your resume to get started.</p>;

  const onDelete = async (row: ExpRow) => {
    if (!window.confirm(`Delete "${row.title} @ ${row.company}"? It won't return on a re-import.`)) return;
    const prev = exps;
    setExps(exps.filter(e => e.id !== row.id));
    setError(null);
    try {
      await deleteExperience(row.id);
    } catch (err) {
      setExps(prev);
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const onSaved = (updated: ExpRow) => {
    setExps(exps.map(e => (e.id === updated.id ? updated : e)));
    setEditingId(null);
  };

  return (
    <div style={sInner.table}>
      {error && <p style={{ ...sInner.empty, color: colors.error, marginBottom: "0.5rem" }}>{error}</p>}
      <div style={{ ...sInner.tableHead, gridTemplateColumns: EXP_COLS }}>
        <span>Title</span><span>Company</span><span>Start</span><span>End</span><span />
      </div>
      {exps.map((e, i) =>
        editingId === e.id ? (
          <ExpEditForm key={e.id} row={e} onSaved={onSaved} onCancel={() => setEditingId(null)} />
        ) : (
          <div key={e.id} style={{ ...sInner.tableRow, gridTemplateColumns: EXP_COLS, background: i % 2 === 0 ? colors.surface : colors.boost }}>
            <span style={sInner.expTitleCell}>
              {e.title}
              {e.incomplete && (
                <span
                  style={sInner.incompleteBadge}
                  title={`Missing ${e.missing.join(", ")} — edit to complete, or delete. Won't appear in tailored resumes until it has details.`}
                >
                  ⚠ {e.missing.join(", ")}
                </span>
              )}
            </span>
            <span style={{ color: colors.textMuted }}>{e.company}</span>
            <span style={{ color: colors.textMuted }}>{e.start}</span>
            <span style={{ color: colors.textMuted }}>{e.end}</span>
            <RowActions onEdit={() => setEditingId(e.id)} onDelete={() => onDelete(e)} />
          </div>
        )
      )}
    </div>
  );
}

function ExpEditForm({ row, onSaved, onCancel }: { row: ExpRow; onSaved: (r: ExpRow) => void; onCancel: () => void }) {
  const [title, setTitle] = useState(row.title);
  const [company, setCompany] = useState(row.company);
  const [start, setStart] = useState(row.start === "?" ? "" : row.start);
  const [end, setEnd] = useState(row.end === "?" ? "" : row.end);
  const [description, setDescription] = useState(row.description);
  const [bulletsText, setBulletsText] = useState((row.bullets || []).join("\n"));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateExperience(row.id, {
        title, company,
        start_date: start.trim() || null,
        end_date: end.trim() || null,
        description: description.trim() || null,
        bullets: bulletsText.split("\n").map(b => b.trim()).filter(Boolean),
      });
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      setSaving(false);
    }
  };

  return (
    <div style={sForm.root}>
      <div style={sForm.grid2}>
        <Field label="Title"><input style={sForm.input} value={title} onChange={e => setTitle(e.target.value)} /></Field>
        <Field label="Company"><input style={sForm.input} value={company} onChange={e => setCompany(e.target.value)} /></Field>
        <Field label="Start"><input style={sForm.input} value={start} onChange={e => setStart(e.target.value)} placeholder="e.g. Jun 2023" /></Field>
        <Field label="End"><input style={sForm.input} value={end} onChange={e => setEnd(e.target.value)} placeholder="e.g. Present" /></Field>
      </div>
      <Field label="Description"><textarea style={sForm.textarea} value={description} onChange={e => setDescription(e.target.value)} /></Field>
      <Field label="Bullets (one per line)"><textarea style={sForm.textarea} value={bulletsText} onChange={e => setBulletsText(e.target.value)} /></Field>
      <FormFooter saving={saving} error={error} onSave={save} onCancel={onCancel} />
    </div>
  );
}

// ── Shared edit helpers (issue #92) ─────────────────────────────────────────────

function RowActions({ onEdit, onDelete }: { onEdit: () => void; onDelete: () => void }) {
  return (
    <span style={sInner.rowActions}>
      <button type="button" style={sInner.actionBtn} onClick={onEdit}>Edit</button>
      <button type="button" style={sInner.actionBtnDanger} onClick={onDelete}>Delete</button>
    </span>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={sForm.field}>
      <span style={sForm.label}>{label}</span>
      {children}
    </label>
  );
}

function FormFooter({ saving, error, onSave, onCancel }: {
  saving: boolean; error: string | null; onSave: () => void; onCancel: () => void;
}) {
  return (
    <>
      {error && <span style={{ color: colors.error, fontSize: font.size.sm }}>{error}</span>}
      <div style={sForm.actions}>
        <button type="button" style={sForm.saveBtn} onClick={onSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
        <button type="button" style={sForm.cancelBtn} onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </>
  );
}

const EDU_COLS = "2fr 2fr 1fr 0.75fr 1.25fr 7.5rem";

function EducationTab({ education, setEducation }: { education: EducationRow[]; setEducation: (rows: EducationRow[]) => void }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (education.length === 0) {
    return (
      <p style={sInner.empty}>
        No education yet — ingest your resume or LinkedIn to add education.
        Your tailored resumes will omit the education section until then.
      </p>
    );
  }

  const onDelete = async (row: EducationRow) => {
    if (!window.confirm(`Delete "${row.degree} at ${row.institution}"? It won't return on a re-import.`)) return;
    const prev = education;
    setEducation(education.filter(e => e.id !== row.id));
    setError(null);
    try {
      await deleteEducation(row.id);
    } catch (err) {
      setEducation(prev);
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const onSaved = (updated: EducationRow) => {
    setEducation(education.map(e => (e.id === updated.id ? updated : e)));
    setEditingId(null);
  };

  const dates = (e: EducationRow) =>
    e.start && e.end ? `${e.start} – ${e.end}` : e.end || e.start || "—";

  return (
    <div style={sInner.table}>
      {error && <p style={{ ...sInner.empty, color: colors.error, marginBottom: "0.5rem" }}>{error}</p>}
      <div style={{ ...sInner.tableHead, gridTemplateColumns: EDU_COLS }}>
        <span>Institution</span><span>Degree</span><span>Location</span><span>GPA</span><span>Dates</span><span />
      </div>
      {education.map((e, i) =>
        editingId === e.id ? (
          <EducationEditForm key={e.id} row={e} onSaved={onSaved} onCancel={() => setEditingId(null)} />
        ) : (
          <div
            key={e.id}
            style={{ ...sInner.tableRow, gridTemplateColumns: EDU_COLS, background: i % 2 === 0 ? colors.surface : colors.boost }}
          >
            <span>{e.institution}</span>
            <span style={{ color: colors.textMuted }}>{e.degree}</span>
            <span style={{ color: colors.textMuted }}>{e.location || "—"}</span>
            <span style={{ color: colors.textMuted }}>{e.gpa || "—"}</span>
            <span style={{ color: colors.textMuted }}>{dates(e)}</span>
            <RowActions onEdit={() => setEditingId(e.id)} onDelete={() => onDelete(e)} />
          </div>
        )
      )}
    </div>
  );
}

function EducationEditForm({ row, onSaved, onCancel }: { row: EducationRow; onSaved: (r: EducationRow) => void; onCancel: () => void }) {
  const [institution, setInstitution] = useState(row.institution);
  const [degree, setDegree] = useState(row.degree === "—" ? "" : row.degree);
  const [location, setLocation] = useState(row.location);
  const [start, setStart] = useState(row.start);
  const [end, setEnd] = useState(row.end);
  const [gpa, setGpa] = useState(row.gpa);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateEducation(row.id, {
        institution, degree: degree.trim(),
        location: location.trim() || null,
        start_date: start.trim() || null,
        end_date: end.trim() || null,
        gpa: gpa.trim() || null,
      });
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      setSaving(false);
    }
  };

  return (
    <div style={sForm.root}>
      <div style={sForm.grid2}>
        <Field label="Institution"><input style={sForm.input} value={institution} onChange={e => setInstitution(e.target.value)} /></Field>
        <Field label="Degree"><input style={sForm.input} value={degree} onChange={e => setDegree(e.target.value)} /></Field>
        <Field label="Location"><input style={sForm.input} value={location} onChange={e => setLocation(e.target.value)} /></Field>
        <Field label="GPA"><input style={sForm.input} value={gpa} onChange={e => setGpa(e.target.value)} /></Field>
        <Field label="Start"><input style={sForm.input} value={start} onChange={e => setStart(e.target.value)} placeholder="e.g. Sep 2021" /></Field>
        <Field label="End"><input style={sForm.input} value={end} onChange={e => setEnd(e.target.value)} placeholder="e.g. Expected 2027" /></Field>
      </div>
      <FormFooter saving={saving} error={error} onSave={save} onCancel={onCancel} />
    </div>
  );
}

function AchievementsTab({ achievements }: { achievements: AchievementRow[] }) {
  if (achievements.length === 0) {
    return (
      <p style={sInner.empty}>
        No achievements yet — ingest your resume or LinkedIn to add achievements.
        Your tailored resumes will omit the achievements section until then.
      </p>
    );
  }
  const meta = (a: AchievementRow) =>
    [a.issuer, a.date].filter(Boolean).join(", ");
  return (
    <div style={sInner.table}>
      <div style={{ ...sInner.tableHead, gridTemplateColumns: "2fr 3fr" }}>
        <span>Achievement</span><span>Details</span>
      </div>
      {achievements.map((a, i) => (
        <div
          key={i}
          style={{
            ...sInner.tableRow,
            gridTemplateColumns: "2fr 3fr",
            background: i % 2 === 0 ? colors.surface : colors.boost,
          }}
        >
          <span>
            {a.title}
            {meta(a) && <span style={{ color: colors.textMuted }}> ({meta(a)})</span>}
          </span>
          <span style={{ color: colors.textMuted }}>{a.description || "—"}</span>
        </div>
      ))}
    </div>
  );
}

function ProjectsTab({ projects, setProjects }: { projects: ProjectRow[]; setProjects: (rows: ProjectRow[]) => void }) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (projects.length === 0) return <p style={sInner.empty}>No projects yet — ingest GitHub repos to add projects.</p>;

  const onDelete = async (row: ProjectRow) => {
    if (!window.confirm(`Delete "${row.name}"? It won't return on a re-import.`)) return;
    const prev = projects;
    setProjects(projects.filter(p => p.id !== row.id));
    setError(null);
    try {
      await deleteProject(row.id);
    } catch (err) {
      setProjects(prev);
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  };

  const onSaved = (updated: ProjectRow) => {
    setProjects(projects.map(p => (p.id === updated.id ? updated : p)));
    setEditingId(null);
  };

  return (
    <>
      {error && <p style={{ ...sInner.empty, color: colors.error, marginBottom: "0.5rem" }}>{error}</p>}
      <div style={sInner.projGrid}>
        {projects.map(p =>
          editingId === p.id ? (
            <ProjectEditForm key={p.id} row={p} onSaved={onSaved} onCancel={() => setEditingId(null)} />
          ) : (
            <div key={p.id} style={sInner.projCard}>
              <span style={sInner.projName}>{p.name}</span>
              {p.url !== "—" && (
                <a href={p.url} target="_blank" rel="noreferrer" style={sInner.projUrl}>{p.url}</a>
              )}
              {p.desc && <span style={sInner.projDesc}>{p.desc}</span>}
              <RowActions onEdit={() => setEditingId(p.id)} onDelete={() => onDelete(p)} />
            </div>
          )
        )}
      </div>
    </>
  );
}

function ProjectEditForm({ row, onSaved, onCancel }: { row: ProjectRow; onSaved: (r: ProjectRow) => void; onCancel: () => void }) {
  const [name, setName] = useState(row.name);
  const [description, setDescription] = useState(row.description);
  const [repoUrl, setRepoUrl] = useState(row.repo_url);
  const [demoUrl, setDemoUrl] = useState(row.demo_url);
  const [start, setStart] = useState(row.start);
  const [end, setEnd] = useState(row.end);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const updated = await updateProject(row.id, {
        name,
        description: description.trim() || null,
        repo_url: repoUrl.trim() || null,
        demo_url: demoUrl.trim() || null,
        start_date: start.trim() || null,
        end_date: end.trim() || null,
      });
      onSaved(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
      setSaving(false);
    }
  };

  return (
    <div style={{ ...sForm.root, gridColumn: "1 / -1" }}>
      <Field label="Name"><input style={sForm.input} value={name} onChange={e => setName(e.target.value)} /></Field>
      <Field label="Description"><textarea style={sForm.textarea} value={description} onChange={e => setDescription(e.target.value)} /></Field>
      <div style={sForm.grid2}>
        <Field label="Repo URL"><input style={sForm.input} value={repoUrl} onChange={e => setRepoUrl(e.target.value)} /></Field>
        <Field label="Demo URL"><input style={sForm.input} value={demoUrl} onChange={e => setDemoUrl(e.target.value)} /></Field>
        <Field label="Start"><input style={sForm.input} value={start} onChange={e => setStart(e.target.value)} /></Field>
        <Field label="End"><input style={sForm.input} value={end} onChange={e => setEnd(e.target.value)} /></Field>
      </div>
      <FormFooter saving={saving} error={error} onSave={save} onCancel={onCancel} />
    </div>
  );
}

function GraphTab({ graph }: { graph: GraphData }) {
  if (graph.top_skills.length === 0 && Object.keys(graph.by_category).length === 0) {
    return <p style={sInner.empty}>No graph data yet — ingest your resume to build the knowledge graph.</p>;
  }

  return (
    <div style={sInner.graphRoot}>
      <section style={sInner.graphSection}>
        <h3 style={sInner.graphHeading}>By Category</h3>
        <ul style={sInner.list}>
          {Object.entries(graph.by_category).sort((a, b) => b[1] - a[1]).map(([cat, count]) => (
            <li key={cat} style={sInner.listItem}>
              <span style={sInner.graphCat}>{cat}</span>
              <span style={sInner.graphCount}>{count}</span>
            </li>
          ))}
        </ul>
      </section>

      <section style={sInner.graphSection}>
        <h3 style={sInner.graphHeading}>Top Skills by Connections</h3>
        <ul style={sInner.list}>
          {graph.top_skills.map(sk => (
            <li key={sk.name} style={sInner.listItem}>
              <span style={sInner.graphSkill}>{sk.name}</span>
              <span style={sInner.graphCount}>{sk.connections} connections</span>
            </li>
          ))}
        </ul>
      </section>

      {Object.keys(graph.evidence).length > 0 && (
        <section style={sInner.graphSection}>
          <h3 style={sInner.graphHeading}>Evidence (top skills)</h3>
          <ul style={sInner.list}>
            {Object.entries(graph.evidence).map(([skill, sources]) => (
              <li key={skill} style={{ ...sInner.listItem, flexDirection: "column", alignItems: "flex-start", gap: "0.125rem" }}>
                <span style={sInner.graphSkill}>{skill}</span>
                <ul style={{ ...sInner.list, paddingLeft: "1rem", marginTop: 0 }}>
                  {sources.map((src, i) => (
                    <li key={i} style={{ ...sInner.listItem, color: colors.textMuted, fontSize: font.size.sm }}>→ {src}</li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

function ChartsTab({ skills, graph }: { skills: SkillRow[]; graph: GraphData | null }) {
  if (skills.length === 0) return <p style={sInner.empty}>No data yet — ingest your resume to see charts.</p>;

  // Skills by category count
  const byCategory: Record<string, number> = {};
  for (const sk of skills) {
    const cat = sk.category || "Uncategorized";
    byCategory[cat] = (byCategory[cat] ?? 0) + 1;
  }
  const catEntries = Object.entries(byCategory).sort((a, b) => b[1] - a[1]);
  const maxCat = Math.max(...catEntries.map(([, v]) => v), 1);

  // Top skills by confidence
  const topByConf = [...skills]
    .sort((a, b) => parseFloat(b.confidence) - parseFloat(a.confidence))
    .slice(0, 12);
  const maxConf = Math.max(...topByConf.map(s => parseFloat(s.confidence)), 1);

  // Top skills by connections (from graph)
  const topByConn = graph?.top_skills.slice(0, 10) ?? [];
  const maxConn = Math.max(...topByConn.map(s => s.connections), 1);

  return (
    <div style={sChart.root}>
      {/* Chart 1: Skills by category */}
      <section style={sChart.section}>
        <h3 style={sChart.heading}>Skills by Category</h3>
        <div style={sChart.bars}>
          {catEntries.map(([cat, count]) => (
            <div key={cat} style={sChart.barRow}>
              <span style={sChart.barLabel}>{cat}</span>
              <div style={sChart.barTrack}>
                <div style={{ ...sChart.barFill, width: `${(count / maxCat) * 100}%` }} />
              </div>
              <span style={sChart.barValue}>{count}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Chart 2: Top skills by confidence */}
      <section style={sChart.section}>
        <h3 style={sChart.heading}>Top Skills by Confidence</h3>
        <div style={sChart.bars}>
          {topByConf.map(sk => (
            <div key={sk.name} style={sChart.barRow}>
              <span style={sChart.barLabel}>{sk.name}</span>
              <div style={sChart.barTrack}>
                <div style={{ ...sChart.barFill, width: `${(parseFloat(sk.confidence) / maxConf) * 100}%`, background: colors.accent }} />
              </div>
              <span style={sChart.barValue}>{sk.confidence}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Chart 3: Top skills by knowledge graph connections */}
      {topByConn.length > 0 && (
        <section style={sChart.section}>
          <h3 style={sChart.heading}>Top Skills by Graph Connections</h3>
          <div style={sChart.bars}>
            {topByConn.map(sk => (
              <div key={sk.name} style={sChart.barRow}>
                <span style={sChart.barLabel}>{sk.name}</span>
                <div style={sChart.barTrack}>
                  <div style={{ ...sChart.barFill, width: `${(sk.connections / maxConn) * 100}%`, background: "#d29922" }} />
                </div>
                <span style={sChart.barValue}>{sk.connections}</span>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

const sChart: Record<string, CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: "2rem" },
  section: {},
  heading: { margin: "0 0 0.75rem", color: colors.accent, fontSize: font.size.base, fontWeight: 700 },
  bars: { display: "flex", flexDirection: "column", gap: "0.4rem" },
  barRow: { display: "grid", gridTemplateColumns: "18ch 1fr 4ch", alignItems: "center", gap: "0.625rem" },
  barLabel: { color: colors.text, fontSize: font.size.sm, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  barTrack: { height: "0.875rem", background: colors.primary, position: "relative" },
  barFill: { position: "absolute", top: 0, left: 0, height: "100%", background: colors.textMuted, transition: "width 0.3s ease" },
  barValue: { color: colors.textMuted, fontSize: font.size.sm, textAlign: "right" as const },
};

const s: Record<string, CSSProperties> = {
  panel: { display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" },
  tabStrip: {
    display: "flex",
    borderBottom: `1px solid ${colors.primary}`,
    background: colors.surface,
    flexShrink: 0,
    padding: "0 0.75rem",
    gap: "0.125rem",
  },
  tabBtn: {
    background: "transparent", border: "none", borderBottom: "2px solid transparent",
    color: colors.textMuted, fontSize: font.size.sm, padding: "0.5rem 0.75rem",
    cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  tabBtnActive: { color: colors.accent, borderBottomColor: colors.accent },
  content: { flex: 1, overflowY: "auto", padding: "1rem" },
  muted: { color: colors.textMuted, fontSize: font.size.sm },
};

const sInner: Record<string, CSSProperties> = {
  empty: { color: colors.textMuted, fontSize: font.size.sm, margin: 0 },
  skillsRoot: { display: "flex", flexDirection: "column", gap: "1.25rem" },
  pinHint: { color: colors.textMuted, fontSize: "0.7rem", margin: 0 },
  catBlock: {},
  catHeader: { fontWeight: 700, color: colors.accent, fontSize: font.size.sm, marginBottom: "0.5rem" },
  catCount: { color: colors.textMuted, fontWeight: 400 },
  skillGrid: { display: "flex", flexWrap: "wrap", gap: "0.5rem" },
  skillChip: {
    background: colors.surface, border: `1px solid ${colors.primary}`, position: "relative",
    padding: "0.375rem 1.25rem 0.375rem 0.625rem", display: "flex", flexDirection: "column",
    gap: "0.2rem", minWidth: "10ch",
  },
  skillChipPinned: { border: `1px solid ${colors.accent}` },
  pinBtn: {
    position: "absolute", top: "0.125rem", right: "0.25rem", background: "none", border: "none",
    color: colors.textMuted, cursor: "pointer", fontSize: "0.8rem", lineHeight: 1, padding: 0,
  },
  pinBtnActive: { color: colors.accent },
  skillName: { color: colors.text, fontSize: font.size.sm, fontWeight: 600 },
  skillMeta: { color: colors.textMuted, fontSize: "0.7rem" },
  skillSource: { color: colors.textMuted, fontSize: "0.7rem" },
  confBarBg: { height: "2px", background: colors.primary, width: "100%" },
  confBarFill: { height: "2px", background: colors.accent },
  table: { display: "flex", flexDirection: "column", gap: 0 },
  tableHead: {
    display: "grid", gridTemplateColumns: "2fr 1.5fr 1fr 1fr",
    padding: "0.375rem 0.75rem", fontWeight: 700,
    color: colors.textMuted, fontSize: font.size.sm,
    borderBottom: `1px solid ${colors.primary}`,
  },
  tableRow: {
    display: "grid", gridTemplateColumns: "2fr 1.5fr 1fr 1fr",
    padding: "0.375rem 0.75rem", fontSize: font.size.sm,
  },
  projGrid: { display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(28ch, 1fr))", gap: "0.75rem" },
  projCard: {
    display: "flex", flexDirection: "column", gap: "0.25rem",
    border: `1px solid ${colors.primary}`, padding: "0.75rem",
    background: colors.surface,
  },
  projName: { fontWeight: 700, color: colors.text, fontSize: font.size.sm },
  projUrl: { color: colors.accent, fontSize: "0.7rem", wordBreak: "break-all" },
  projDesc: { color: colors.textMuted, fontSize: font.size.sm },
  graphRoot: { display: "flex", flexDirection: "column", gap: "1.5rem" },
  graphSection: {},
  graphHeading: { margin: "0 0 0.5rem", color: colors.accent, fontSize: font.size.base, fontWeight: 700 },
  list: { listStyle: "none", padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "0.25rem" },
  listItem: { display: "flex", alignItems: "center", gap: "0.75rem", fontSize: font.size.sm, paddingLeft: "0.5rem", borderLeft: `1px solid ${colors.primary}` },
  graphCat: { color: colors.text, flex: 1 },
  graphSkill: { color: colors.accent, flex: 1 },
  graphCount: { color: colors.textMuted },
  expTitleCell: { display: "flex", alignItems: "center", gap: "0.4rem", flexWrap: "wrap" },
  incompleteBadge: {
    color: "#d29922", fontSize: "0.65rem", border: "1px solid #d29922",
    padding: "0 0.3rem", borderRadius: 0, whiteSpace: "nowrap",
  },
  rowActions: { display: "flex", gap: "0.375rem", justifyContent: "flex-end" },
  actionBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`, color: colors.textMuted,
    fontSize: "0.7rem", padding: "0.125rem 0.5rem", cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  actionBtnDanger: {
    background: "transparent", border: `1px solid ${colors.primary}`, color: colors.error,
    fontSize: "0.7rem", padding: "0.125rem 0.5rem", cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
};

const sForm: Record<string, CSSProperties> = {
  root: {
    display: "flex", flexDirection: "column", gap: "0.5rem",
    border: `1px solid ${colors.accent}`, padding: "0.75rem", background: colors.surface,
  },
  grid2: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.5rem" },
  field: { display: "flex", flexDirection: "column", gap: "0.2rem" },
  label: { color: colors.textMuted, fontSize: "0.7rem" },
  input: {
    background: colors.boost, border: `1px solid ${colors.primary}`, color: colors.text,
    fontSize: font.size.sm, padding: "0.3rem 0.4rem", fontFamily: "inherit", borderRadius: 0,
  },
  textarea: {
    background: colors.boost, border: `1px solid ${colors.primary}`, color: colors.text,
    fontSize: font.size.sm, padding: "0.3rem 0.4rem", fontFamily: "inherit", borderRadius: 0,
    minHeight: "3.5rem", resize: "vertical",
  },
  actions: { display: "flex", gap: "0.5rem", marginTop: "0.25rem" },
  saveBtn: {
    background: colors.accent, border: "none", color: colors.surface, fontWeight: 700,
    fontSize: font.size.sm, padding: "0.35rem 0.9rem", cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
  cancelBtn: {
    background: "transparent", border: `1px solid ${colors.primary}`, color: colors.textMuted,
    fontSize: font.size.sm, padding: "0.35rem 0.9rem", cursor: "pointer", fontFamily: "inherit", borderRadius: 0,
  },
};
